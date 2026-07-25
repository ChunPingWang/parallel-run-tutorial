#!/usr/bin/env python3
"""從 Pod 內部實測 TCP 可達性。argv: <標籤>=<ip>:<port> ...

輸出每行一筆 JSON：{"name":..., "target":..., "reachable":bool, "err":...}
不做任何重試，逾時一律 3 秒——「被 NetworkPolicy drop」的表現就是逾時。
"""
import json
import socket
import sys

for spec in sys.argv[1:]:
    name, _, addr = spec.partition("=")
    host, _, port = addr.rpartition(":")
    rec = {"name": name, "target": addr, "reachable": False, "err": ""}
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            rec["reachable"] = True
    except Exception as exc:                      # noqa: BLE001
        rec["err"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(rec, ensure_ascii=False))
