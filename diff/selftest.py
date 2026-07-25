#!/usr/bin/env python3
"""
Self-test for the diff engine.

Generates a synthetic .gor capture containing known noise (timestamps, trace
ids, hostname, array ordering) plus a small number of injected real defects,
then asserts that the engine suppresses the former and reports the latter.

Run:  python3 selftest.py
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import uuid as uuidlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gor_parser import PAYLOAD_SEPARATOR, load_exchanges  # noqa: E402
from normalizer import NoiseProfile  # noqa: E402
from diff_engine import build_report, compare_exchange  # noqa: E402


def _payload(ptype: str, uid: str, ts: int, latency: int, http: bytes) -> bytes:
    meta = f"{ptype} {uid} {ts} {latency}".encode()
    return meta + b"\n" + http


def _request(method: str, path: str) -> bytes:
    return (
        f"{method} {path} HTTP/1.1\r\n"
        "Host: legacy.bank.internal\r\n"
        "X-Shadow-Request: true\r\n"
        "Content-Type: application/json\r\n"
        "\r\n"
    ).encode()


def _response(status: int, body: dict) -> bytes:
    payload = json.dumps(body, ensure_ascii=False).encode()
    return (
        f"HTTP/1.1 {status} OK\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "\r\n"
    ).encode() + payload


def build_fixture(path: str, n: int = 200, defect_every: int = 50) -> int:
    """Returns the number of injected real defects."""
    random.seed(42)
    chunks = []
    defects = 0

    for i in range(n):
        uid = uuidlib.uuid4().hex[:16]
        ts = 1_753_400_000_000_000_000 + i * 1_000_000

        txns = [
            {"transactionId": f"T{j:04d}", "amount": 100 + j, "memo": "轉帳"}
            for j in range(3)
        ]

        legacy_body = {
            "code": "0000",
            "data": {
                "accountNo": "1234567890",
                "availableBalance": 15000.00,
                "transactions": txns,
            },
            # ---- pure noise ----
            "timestamp": "2026-07-25T09:15:03.221Z",
            "traceId": uuidlib.uuid4().hex,
            "hostname": "vm-legacy-01",
            "meta": {"timing": {"dbMs": 12}},
        }

        candidate_body = json.loads(json.dumps(legacy_body))
        candidate_body["timestamp"] = "2026-07-25T09:15:03.998Z"
        candidate_body["traceId"] = uuidlib.uuid4().hex
        candidate_body["hostname"] = "new-app-7d9f4b6c8-x2klp"   # Pod name
        candidate_body["meta"]["timing"]["dbMs"] = 31
        # unstable ordering - must be suppressed by sort_arrays
        candidate_body["data"]["transactions"] = list(
            reversed(candidate_body["data"]["transactions"])
        )

        legacy_status = candidate_status = 200

        # ---- inject a genuine behavioural defect ----
        if i % defect_every == 0:
            defects += 1
            candidate_body["data"]["availableBalance"] = 14999.00   # wrong money
            candidate_status = 500

        chunks.append(_payload("1", uid, ts, 0, _request("GET", "/api/v1/accounts/1234567890")))
        chunks.append(_payload("2", uid, ts, random.randint(20, 60) * 1_000_000,
                               _response(legacy_status, legacy_body)))
        chunks.append(_payload("3", uid, ts, random.randint(25, 90) * 1_000_000,
                               _response(candidate_status, candidate_body)))

    with open(path, "wb") as fh:
        fh.write(PAYLOAD_SEPARATOR.join(chunks))
    return defects


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="parallel-poc-")
    gor = os.path.join(tmp, "replay.gor")
    expected_defects = build_fixture(gor)

    profile = NoiseProfile.load(os.path.join(HERE, "noise-profile.yaml"))
    exchanges = load_exchanges(combined=gor)

    assert len(exchanges) == 200, f"expected 200 exchanges, got {len(exchanges)}"
    assert all(ex.complete for ex in exchanges.values()), "some exchanges are unpaired"

    results = [compare_exchange(ex, profile) for ex in exchanges.values()]
    report = build_report(results)

    divergent = report["summary"]["divergent"]
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("categories:", report["differences_by_category"])
    print("top paths:", [p["path"] for p in report["top_divergent_paths"][:5]])

    assert divergent == expected_defects, (
        f"noise suppression failed: expected {expected_defects} divergent, got {divergent}"
    )
    assert "$.status" in [p["path"] for p in report["top_divergent_paths"]], \
        "status mismatch not detected"
    assert any("availableBalance" in p["path"] for p in report["top_divergent_paths"]), \
        "balance defect not detected"

    # gate must fail at 99.9%
    from diff_engine import evaluate_gates
    gates = evaluate_gates(report, 99.9, 1.1, False)
    assert not gates[0]["passed"], "consistency gate should have failed"

    # CLI smoke test
    out_md = os.path.join(tmp, "report.md")
    rc = subprocess.call(
        [sys.executable, os.path.join(HERE, "diff_engine.py"), "compare",
         "--combined", gor, "--profile", os.path.join(HERE, "noise-profile.yaml"),
         "--out-md", out_md, "--out-json", os.path.join(tmp, "report.json"), "--quiet"]
    )
    assert rc == 1, f"CLI should exit 1 on gate failure, got {rc}"
    assert os.path.getsize(out_md) > 0, "markdown report is empty"

    print(f"\nOK - {expected_defects} real defects detected, "
          f"{200 - expected_defects} noisy responses correctly suppressed")
    print(f"artifacts: {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
