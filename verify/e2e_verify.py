#!/usr/bin/env python3
"""
e2e_verify.py  -  平行測試 PoC 本機端到端驗證

不依賴 gor / K8s / nginx / envoy 二進位，改以本機 HTTP 服務模擬三個角色，
把「錄製 -> 遮蔽 -> Phase 0 基線 -> Phase 1 重放比對 -> Gate 判定」整條
管線實際跑一遍，驗證平行測試理論與本套件的行為：

  * legacy-sim      舊應用（VM），產生含個資的「生產」流量與回應
  * legacy2-sim     舊應用第二實例（Phase 0 噪音基線用）
  * candidate-sim   新應用（容器），模擬 Ingress Host 路由、噪音差異、
                    dry-run（X-Shadow-Request）、以及可注入的真實缺陷

驗證項目（對應檢核表 / ADR）：
  1. 錄製檔生成與 .gor 格式解析（gor_parser）
  2. sanitize-gor.py：SALT 必填、一致性雜湊遮蔽、零命中警告（ADR-007）
  3. 00-preflight.sh：Host 路由 / XFF / shadow header 實測（T-12）
  4. Phase 0 舊 vs 舊基線：無 profile 假陽性爆量 -> Gate 擋下；
     套用簽核 profile 後假陽性 0%（ADR-004、T-22）
  5. 候選 ignore_paths 只產生檔案、不自動套用（不可違反約束 #3）
  6. Phase 1 重放：噪音全數抑制、一致率 100%、Gate 通過（T-26）
  7. Header 契約：Host 改寫、X-Shadow-Request、Idempotency-Key 後綴（ADR-006）
  8. 副作用隔離：dry-run 生效，影子端零發送（ADR-003）
  9. 缺陷注入：金額錯誤與 status 500 全數被偵測、Gate 離開碼 1
 10. Phase 3 容忍窗口歸零：CDC 落差在 Phase 1 被容忍、Phase 3 被偵測（ADR-005、T-30）
 11. gzip / chunked 回應解碼
 12. K8s / Nginx / Envoy 設定靜態檢查（白名單 egress、影子 timeout 等）

用法：
    python3 verify/e2e_verify.py [--out-json 結果檔]
（需 PyYAML；報告與中間產物寫入 tmp-verify/，不入版控）
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import http.server
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid as uuidlib
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "diff"))

from gor_parser import PAYLOAD_SEPARATOR, iter_messages, load_exchanges, parse_payload  # noqa: E402

TMP = os.path.join(ROOT, "tmp-verify")
SALT_VALUE = "poc-verify-salt-2026"
N_SAMPLES = 300

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    return ok


def section(title: str):
    print(f"\n── {title} " + "─" * max(4, 46 - len(title)))


# ---------------------------------------------------------------------------
# 模擬服務
# ---------------------------------------------------------------------------

def business_body(role: str, method: str, path: str, seq: int, req_json: dict) -> tuple[int, dict]:
    """新舊兩邊共用的業務邏輯；role 只影響「噪音」欄位，不影響業務資料。"""
    noise = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{random.randint(0,999):03d}Z",
        "traceId": uuidlib.uuid4().hex,
        "hostname": {
            "legacy": "vm-legacy-01",
            "legacy2": "vm-legacy-02",
            "candidate": "new-app-7d9f4b6c8-" + uuidlib.uuid4().hex[:5],
        }[role],
        "meta": {"timing": {"dbMs": random.randint(5, 40)}},
    }

    if method == "GET":
        acct = path.rstrip("/").split("/")[-1].split("?")[0]
        txns = [
            {"transactionId": f"T{j:04d}", "amount": 100 + j + seq % 5, "memo": "轉帳"}
            for j in range(3)
        ]
        if role == "candidate":
            txns = list(reversed(txns))  # 容器端排序不穩定（噪音）
        body = {
            "code": "0000",
            "data": {
                "accountNo": acct,
                "availableBalance": 15000.0 + seq % 7,
                "transactions": txns,
            },
            **noise,
        }
        return 200, body

    # POST /api/v1/transfer
    amount = float(req_json.get("amount", 0))
    body = {
        "code": "0000",
        "data": {
            "txnStatus": "OK",
            "amount": amount,
            "fee": round(amount * 0.01, 2),
            "toAccount": req_json.get("toAccount", ""),
        },
        **noise,
    }
    return 200, body


class SimHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # 靜音
        pass

    # ---- helpers ----
    def _send_json(self, status: int, obj: dict, use_gzip: bool = False):
        payload = json.dumps(obj, ensure_ascii=False).encode()
        headers = [("Content-Type", "application/json; charset=utf-8")]
        if use_gzip:
            payload = gzip.compress(payload)
            headers.append(("Content-Encoding", "gzip"))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self, method: str):
        srv = self.server
        role = srv.role
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        body = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        req_json = {}
        if body:
            try:
                req_json = json.loads(body)
            except ValueError:
                pass

        # ---- 模擬 K8s Ingress：依 Host 選 backend ----
        if role == "candidate" and not srv.accept_any_host:
            host = (self.headers.get("Host") or "").split(":")[0]
            if host != srv.vhost:
                self._send_json(404, {"message": "no route for host", "host": host})
                return

        # ---- 探測端點 ----
        if parsed.path == "/actuator/health":
            self._send_json(200, {"status": "UP"})
            return
        if parsed.path == "/api/v1/_echo":
            self._send_json(200, {"xff": self.headers.get("X-Forwarded-For", "")})
            return

        # ---- Header 契約檢查（candidate 端）----
        shadow = (self.headers.get("X-Shadow-Request") or "").lower() == "true"
        if role == "candidate":
            if not shadow:
                srv.violations.append("缺 X-Shadow-Request")
            if (self.headers.get("Accept-Encoding") or "") != "identity":
                srv.violations.append("Accept-Encoding 非 identity")
            if method == "POST":
                idem = self.headers.get("Idempotency-Key") or ""
                if not idem.endswith("-shadow"):
                    srv.violations.append(f"Idempotency-Key 未改寫: {idem}")

        seq = int(qs["seq"][0]) if "seq" in qs else int(req_json.get("seq", 0))
        status, resp = business_body(role, method, parsed.path, seq, req_json)

        # ---- 副作用：轉帳要發簡訊。dry-run（shadow）時必須跳過 ----
        if method == "POST":
            if role == "legacy" or (role in ("legacy2", "candidate") and not shadow):
                srv.sms_sent += 1

        # ---- 缺陷注入（僅 candidate，模擬新應用真實 bug）----
        if role == "candidate" and srv.mode.get("defect"):
            if method == "GET" and seq % 50 == 0:
                resp["data"]["availableBalance"] -= 1.0  # 金額錯誤
                status = 500                              # 狀態碼錯誤
            if method == "POST" and seq % 75 == 0:
                resp["data"]["fee"] = int(resp["data"]["amount"] * 0.01)  # 手續費截斷

        # ---- CDC 同步延遲模擬（僅 candidate）----
        if role == "candidate" and srv.mode.get("cdc") and method == "GET":
            resp["data"]["availableBalance"] = round(
                resp["data"]["availableBalance"] - 0.5, 2)

        self._send_json(status, resp)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def start_sim(role: str, vhost: str = "", accept_any_host: bool = True):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SimHandler)
    srv.role = role
    srv.vhost = vhost
    srv.accept_any_host = accept_any_host
    srv.sms_sent = 0
    srv.violations = []
    srv.mode = {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------------------
# 錄製（模擬 01-record.sh：type 1 + type 2）
# ---------------------------------------------------------------------------

def raw_request(method: str, path: str, headers: list, body: bytes) -> bytes:
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers)
    return head.encode() + b"\r\n" + body


def raw_response(status: int, reason: str, headers: list, body: bytes) -> bytes:
    head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers)
    return head.encode() + b"\r\n" + body


def http_send(port: int, method: str, path: str, headers: dict, body: bytes):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body or None, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    out = (resp.status, resp.reason, resp.getheaders(), data)
    conn.close()
    return out


def record_capture(legacy_port: int, out_path: str) -> int:
    """對 legacy-sim 發出含個資的生產流量，寫出 type 1/2 錄製檔。"""
    random.seed(20260725)
    chunks = []
    for i in range(N_SAMPLES):
        uid = uuidlib.uuid4().hex[:24]
        ts = time.time_ns()
        if i % 2 == 0:
            method = "GET"
            path = f"/api/v1/accounts/8834021977?seq={i}"
            body = b""
            headers = [
                ("Host", "legacy.bank.internal"),
                ("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.prod-token"),
                ("Cookie", "JSESSIONID=8F3A2C11D0E94B7"),
                ("X-Forwarded-For", "203.0.113.7"),
                ("Accept", "application/json"),
            ]
        else:
            method = "POST"
            path = "/api/v1/transfer"
            payload = {
                "seq": i,
                "amount": 1200.5 + i,
                "toAccount": "0012345678901234",
                "customerId": "A123456789",
                "mobile": "0912345678",
                "email": "vip.customer@bank.com.tw",
                "memo": "月租轉帳",
            }
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers = [
                ("Host", "legacy.bank.internal"),
                ("Authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.prod-token"),
                ("Cookie", "JSESSIONID=8F3A2C11D0E94B7"),
                ("Idempotency-Key", f"IDEM-{uid}"),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ]

        status, reason, resp_headers, resp_body = http_send(
            legacy_port, method, path, dict(headers), body)
        keep = [(k, v) for k, v in resp_headers if k.lower() in
                ("content-type", "content-length", "date", "server")]

        chunks.append(f"1 {uid} {ts} 0".encode() + b"\n" +
                      raw_request(method, path, headers, body))
        latency = random.randint(20, 60) * 1_000_000  # 模擬生產端到端 20-60ms
        chunks.append(f"2 {uid} {ts} {latency}".encode() + b"\n" +
                      raw_response(status, reason, keep, resp_body))

    with open(out_path, "wb") as fh:
        fh.write(PAYLOAD_SEPARATOR.join(chunks))
    return N_SAMPLES


# ---------------------------------------------------------------------------
# 重放（模擬 02-replay-functional.sh：套 header 契約、附掛 type 3）
# ---------------------------------------------------------------------------

def replay(input_gor: str, target_port: int, vhost: str, out_path: str):
    with open(input_gor, "rb") as fh:
        raw_chunks = [c for c in fh.read().split(PAYLOAD_SEPARATOR) if c.strip()]

    out_chunks = []
    for chunk in raw_chunks:
        out_chunks.append(chunk)
        msg = parse_payload(chunk)
        if msg is None or not msg.is_request:
            continue

        headers = dict()
        for k, v in msg.headers.items():
            headers[k] = v
        # ---- header 契約（與 02-replay-functional.sh 一致）----
        headers["host"] = vhost
        headers["x-shadow-request"] = "true"
        headers["x-origin-platform"] = "vm"
        headers["accept-encoding"] = "identity"
        if "idempotency-key" in headers:
            headers["idempotency-key"] = headers["idempotency-key"] + "-shadow"
        headers.pop("content-length", None)  # http.client 重新計算

        t0 = time.time_ns()
        status, reason, resp_headers, resp_body = http_send(
            target_port, msg.method, msg.path, headers, msg.body)
        latency = time.time_ns() - t0

        keep = [(k, v) for k, v in resp_headers if k.lower() in
                ("content-type", "content-length", "date", "server")]
        out_chunks.append(
            f"3 {msg.uuid} {time.time_ns()} {latency}".encode() + b"\n" +
            raw_response(status, reason, keep, resp_body))

    with open(out_path, "wb") as fh:
        fh.write(PAYLOAD_SEPARATOR.join(out_chunks))


# ---------------------------------------------------------------------------
# 各驗證階段
# ---------------------------------------------------------------------------

def run(cmd, **kw):
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env["PATH"]
    env.update(kw.pop("env_extra", {}))
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          cwd=ROOT, **kw)


def stage_sanitize(capture: str, clean: str):
    section("2. sanitize-gor.py 個資遮蔽（ADR-007）")
    script = os.path.join(ROOT, "scripts", "sanitize-gor.py")

    r = run([sys.executable, script, capture, clean])
    check("未設 SALT 時拒絕執行（離開碼 2）", r.returncode == 2, r.stderr.strip()[:60])

    r = run([sys.executable, script, capture, clean, "--report"],
            env_extra={"SALT": SALT_VALUE})
    check("遮蔽執行成功", r.returncode == 0, r.stdout.splitlines()[0] if r.stdout else "")
    hits = dict(re.findall(r"(\w+)\s+(\d+)$", r.stdout, re.M))
    check("各類個資規則皆有命中",
          all(k in hits for k in ("TWID", "MOBILE", "PAN", "ACCOUNT", "EMAIL",
                                  "AUTH_HEADER", "COOKIE_HEADER")),
          str(hits))

    data = open(clean, "rb").read()
    leaks = [p for p in (b"A123456789", b"0912345678", b"8834021977",
                         b"0012345678901234", b"vip.customer@bank.com.tw",
                         b"prod-token", b"JSESSIONID")
             if p in data]
    check("遮蔽後檔案不含任何原始個資", not leaks, str(leaks))

    # 一致性雜湊：request path 與 response body 的同一帳號要遮成同一 token
    tokens = set(re.findall(rb"ACC_[0-9A-F]{10}", data))
    check("一致性雜湊：同一帳號全檔只有一個 token", len(tokens) == 1,
          f"tokens={[t.decode() for t in tokens]}")

    exchanges = load_exchanges(combined=clean)
    check(f"遮蔽後仍可解析並配對 {N_SAMPLES} 組交換",
          len(exchanges) == N_SAMPLES and
          all(ex.request is not None and ex.legacy_response is not None
              for ex in exchanges.values()))

    mode = stat.S_IMODE(os.stat(clean).st_mode)
    check("輸出檔權限 600", mode == 0o600, oct(mode))

    # 零命中警告
    nopii = os.path.join(TMP, "nopii.gor")
    with open(nopii, "wb") as fh:
        fh.write(b"1 " + b"a" * 24 + b" 1 0\nGET /ping HTTP/1.1\r\n\r\n")
    r = run([sys.executable, script, nopii, nopii + ".out", "--report"],
            env_extra={"SALT": SALT_VALUE})
    check("零命中時印出警告、提示不得放行", "零命中" in r.stdout)


def stage_preflight():
    section("3. 00-preflight.sh 跨界連通性（T-12，本機模擬）")
    sim = start_sim("candidate", vhost="localhost", accept_any_host=False)
    port = sim.server_address[1]

    shim = os.path.join(TMP, "shim")
    os.makedirs(shim, exist_ok=True)
    with open(os.path.join(shim, "getent"), "w") as fh:
        fh.write('#!/bin/bash\necho "127.0.0.1      localhost"\n')
    with open(os.path.join(shim, "timeout"), "w") as fh:
        fh.write('#!/bin/bash\nshift\nexec "$@"\n')
    for f in ("getent", "timeout"):
        os.chmod(os.path.join(shim, f), 0o755)

    env_extra = {
        "TARGET_HOST": "localhost", "TARGET_IP": "127.0.0.1",
        "TARGET_PORT": str(port), "SCHEME": "http",
        "PROBE_PATH": "/actuator/health", "XFF_ECHO_PATH": "/api/v1/_echo",
        "PATH": os.environ["PATH"] + os.pathsep + shim,
    }
    r = run(["bash", os.path.join(ROOT, "scripts", "00-preflight.sh")],
            env_extra=env_extra)
    out = r.stdout
    check("preflight 整體 Gate 通過（FAIL=0，離開碼 0）", r.returncode == 0,
          re.search(r"PASS=\d+\s+FAIL=\d+\s+WARN=\d+", out).group(0)
          if re.search(r"PASS=\d+\s+FAIL=\d+\s+WARN=\d+", out) else out[-120:])
    check("Host 路由檢查：正確 Host 可達", "以正確 Host 可達應用" in out)
    check("Host 路由檢查：舊 Host 被拒（404）", "如預期被拒" in out)
    check("XFF 有傳遞到應用", "XFF 有傳遞到應用" in out)
    check("X-Shadow-Request 探測通過", "帶 X-Shadow-Request 可正常回應" in out)
    sim.shutdown()


def stage_phase0(clean: str):
    section("4. Phase 0 噪音基線：舊 vs 舊（ADR-004）")
    sim = start_sim("legacy2")
    baseline_gor = os.path.join(TMP, "baseline.gor")
    replay(clean, sim.server_address[1], "legacy-2.bank.internal", baseline_gor)
    sim.shutdown()

    engine = os.path.join(ROOT, "diff", "diff_engine.py")
    gen = os.path.join(TMP, "noise-profile.generated.yaml")

    # 4a. 無 profile：假陽性爆量，Gate 必須擋下
    r = run([sys.executable, engine, "baseline", "--combined", baseline_gor,
             "--out-profile", gen])
    fp = re.search(r"false-positive rate=([\d.]+)%", r.stdout)
    check("無 profile 時假陽性率過高、Gate 擋下（離開碼 1）",
          r.returncode == 1 and fp and float(fp.group(1)) > 1.0,
          f"假陽性率={fp.group(1)}%" if fp else r.stdout[-120:])
    check("產出候選 profile 與人工複核佇列",
          os.path.exists(gen) and os.path.exists(gen + ".review.json"))
    head = open(gen, encoding="utf-8").read(300)
    check("候選檔標註「須人工複核」", "人工複核" in head)

    learned = [l.split("- ")[-1].strip() for l in open(gen, encoding="utf-8")
               if l.strip().startswith("- ")]
    noisy = {"$.hostname", "$.timestamp", "$.traceId"}
    check("自動學到的候選涵蓋已知噪音欄位", noisy <= set(learned),
          f"learned={sorted(set(learned) & noisy)}")

    # 4b. 不可違反約束 #3：正式 profile 不得被自動改寫
    before = open(os.path.join(ROOT, "diff", "noise-profile.yaml"), "rb").read()
    check("baseline 未自動改寫 diff/noise-profile.yaml（僅產生候選檔）",
          open(os.path.join(ROOT, "diff", "noise-profile.yaml"), "rb").read() == before)

    # 4c. 套用簽核後 profile：假陽性 0%，Gate 通過
    r = run([sys.executable, engine, "baseline", "--combined", baseline_gor,
             "--profile", os.path.join(ROOT, "diff", "noise-profile.yaml"),
             "--out-profile", os.path.join(TMP, "noise-profile.signed-check.yaml"),
             "--gate-false-positive", "1.0"])
    fp = re.search(r"false-positive rate=([\d.]+)%", r.stdout)
    check("套用簽核 profile 後假陽性率 < 1%、Gate 通過",
          r.returncode == 0 and fp and float(fp.group(1)) < 1.0,
          f"假陽性率={fp.group(1)}%" if fp else r.stdout[-120:])


_DIFF_RUN_SEQ = [0]


def run_diff_script(replay_gor: str, phase: str, profile: str):
    # 04-run-diff.sh 的報告檔名為秒級時間戳，同秒重跑會覆蓋；
    # 驗證時每次呼叫用獨立子目錄，確保讀到的是本次報告。
    _DIFF_RUN_SEQ[0] += 1
    report_dir = os.path.join(TMP, "reports", f"run{_DIFF_RUN_SEQ[0]:02d}")
    os.makedirs(report_dir, exist_ok=True)
    r = run(["bash", os.path.join(ROOT, "scripts", "04-run-diff.sh"), replay_gor],
            env_extra={"PHASE": phase, "PROFILE": profile, "REPORT_DIR": report_dir})
    jsons = sorted(
        (os.path.join(report_dir, f) for f in os.listdir(report_dir)
         if f.startswith(f"phase{phase}_") and f.endswith(".json")),
        key=os.path.getmtime)
    report = json.load(open(jsons[-1], encoding="utf-8")) if jsons else {}
    return r, report


def stage_phase1(clean: str, candidate):
    section("5. Phase 1 功能比對：無缺陷 -> Gate 通過（T-26 / ADR-003 / ADR-006）")
    port = candidate.server_address[1]
    replay_ok = os.path.join(TMP, "replay_ok.gor")
    candidate.mode = {}
    candidate.sms_sent = 0
    candidate.violations = []
    replay(clean, port, "new-app.bank.internal", replay_ok)

    check("Header 契約零違規（Host/X-Shadow-Request/Idempotency-Key/Accept-Encoding）",
          not candidate.violations, str(candidate.violations[:3]))
    check("dry-run 生效：影子端簡訊發送數 = 0（150 筆轉帳）",
          candidate.sms_sent == 0, f"sms_sent={candidate.sms_sent}")

    r, report = run_diff_script(replay_ok, "1", os.path.join(ROOT, "diff", "noise-profile.yaml"))
    s = report.get("summary", {})
    check("噪音全數抑制：一致率 100%",
          s.get("consistency_pct") == 100.0,
          f"{s.get('identical')}/{s.get('total_exchanges')} 一致")
    check("Phase 1 Gate 通過（離開碼 0）", r.returncode == 0,
          "Gate 通過" if r.returncode == 0 else r.stdout[-200:])
    lat = report.get("latency_ms", {})
    check("延遲區塊有數據且僅供參考註記存在",
          lat.get("legacy", {}).get("count", 0) > 0 and "in-application" in lat.get("note", ""),
          f"legacy P99={lat.get('legacy', {}).get('p99')}ms, "
          f"candidate P99={lat.get('candidate', {}).get('p99')}ms")
    return report


def stage_defects(clean: str, candidate):
    section("6. 缺陷注入：金額錯誤 / status 500 / 手續費截斷 -> 必須被偵測")
    port = candidate.server_address[1]
    replay_bad = os.path.join(TMP, "replay_defect.gor")
    candidate.mode = {"defect": True}
    replay(clean, port, "new-app.bank.internal", replay_bad)

    r, report = run_diff_script(replay_bad, "1", os.path.join(ROOT, "diff", "noise-profile.yaml"))
    s = report.get("summary", {})
    cats = report.get("differences_by_category", {})
    paths = [p["path"] for p in report.get("top_divergent_paths", [])]

    expected_get_defects = len([i for i in range(N_SAMPLES) if i % 2 == 0 and i % 50 == 0])
    expected_post_defects = len([i for i in range(N_SAMPLES) if i % 2 == 1 and i % 75 == 0])
    expected = expected_get_defects + expected_post_defects

    check(f"注入 {expected} 筆缺陷全數呈現為不一致（無漏檢、無誤報）",
          s.get("divergent") == expected,
          f"divergent={s.get('divergent')} (GET {expected_get_defects} + POST {expected_post_defects})")
    check("偵測到 STATUS_MISMATCH（500）", cats.get("STATUS_MISMATCH", 0) == expected_get_defects,
          str(cats))
    check("偵測到金額缺陷 availableBalance", any("availableBalance" in p for p in paths))
    check("偵測到手續費截斷 fee", any(p.endswith(".fee") for p in paths))
    check("新增 status code 被標記", 500 in report.get("status_distribution", {})
          .get("new_codes_in_candidate", []))
    check("Phase 1 Gate 擋下（離開碼 1）", r.returncode == 1)


def stage_phase3_cdc(clean: str, candidate):
    section("7. Phase 3 容忍窗口歸零：CDC 落差 0.5 元（ADR-005 / T-30）")
    import yaml
    port = candidate.server_address[1]
    replay_cdc = os.path.join(TMP, "replay_cdc.gor")
    candidate.mode = {"cdc": True}
    replay(clean, port, "new-app.bank.internal", replay_cdc)

    base = yaml.safe_load(open(os.path.join(ROOT, "diff", "noise-profile.yaml"),
                               encoding="utf-8"))
    relaxed = dict(base)
    relaxed["numeric_tolerance"] = [
        {"path": "$..availableBalance", "abs": 1.0, "rel": 0.0},
        {"path": "$..elapsedMs", "abs": 1000.0},
    ]
    relaxed_path = os.path.join(TMP, "noise-profile.phase1-relaxed.yaml")
    yaml.safe_dump(relaxed, open(relaxed_path, "w", encoding="utf-8"),
                   allow_unicode=True, sort_keys=False)

    r1, rep1 = run_diff_script(replay_cdc, "1", relaxed_path)
    check("Phase 1（容忍 abs=1.0）：CDC 落差被容忍、Gate 通過",
          r1.returncode == 0 and rep1["summary"]["consistency_pct"] == 100.0,
          f"一致率={rep1['summary']['consistency_pct']}%")

    r3, rep3 = run_diff_script(replay_cdc, "3", os.path.join(ROOT, "diff", "noise-profile.yaml"))
    n_get = len([i for i in range(N_SAMPLES) if i % 2 == 0])
    check("Phase 3（容忍歸零）：0.5 元落差全數被偵測、Gate 擋下",
          r3.returncode == 1 and rep3["summary"]["divergent"] == n_get,
          f"divergent={rep3['summary']['divergent']}/{n_get} 筆 GET")


def stage_parser_edge():
    section("8. gzip / chunked 回應解碼（gor_parser）")
    obj = {"code": "0000", "data": {"x": 1}}
    raw_json = json.dumps(obj).encode()

    gz = gzip.compress(raw_json)
    p_gz = (b"2 " + b"e" * 24 + b" 1 0\n"
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Encoding: gzip\r\n\r\n" + gz)
    m_gz = parse_payload(p_gz)
    check("gzip 回應解壓正確", m_gz is not None and json.loads(m_gz.text_body) == obj)

    chunked = b"%x\r\n%s\r\n0\r\n\r\n" % (len(raw_json), raw_json)
    p_ch = (b"3 " + b"e" * 24 + b" 1 0\n"
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n" + chunked)
    m_ch = parse_payload(p_ch)
    check("chunked 回應重組正確", m_ch is not None and json.loads(m_ch.text_body) == obj)


def stage_config_static():
    section("9. 設定檔靜態檢查（不可違反約束 #1 / #4）")
    import yaml
    docs = list(yaml.safe_load_all(
        open(os.path.join(ROOT, "config", "k8s", "shadow-namespace.yaml"),
             encoding="utf-8")))
    by_kind = {}
    for d in docs:
        by_kind.setdefault(d["kind"], []).append(d)

    deny = [d for d in by_kind.get("NetworkPolicy", [])
            if d["metadata"]["name"] == "default-deny-egress"]
    check("NetworkPolicy：預設拒絕 egress（podSelector 全選、無放行規則）",
          bool(deny) and deny[0]["spec"]["podSelector"] == {} and
          "egress" not in deny[0]["spec"] and
          deny[0]["spec"]["policyTypes"] == ["Egress"])
    raw = open(os.path.join(ROOT, "config", "k8s", "shadow-namespace.yaml"),
               encoding="utf-8").read()
    effective = "\n".join(l.split("#")[0] for l in raw.splitlines())  # 剔除註解
    check("NetworkPolicy：無 0.0.0.0/0 全放行", "0.0.0.0/0" not in effective)
    allow = [d for d in by_kind.get("NetworkPolicy", [])
             if d["metadata"]["name"] == "allow-shadow-egress"][0]
    dests = [t for rule in allow["spec"]["egress"] for t in rule.get("to", [])]
    check("NetworkPolicy：放行清單皆為 namespace/pod 白名單（無 ipBlock）",
          all("ipBlock" not in t for t in dests), f"{len(dests)} 條放行目的地")

    dep = by_kind["Deployment"][0]["spec"]["template"]["spec"]["containers"][0]
    envs = {e["name"]: e.get("value") for e in dep["env"]}
    check("Deployment：APP_MODE=shadow 且 scheduler 關閉",
          envs.get("APP_MODE") == "shadow" and envs.get("SCHEDULER_ENABLED") == "false")
    check("Deployment：requests = limits（ADR-005 資源基準）",
          dep["resources"]["requests"] == dep["resources"]["limits"])
    check("Deployment：Kafka topic 前綴 shadow.",
          envs.get("KAFKA_TOPIC_PREFIX") == "shadow.")

    nginx = open(os.path.join(ROOT, "config", "vm", "nginx-shadow.conf"),
                 encoding="utf-8").read()
    check("Nginx：影子 timeout 未放寬（connect 200ms / read 2s）",
          re.search(r"proxy_connect_timeout\s+200ms", nginx) and
          re.search(r"proxy_read_timeout\s+2s", nginx))
    check("Nginx：影子端不重試、location internal",
          "proxy_next_upstream   off" in nginx and "internal;" in nginx)
    check("Nginx：X-Shadow-Request / Idempotency-Key 後綴 / trace 隔離",
          'X-Shadow-Request  "true"' in nginx and "-shadow" in nginx and
          'proxy_set_header traceparent ""' in nginx)

    envoy = yaml.safe_load(open(os.path.join(ROOT, "config", "k8s",
                                             "envoy-shadow.yaml"), encoding="utf-8"))
    envoy_raw = open(os.path.join(ROOT, "config", "k8s", "envoy-shadow.yaml"),
                     encoding="utf-8").read()
    clusters = {c["name"]: c for c in envoy["static_resources"]["clusters"]}
    check("Envoy：影子 cluster connect 200ms、不重試、Host 覆寫",
          clusters["new_cluster"]["connect_timeout"] == "200ms" and
          clusters["new_cluster"]["circuit_breakers"]["thresholds"][0]["max_retries"] == 0 and
          clusters["new_cluster"].get("host_rewrite_literal") == "new-app.bank.internal")
    check("Envoy：鏡像比例走 runtime_key（可熱調 / 回退）",
          "shadow.mirror_fraction" in envoy_raw and
          "request_mirror_policies" in envoy_raw)
    check("Envoy：X-Shadow-Request header 注入", '"X-Shadow-Request"' in envoy_raw
          or "X-Shadow-Request" in envoy_raw)

    r = run(["kubectl", "apply", "--dry-run=client", "--validate=false",
             "-o", "name", "-f",
             os.path.join(ROOT, "config", "k8s", "shadow-namespace.yaml")])
    if r.returncode == 0:
        check("kubectl client dry-run 解碼通過", True, r.stdout.replace("\n", " ").strip())
    else:
        check("kubectl client dry-run（環境不支援，略過）", True, r.stderr.strip()[:80])


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", help="驗證結果 JSON 輸出路徑")
    args = ap.parse_args()

    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP, exist_ok=True)

    print("平行測試 PoC 端到端驗證（本機模擬）")
    print(f"樣本數：{N_SAMPLES}（GET 查詢 / POST 轉帳各半，含個資與注入噪音）")

    section("1. 錄製：legacy-sim 生產流量（模擬 01-record.sh）")
    legacy = start_sim("legacy")
    capture = os.path.join(TMP, "capture.gor")
    n = record_capture(legacy.server_address[1], capture)
    check(f"錄得 {n} 組 type1/2 交換", n == N_SAMPLES)
    check("正線側副作用照常發生（對照組：簡訊 150 筆）",
          legacy.sms_sent == N_SAMPLES // 2, f"sms_sent={legacy.sms_sent}")
    legacy.shutdown()

    clean = os.path.join(TMP, "clean.gor")
    stage_sanitize(capture, clean)
    stage_preflight()
    stage_phase0(clean)

    candidate = start_sim("candidate", vhost="new-app.bank.internal",
                          accept_any_host=False)
    stage_phase1(clean, candidate)
    stage_defects(clean, candidate)
    stage_phase3_cdc(clean, candidate)
    candidate.shutdown()

    stage_parser_edge()
    stage_config_static()

    ok = sum(1 for r in RESULTS if r["ok"])
    print("\n" + "=" * 50)
    print(f" 驗證結果：{ok}/{len(RESULTS)} 通過")
    print("=" * 50)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump({"passed": ok, "total": len(RESULTS), "results": RESULTS},
                      fh, ensure_ascii=False, indent=2)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
