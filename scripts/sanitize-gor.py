#!/usr/bin/env python3
"""
sanitize-gor.py  -  錄製檔個資遮蔽

錄製檔含完整 request/response body。在銀行環境，這些檔案離開正線主機前
必須先遮蔽個資，否則等同把生產資料複製到測試環境。

重點：遮蔽採「一致性雜湊」而非隨機值 —— 同一個帳號在新舊兩邊會被遮成
同一個字串，diff 才不會因為遮蔽本身產生假陽性。

用法：
    python3 sanitize-gor.py in.gor out.gor
    SALT=$(cat /etc/shadow-poc/salt) python3 sanitize-gor.py in.gor out.gor --report

⚠ SALT 必須保密且固定；換 SALT 會讓新舊錄製檔無法比對。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "diff"))
from gor_parser import PAYLOAD_SEPARATOR  # noqa: E402

SALT = os.environ.get("SALT", "")


def _token(value: str, prefix: str, length: int = 10) -> str:
    digest = hashlib.sha256((SALT + value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:length].upper()}"


# 依台灣金融場景常見格式。上線前務必依實際資料再校準一次。
RULES = [
    # 身分證字號
    ("TWID", re.compile(rb"\b[A-Z][12]\d{8}\b"),
     lambda m: _token(m.group(0).decode(), "ID_").encode()),
    # 手機
    ("MOBILE", re.compile(rb"\b09\d{8}\b"),
     lambda m: _token(m.group(0).decode(), "MOB_").encode()),
    # 信用卡（13-19 碼，含分隔）
    ("PAN", re.compile(rb"\b(?:\d[ -]?){12,18}\d\b"),
     lambda m: _token(m.group(0).decode(), "PAN_", 12).encode()),
    # 帳號（10-16 碼純數字）— 放在 PAN 之後，避免搶匹配
    ("ACCOUNT", re.compile(rb"\b\d{10,16}\b"),
     lambda m: _token(m.group(0).decode(), "ACC_").encode()),
    # Email
    ("EMAIL", re.compile(rb"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
     lambda m: _token(m.group(0).decode(), "MAIL_").encode() + b"@masked.local"),
    # Authorization / Cookie header 值整段抹除
    ("AUTH_HEADER", re.compile(rb"(?i)(authorization:\s*)([^\r\n]+)"),
     lambda m: m.group(1) + b"<<REDACTED>>"),
    ("COOKIE_HEADER", re.compile(rb"(?i)(cookie:\s*)([^\r\n]+)"),
     lambda m: m.group(1) + b"<<REDACTED>>"),
]


def sanitize(payload: bytes, stats: Counter) -> bytes:
    """遮蔽 body 與敏感 header，但保留 meta 行（uuid 是配對用的）。"""
    nl = payload.find(b"\n")
    if nl == -1:
        return payload
    meta, rest = payload[:nl + 1], payload[nl + 1:]

    for name, pattern, repl in RULES:
        rest, count = pattern.subn(repl, rest)
        if count:
            stats[name] += count
    return meta + rest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--report", action="store_true", help="印出各規則命中次數")
    args = ap.parse_args()

    if not SALT:
        print("error: 環境變數 SALT 未設定。固定 SALT 才能保證新舊錄製檔遮蔽一致。",
              file=sys.stderr)
        return 2

    with open(args.input, "rb") as fh:
        data = fh.read()

    stats: Counter = Counter()
    chunks = [sanitize(c, stats) for c in data.split(PAYLOAD_SEPARATOR)]

    with open(args.output, "wb") as fh:
        fh.write(PAYLOAD_SEPARATOR.join(chunks))
    os.chmod(args.output, 0o600)

    print(f"{args.input} -> {args.output}  ({len(chunks)} payloads)")
    if args.report:
        for name, count in stats.most_common():
            print(f"  {name:<14} {count}")
        if not stats:
            print("  ⚠ 零命中 —— 請確認規則是否符合實際資料格式，勿直接放行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
