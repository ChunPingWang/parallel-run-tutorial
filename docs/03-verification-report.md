# 平行測試 PoC 套件驗證報告

**日期** 2026-07-25
**環境** macOS（Darwin 25.4.0）、Python 3.14 + PyYAML 6.0.3、kubectl（無叢集）、Node 26
**驗證方式** 靜態檢查 + `diff/selftest.py` + `verify/e2e_verify.py` 本機端到端模擬

---

## 1. 結論

**49 / 49 項端到端檢查全數通過，selftest 通過，平行測試理論在本機模擬環境獲得驗證。**

本套件宣稱的核心主張逐一實測成立：

| 主張 | 驗證結果 |
|---|---|
| 噪音（時間戳、traceId、hostname、陣列順序）可被 profile 全數抑制 | ✅ 300 筆樣本一致率 100% |
| 真實缺陷（金額錯誤、status 500、手續費截斷）不會被噪音抑制淹沒 | ✅ 注入 8 筆全數偵測，無漏檢、無誤報 |
| Phase 0「舊 vs 舊」能客觀界定噪音 | ✅ 無 profile 假陽性 100% → Gate 擋下；簽核 profile 後 0% |
| 一致性雜湊遮蔽不產生假陽性 | ✅ 同一帳號在 request path 與 response body 遮成同一 token |
| dry-run（`X-Shadow-Request`）能隔離副作用 | ✅ 正線側 150 筆簡訊照發、影子側 0 筆 |
| Gate 未過離開碼為 1，可供 CI 擋關 | ✅ Phase 1 / Phase 3 / baseline 三處皆驗證 |
| Phase 3 容忍歸零能抓出 CDC 級微小落差 | ✅ 0.5 元落差在 Phase 1（容忍 1.0）被容忍、Phase 3 全數偵測 |

---

## 2. 驗證方法

本機無 `gor`、`nginx`、`envoy` 與 K8s 叢集，故 `verify/e2e_verify.py` 以三個真實
HTTP 服務模擬拓撲，**重放與比對管線本身（sanitize、gor_parser、normalizer、
diff_engine、00/04 腳本）全部是實際執行**，僅流量搬運層（gor 二進位）以等價的
Python 重放器代替（套用與 `02-replay-functional.sh` 相同的 header 契約）：

```
legacy-sim（VM 舊應用）      ── 錄製 300 筆含個資生產流量 → capture.gor
        │ sanitize-gor.py（SALT 一致性雜湊）
        ▼
clean.gor ──┬─ 重放 → legacy2-sim（第二實例）  → Phase 0 噪音基線
            └─ 重放 → candidate-sim（容器新應用，模擬 Ingress Host 路由、
                       dry-run、噪音差異、可注入缺陷）→ Phase 1 / 3 比對
```

樣本組成：GET 帳戶查詢 / POST 轉帳各 150 筆；request 含身分證、手機、卡號、
帳號、Email、Authorization、Cookie 等個資；candidate 回應注入時間戳 / traceId /
Pod hostname / 陣列反序 / `meta.timing` 等已知噪音。

---

## 3. 分項結果

### 3.1 靜態檢查

| 項目 | 結果 |
|---|---|
| `bash -n scripts/*.sh`（5 支腳本） | ✅ |
| YAML 解析：`noise-profile.yaml`、`envoy-shadow.yaml`、`shadow-namespace.yaml`（5 份文件） | ✅ |
| `py_compile`：diff/ 四支模組 + `sanitize-gor.py` | ✅ |
| `diff/selftest.py` | ✅ 4 筆缺陷全偵測、196 筆噪音全抑制、CLI 離開碼 1 |

### 3.2 個資遮蔽（ADR-007，檢核表 T-18）

- 未設 `SALT` 拒絕執行（離開碼 2）✅
- 七類規則（TWID / MOBILE / PAN / ACCOUNT / EMAIL / AUTH / COOKIE）全數命中 ✅
- 遮蔽後檔案以原始個資字串全文搜尋 → 零殘留 ✅
- 同一帳號全檔僅一個 token（`ACC_F3EC0F04FB`），路徑與 body 一致 ✅
- 遮蔽後 300 組交換仍可解析配對、輸出檔權限 600 ✅
- 零命中時印出「不得放行」警告 ✅

### 3.3 跨界連通性（`00-preflight.sh`，T-12）

以 candidate-sim 模擬 Ingress 實測：整體 Gate 通過（PASS=6 FAIL=0），
其中 Host 路由（正確 Host 200 / 舊 Host 404）、XFF 傳遞、`X-Shadow-Request`
探測皆如預期。MTU（`ping -M`）與 `getent` 為 Linux 專用，於 macOS 以 shim
代替 —— 此腳本目標環境本為 legacy Linux VM。

### 3.4 Phase 0 噪音基線（ADR-004，T-19～T-22）

- 無 profile：假陽性率 100%，baseline Gate 擋下（離開碼 1）✅
- 自動學習正確產出候選 `$.hostname` / `$.timestamp` / `$.traceId`，
  並附 `.review.json` 人工複核佇列，檔頭標註「須人工複核」✅
- **`diff/noise-profile.yaml` 未被自動改寫**（不可違反約束 #3）✅
- 套用簽核 profile 重跑：假陽性率 0.000%，Gate 通過 ✅

### 3.5 Phase 1 功能比對（T-26，ADR-003 / ADR-006）

- Header 契約零違規：Host 改寫、`X-Shadow-Request: true`、
  `Accept-Encoding: identity`、`Idempotency-Key` 加 `-shadow` 後綴 ✅
- dry-run 生效：影子端 150 筆轉帳零簡訊發送（正線對照組 150 筆照發）✅
- 噪音全數抑制：一致率 100%，`04-run-diff.sh` 離開碼 0 ✅

### 3.6 缺陷偵測

注入 8 筆真實缺陷（GET 金額 -1 且回 500 ×6、POST 手續費截斷 ×2）：

- divergent = 8，恰等於注入數（無漏檢、無誤報）✅
- 分類正確：`STATUS_MISMATCH` ×6、`availableBalance` 與 `fee` 路徑上榜 ✅
- 候選端新增 status code（500）被標記 ✅
- Phase 1 Gate 擋下，離開碼 1 ✅

### 3.7 Phase 3 容忍歸零（ADR-005，T-30）

模擬 CDC 同步落差（餘額 -0.5）：

- Phase 1 + `abs: 1.0` 容忍 → 一致率 100%，Gate 通過（合理容忍）✅
- Phase 3 + 容忍歸零 → 150 筆 GET 全數偵測，Gate 擋下 ✅

### 3.8 解析器邊界

gzip 回應解壓、chunked 回應重組皆正確 ✅

### 3.9 設定檔（不可違反約束 #1 / #4）

- NetworkPolicy：預設拒絕 egress、放行清單皆 namespace/pod 白名單、
  無 `ipBlock`、無 `0.0.0.0/0` ✅
- Deployment：`APP_MODE=shadow`、scheduler 關閉、requests = limits、
  Kafka `shadow.` 前綴 ✅
- Nginx：影子 timeout 維持 connect 200ms / read 2s、不重試、
  location internal、trace 隔離 ✅
- Envoy：影子 cluster connect 200ms、`max_retries: 0`、Host 覆寫、
  `runtime_key` 熱調 ✅

### 3.10 簡報腳本

`docs/build-deck.js` 以 pptxgenjs 實際執行成功，產出檔與
`dist/poc-exec-deck.pptx` 位元組數完全一致（346,266 bytes），產物可重現。

---

## 4. 未能在本機實測的項目（上線前仍須驗證）

| 項目 | 原因 | 對應檢核 |
|---|---|---|
| `gor` 錄製 / 重放本體（`01-record.sh`、`02/03-replay-*.sh`） | 本機無 gor 二進位；僅通過語法檢查，flag 需依實際 gor 版本驗證 | T-10、T-11 |
| NetworkPolicy 實際生效 | 無叢集；kubectl client dry-run 無法連線，僅做 YAML 靜態檢查 | **T-14（Gate 阻擋項）** |
| Nginx / Envoy 設定載入 | 本機無二進位；`nginx -t` / `envoy --mode validate` 未執行 | R7 |
| CDC 實際延遲分布、gor CPU 佔用、正線 P99 增幅 | 需真實環境 | T-11、T-21、T-24 |

## 5. 驗證過程中的觀察

1. `04-run-diff.sh` 報告檔名為秒級時間戳（`phase1_%Y%m%d-%H%M%S`），同一秒內
   重跑會互相覆蓋。實務上單次比對耗時遠超過一秒，風險低；自動化批次呼叫時
   建議自行指定 `REPORT_DIR`（`verify/e2e_verify.py` 即如此處理）。
2. `sanitize-gor.py` 的 PAN 規則（13–19 碼）會先於 ACCOUNT 規則吃掉 16 碼帳號，
   遮蔽前綴為 `PAN_` —— 行為一致故不影響比對，但報表命中統計歸類會偏向 PAN，
   校準規則時（CLAUDE.md 已知限制）需留意。
3. 套件需 PyYAML；本機 Python 3.9 / 3.14 皆未內建，README 依賴一節已列明，
   驗證時以 venv 安裝。

## 6. 重跑方式

```bash
python3 -m venv .venv && .venv/bin/pip install pyyaml
.venv/bin/python verify/e2e_verify.py --out-json tmp-verify/results.json
cd diff && ../.venv/bin/python selftest.py
```
