"""
GoReplay (.gor) capture file parser.

File format
-----------
Payloads are separated by the byte sequence  b"\\n\\xf0\\x9f\\x90\\xb5\\xf0\\x9f\\x99\\x88\\xf0\\x9f\\x99\\x89\\n"
(i.e. "\\n" + U+1F435 U+1F648 U+1F649 + "\\n").

Each payload is::

    <meta line>\\n<raw HTTP bytes>

The meta line is space separated::

    <type> <uuid> <timestamp_ns> [<latency_ns>]

type  1 = captured request
      2 = original (upstream) response
      3 = replayed response
      4 = replayed response latency marker (rare)

Requests and their responses share the same uuid, which is what lets us pair
legacy and candidate responses for diffing.
"""

from __future__ import annotations

import gzip
import io
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

PAYLOAD_SEPARATOR = b"\n\xf0\x9f\x90\xb5\xf0\x9f\x99\x88\xf0\x9f\x99\x89\n"

TYPE_REQUEST = "1"
TYPE_ORIGINAL_RESPONSE = "2"
TYPE_REPLAYED_RESPONSE = "3"


@dataclass
class HttpMessage:
    """A decoded HTTP request or response captured by GoReplay."""

    payload_type: str
    uuid: str
    timestamp_ns: int
    latency_ns: int
    # request-only
    method: Optional[str] = None
    path: Optional[str] = None
    # response-only
    status_code: Optional[int] = None
    reason: Optional[str] = None
    # common
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    raw: bytes = b""

    @property
    def latency_ms(self) -> float:
        return self.latency_ns / 1_000_000.0

    @property
    def is_request(self) -> bool:
        return self.payload_type == TYPE_REQUEST

    @property
    def text_body(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


class GorParseError(ValueError):
    pass


def _decompress(body: bytes, content_encoding: str) -> bytes:
    enc = content_encoding.lower().strip()
    if not body:
        return body
    try:
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            return zlib.decompress(body)
    except (OSError, zlib.error):
        # Leave the body untouched; the diff engine will report a body-decode
        # anomaly rather than silently comparing garbage.
        return body
    return body


def _dechunk(body: bytes) -> bytes:
    """Decode Transfer-Encoding: chunked bodies."""
    out = io.BytesIO()
    pos = 0
    while pos < len(body):
        line_end = body.find(b"\r\n", pos)
        if line_end == -1:
            break
        size_token = body[pos:line_end].split(b";")[0].strip()
        try:
            size = int(size_token, 16)
        except ValueError:
            return body  # not actually chunked - return as-is
        if size == 0:
            break
        start = line_end + 2
        out.write(body[start:start + size])
        pos = start + size + 2
    return out.getvalue()


def _split_head_body(raw: bytes) -> Tuple[bytes, bytes]:
    idx = raw.find(b"\r\n\r\n")
    if idx != -1:
        return raw[:idx], raw[idx + 4:]
    idx = raw.find(b"\n\n")
    if idx != -1:
        return raw[:idx], raw[idx + 2:]
    return raw, b""


def _parse_headers(head_lines: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for line in head_lines:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return headers


def parse_payload(payload: bytes) -> Optional[HttpMessage]:
    """Parse one GoReplay payload (meta line + raw HTTP)."""
    if not payload.strip():
        return None

    nl = payload.find(b"\n")
    if nl == -1:
        raise GorParseError("payload has no meta line")

    meta_parts = payload[:nl].decode("utf-8", errors="replace").split()
    if len(meta_parts) < 3:
        raise GorParseError(f"malformed meta line: {meta_parts!r}")

    payload_type = meta_parts[0]
    uuid = meta_parts[1]
    timestamp_ns = int(meta_parts[2]) if meta_parts[2].isdigit() else 0
    latency_ns = int(meta_parts[3]) if len(meta_parts) > 3 and meta_parts[3].isdigit() else 0

    raw = payload[nl + 1:]
    head, body = _split_head_body(raw)
    head_text = head.decode("utf-8", errors="replace")
    lines = head_text.split("\r\n") if "\r\n" in head_text else head_text.split("\n")
    if not lines:
        raise GorParseError("empty HTTP head")

    start_line = lines[0]
    headers = _parse_headers(lines[1:])

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _dechunk(body)
    if "content-encoding" in headers:
        body = _decompress(body, headers["content-encoding"])

    msg = HttpMessage(
        payload_type=payload_type,
        uuid=uuid,
        timestamp_ns=timestamp_ns,
        latency_ns=latency_ns,
        headers=headers,
        body=body,
        raw=raw,
    )

    tokens = start_line.split(" ", 2)
    if start_line.upper().startswith("HTTP/"):
        msg.status_code = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else None
        msg.reason = tokens[2] if len(tokens) > 2 else ""
    else:
        msg.method = tokens[0] if tokens else None
        msg.path = tokens[1] if len(tokens) > 1 else None

    return msg


def iter_messages(path: str) -> Iterator[HttpMessage]:
    """Stream every message from a .gor file."""
    with open(path, "rb") as fh:
        data = fh.read()
    for chunk in data.split(PAYLOAD_SEPARATOR):
        if not chunk.strip():
            continue
        try:
            msg = parse_payload(chunk)
        except GorParseError:
            continue
        if msg is not None:
            yield msg


@dataclass
class Exchange:
    """A request paired with its legacy and candidate responses."""

    uuid: str
    request: Optional[HttpMessage] = None
    legacy_response: Optional[HttpMessage] = None
    candidate_response: Optional[HttpMessage] = None

    @property
    def complete(self) -> bool:
        return self.legacy_response is not None and self.candidate_response is not None


def load_exchanges(
    combined: Optional[str] = None,
    legacy_file: Optional[str] = None,
    candidate_file: Optional[str] = None,
) -> Dict[str, Exchange]:
    """
    Two supported layouts:

    1. ``combined``  - a single .gor produced by
       ``gor --input-file ... --output-http ... --output-http-track-response
       --output-file replay.gor``  which contains type 1/2/3 records.

    2. ``legacy_file`` + ``candidate_file`` - separate captures, paired on uuid.
       Use this when the candidate is exercised by a different process.
    """
    exchanges: Dict[str, Exchange] = {}

    def ensure(uuid: str) -> Exchange:
        return exchanges.setdefault(uuid, Exchange(uuid=uuid))

    if combined:
        for msg in iter_messages(combined):
            ex = ensure(msg.uuid)
            if msg.payload_type == TYPE_REQUEST:
                ex.request = msg
            elif msg.payload_type == TYPE_ORIGINAL_RESPONSE:
                ex.legacy_response = msg
            elif msg.payload_type == TYPE_REPLAYED_RESPONSE:
                ex.candidate_response = msg

    if legacy_file:
        for msg in iter_messages(legacy_file):
            ex = ensure(msg.uuid)
            if msg.payload_type == TYPE_REQUEST:
                ex.request = ex.request or msg
            elif msg.payload_type in (TYPE_ORIGINAL_RESPONSE, TYPE_REPLAYED_RESPONSE):
                ex.legacy_response = msg

    if candidate_file:
        for msg in iter_messages(candidate_file):
            ex = ensure(msg.uuid)
            if msg.payload_type == TYPE_REQUEST:
                ex.request = ex.request or msg
            elif msg.payload_type in (TYPE_ORIGINAL_RESPONSE, TYPE_REPLAYED_RESPONSE):
                ex.candidate_response = msg

    return exchanges
