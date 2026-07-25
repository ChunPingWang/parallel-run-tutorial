"""
Noise normalization for parallel-test response diffing.

Phase 0 of the PoC establishes which differences are *expected* (timestamps,
generated identifiers, unstable collection ordering, CDC replication lag) so
that Phase 1+ only surfaces real behavioural divergence. This module applies
that noise profile to a decoded response before comparison.

Supported JSONPath subset
-------------------------
    $.a.b            exact key path
    $.a[*].b         every element of an array
    $.a[0].b         a specific index
    $..token         any depth - matches the key anywhere in the tree
    $.a.*            any key at that level
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

REDACTED = "<<NORMALIZED>>"


# --------------------------------------------------------------------------
# path matching
# --------------------------------------------------------------------------

def _tokenize(pattern: str) -> List[str]:
    """'$.a[*].b' -> ['a', '[*]', 'b'];  '$..id' -> ['**', 'id']"""
    p = pattern.strip()
    if p.startswith("$"):
        p = p[1:]
    p = p.replace("..", ".**.")
    tokens: List[str] = []
    for part in p.split("."):
        if not part:
            continue
        while "[" in part:
            head, _, rest = part.partition("[")
            if head:
                tokens.append(head)
            idx, _, part = rest.partition("]")
            tokens.append(f"[{idx}]")
        if part:
            tokens.append(part)
    return tokens


def _match_tokens(tokens: List[str], path: List[str], ti: int = 0, pi: int = 0) -> bool:
    while ti < len(tokens) and pi < len(path):
        tok = tokens[ti]
        if tok == "**":
            # recursive descent: try to match the remainder at any depth
            for skip in range(pi, len(path) + 1):
                if _match_tokens(tokens, path, ti + 1, skip):
                    return True
            return False
        seg = path[pi]
        if tok == "*" or tok == "[*]":
            pass  # wildcard, always matches one segment
        elif tok.startswith("["):
            if seg != tok:
                return False
        elif tok != seg:
            return False
        ti += 1
        pi += 1
    if ti < len(tokens) and tokens[ti] == "**" and ti + 1 == len(tokens):
        return True
    return ti == len(tokens) and pi == len(path)


class PathMatcher:
    def __init__(self, patterns: List[str]):
        self.patterns = patterns
        self._tokens = [_tokenize(p) for p in patterns]

    def matches(self, path: List[str]) -> bool:
        return any(_match_tokens(tok, path) for tok in self._tokens)

    def __bool__(self) -> bool:
        return bool(self.patterns)


def _fmt_path(path: List[str]) -> str:
    out = "$"
    for seg in path:
        out += seg if seg.startswith("[") else "." + seg
    return out


# --------------------------------------------------------------------------
# noise profile
# --------------------------------------------------------------------------

@dataclass
class NumericTolerance:
    matcher: PathMatcher
    abs_tol: float = 0.0
    rel_tol: float = 0.0


@dataclass
class ArraySort:
    matcher: PathMatcher
    key: Optional[str] = None


@dataclass
class NoiseProfile:
    ignore_paths: PathMatcher = field(default_factory=lambda: PathMatcher([]))
    ignore_headers: List[str] = field(default_factory=list)
    compare_headers: List[str] = field(default_factory=list)
    mask_patterns: List[Tuple[re.Pattern, str]] = field(default_factory=list)
    sort_arrays: List[ArraySort] = field(default_factory=list)
    numeric_tolerances: List[NumericTolerance] = field(default_factory=list)
    ignore_status_pairs: List[Tuple[int, int]] = field(default_factory=list)
    treat_absent_as_null: bool = True

    @classmethod
    def load(cls, path: Optional[str]) -> "NoiseProfile":
        if not path:
            return cls()
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "NoiseProfile":
        masks: List[Tuple[re.Pattern, str]] = []
        for item in raw.get("mask_patterns", []) or []:
            if isinstance(item, str):
                masks.append((re.compile(item), REDACTED))
            else:
                masks.append((re.compile(item["pattern"]), item.get("replacement", REDACTED)))

        sorts: List[ArraySort] = []
        for item in raw.get("sort_arrays", []) or []:
            if isinstance(item, str):
                sorts.append(ArraySort(PathMatcher([item])))
            else:
                sorts.append(ArraySort(PathMatcher([item["path"]]), item.get("key")))

        tols: List[NumericTolerance] = []
        for item in raw.get("numeric_tolerance", []) or []:
            tols.append(
                NumericTolerance(
                    PathMatcher([item["path"]]),
                    abs_tol=float(item.get("abs", 0.0)),
                    rel_tol=float(item.get("rel", 0.0)),
                )
            )

        return cls(
            ignore_paths=PathMatcher(raw.get("ignore_paths", []) or []),
            ignore_headers=[h.lower() for h in (raw.get("ignore_headers", []) or [])],
            compare_headers=[h.lower() for h in (raw.get("compare_headers", []) or [])],
            mask_patterns=masks,
            sort_arrays=sorts,
            numeric_tolerances=tols,
            ignore_status_pairs=[tuple(p) for p in (raw.get("ignore_status_pairs", []) or [])],
            treat_absent_as_null=bool(raw.get("treat_absent_as_null", True)),
        )

    # ---------------------------------------------------------------- masking

    def apply_masks(self, text: str) -> str:
        for pattern, replacement in self.mask_patterns:
            text = pattern.sub(replacement, text)
        return text

    def tolerance_for(self, path: List[str]) -> Optional[NumericTolerance]:
        for tol in self.numeric_tolerances:
            if tol.matcher.matches(path):
                return tol
        return None

    def sort_spec_for(self, path: List[str]) -> Optional[ArraySort]:
        for spec in self.sort_arrays:
            if spec.matcher.matches(path):
                return spec
        return None


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------

def _sort_key(item: Any, key: Optional[str]) -> str:
    if key and isinstance(item, dict) and key in item:
        return json.dumps(item[key], sort_keys=True, ensure_ascii=False)
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def normalize_json(node: Any, profile: NoiseProfile, path: Optional[List[str]] = None) -> Any:
    """Recursively drop ignored paths, mask noisy scalars and stabilise arrays."""
    path = path or []

    if isinstance(node, dict):
        out = {}
        for key in sorted(node.keys()):
            child_path = path + [key]
            if profile.ignore_paths.matches(child_path):
                continue
            out[key] = normalize_json(node[key], profile, child_path)
        return out

    if isinstance(node, list):
        items = []
        for i, item in enumerate(node):
            child_path = path + [f"[{i}]"]
            if profile.ignore_paths.matches(child_path):
                continue
            items.append(normalize_json(item, profile, path + ["[*]"]))
        spec = profile.sort_spec_for(path)
        if spec:
            items.sort(key=lambda it: _sort_key(it, spec.key))
        return items

    if isinstance(node, str):
        return profile.apply_masks(node)

    return node


def parse_body(raw: bytes, content_type: str) -> Tuple[str, Any]:
    """Returns (kind, value) where kind is 'json' | 'text' | 'binary'."""
    ct = (content_type or "").lower()
    text = raw.decode("utf-8", errors="replace")
    if "json" in ct or text.lstrip()[:1] in ("{", "["):
        try:
            return "json", json.loads(text or "null")
        except (json.JSONDecodeError, ValueError):
            return "text", text
    if not ct or ct.startswith("text/") or "xml" in ct or "html" in ct:
        return "text", text
    return "binary", text
