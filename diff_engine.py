#!/usr/bin/env python3
"""
Parallel-test diff engine
=========================

Compares legacy (VM) and candidate (container) responses captured by GoReplay
and produces a consistency report that can gate each PoC phase.

Commands
--------
    compare    Diff legacy vs candidate and evaluate the phase gates.
    baseline   Phase 0 - diff legacy vs legacy and emit noise-profile
               candidates for the paths that differ.

Examples
--------
    python3 diff_engine.py compare \\
        --combined /data/replay_20260725.gor \\
        --profile noise-profile.yaml \\
        --out-json report.json --out-md report.md \\
        --gate-consistency 99.9 --gate-latency-ratio 1.1

    python3 diff_engine.py baseline \\
        --combined /data/phase0_20260725.gor \\
        --out-profile noise-profile.generated.yaml

Exit codes
----------
    0  all gates passed
    1  a gate failed
    2  usage / input error
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gor_parser import Exchange, load_exchanges
from normalizer import NoiseProfile, PathMatcher, _fmt_path, normalize_json, parse_body

# --------------------------------------------------------------------------
# difference model
# --------------------------------------------------------------------------

CAT_STATUS = "STATUS_MISMATCH"
CAT_BODY = "BODY_DIFF"
CAT_HEADER = "HEADER_DIFF"
CAT_MISSING = "CANDIDATE_MISSING"
CAT_SHAPE = "BODY_TYPE_MISMATCH"


@dataclass
class Difference:
    category: str
    path: str
    legacy: Any
    candidate: Any

    def truncated(self, limit: int = 200) -> "Difference":
        def cut(v: Any) -> Any:
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
            return s if len(s) <= limit else s[:limit] + "...<truncated>"
        return Difference(self.category, self.path, cut(self.legacy), cut(self.candidate))


@dataclass
class ExchangeResult:
    uuid: str
    method: Optional[str]
    path: Optional[str]
    legacy_status: Optional[int]
    candidate_status: Optional[int]
    legacy_latency_ms: float
    candidate_latency_ms: float
    differences: List[Difference] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.differences


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------

def _values_equal(a: Any, b: Any, profile: NoiseProfile, path: List[str]) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        tol = profile.tolerance_for(path)
        if tol:
            delta = abs(a - b)
            if delta <= tol.abs_tol:
                return True
            if tol.rel_tol and abs(b) > 0 and delta / abs(b) <= tol.rel_tol:
                return True
            return False
        return a == b
    return a == b


def diff_json(
    legacy: Any,
    candidate: Any,
    profile: NoiseProfile,
    path: Optional[List[str]] = None,
    out: Optional[List[Difference]] = None,
) -> List[Difference]:
    path = path or []
    out = out if out is not None else []

    if profile.ignore_paths.matches(path):
        return out

    if isinstance(legacy, dict) and isinstance(candidate, dict):
        for key in sorted(set(legacy) | set(candidate)):
            child = path + [key]
            if profile.ignore_paths.matches(child):
                continue
            if key not in legacy:
                out.append(Difference(CAT_BODY, _fmt_path(child), "<absent>", candidate[key]))
            elif key not in candidate:
                out.append(Difference(CAT_BODY, _fmt_path(child), legacy[key], "<absent>"))
            else:
                diff_json(legacy[key], candidate[key], profile, child, out)
        return out

    if isinstance(legacy, list) and isinstance(candidate, list):
        if len(legacy) != len(candidate):
            out.append(
                Difference(CAT_BODY, _fmt_path(path) + ".length", len(legacy), len(candidate))
            )
        for i, (l_item, c_item) in enumerate(zip(legacy, candidate)):
            diff_json(l_item, c_item, profile, path + [f"[{i}]"], out)
        return out

    if type(legacy) is not type(candidate) and not (
        isinstance(legacy, (int, float)) and isinstance(candidate, (int, float))
    ):
        out.append(Difference(CAT_SHAPE, _fmt_path(path) or "$", legacy, candidate))
        return out

    if not _values_equal(legacy, candidate, profile, path):
        out.append(Difference(CAT_BODY, _fmt_path(path) or "$", legacy, candidate))
    return out


def compare_exchange(ex: Exchange, profile: NoiseProfile) -> ExchangeResult:
    req = ex.request
    legacy = ex.legacy_response
    candidate = ex.candidate_response

    result = ExchangeResult(
        uuid=ex.uuid,
        method=req.method if req else None,
        path=(req.path.split("?")[0] if req and req.path else None),
        legacy_status=legacy.status_code if legacy else None,
        candidate_status=candidate.status_code if candidate else None,
        legacy_latency_ms=legacy.latency_ms if legacy else 0.0,
        candidate_latency_ms=candidate.latency_ms if candidate else 0.0,
    )

    if legacy is None or candidate is None:
        result.differences.append(
            Difference(CAT_MISSING, "$", bool(legacy), bool(candidate))
        )
        return result

    # status ------------------------------------------------------------
    if legacy.status_code != candidate.status_code:
        pair = (legacy.status_code, candidate.status_code)
        if pair not in profile.ignore_status_pairs:
            result.differences.append(
                Difference(CAT_STATUS, "$.status", legacy.status_code, candidate.status_code)
            )

    # headers -----------------------------------------------------------
    for name in profile.compare_headers:
        if name in profile.ignore_headers:
            continue
        l_val, c_val = legacy.header(name), candidate.header(name)
        if l_val != c_val:
            result.differences.append(
                Difference(CAT_HEADER, f"$.headers.{name}", l_val, c_val)
            )

    # body --------------------------------------------------------------
    l_kind, l_body = parse_body(legacy.body, legacy.header("content-type"))
    c_kind, c_body = parse_body(candidate.body, candidate.header("content-type"))

    if l_kind == "json" and c_kind == "json":
        diff_json(
            normalize_json(l_body, profile),
            normalize_json(c_body, profile),
            profile,
            [],
            result.differences,
        )
    else:
        l_text = profile.apply_masks(l_body if isinstance(l_body, str) else str(l_body))
        c_text = profile.apply_masks(c_body if isinstance(c_body, str) else str(c_body))
        if l_text.strip() != c_text.strip():
            result.differences.append(Difference(CAT_BODY, "$.body", l_text, c_text))

    return result


# --------------------------------------------------------------------------
# aggregation & reporting
# --------------------------------------------------------------------------

def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * len(ordered))) - 1))
    return ordered[idx]


def build_report(results: List[ExchangeResult], sample_limit: int = 20) -> Dict[str, Any]:
    total = len(results)
    identical = sum(1 for r in results if r.identical)
    by_category = Counter()
    by_path = Counter()
    endpoint_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "diff": 0})
    samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    legacy_lat, candidate_lat = [], []
    legacy_status, candidate_status = Counter(), Counter()

    for r in results:
        key = f"{r.method or '?'} {r.path or '?'}"
        endpoint_stats[key]["total"] += 1
        if r.legacy_latency_ms:
            legacy_lat.append(r.legacy_latency_ms)
        if r.candidate_latency_ms:
            candidate_lat.append(r.candidate_latency_ms)
        if r.legacy_status:
            legacy_status[r.legacy_status] += 1
        if r.candidate_status:
            candidate_status[r.candidate_status] += 1
        if not r.identical:
            endpoint_stats[key]["diff"] += 1
            for d in r.differences:
                by_category[d.category] += 1
                by_path[d.path] += 1
                if len(samples[d.path]) < sample_limit:
                    samples[d.path].append(
                        {"uuid": r.uuid, "endpoint": key, **asdict(d.truncated())}
                    )

    consistency = (identical / total * 100.0) if total else 0.0
    new_codes = sorted(set(candidate_status) - set(legacy_status))

    def lat_block(values: List[float]) -> Dict[str, float]:
        return {
            "count": len(values),
            "mean": round(statistics.fmean(values), 2) if values else 0.0,
            "p50": round(_percentile(values, 50), 2),
            "p95": round(_percentile(values, 95), 2),
            "p99": round(_percentile(values, 99), 2),
            "max": round(max(values), 2) if values else 0.0,
        }

    l_block, c_block = lat_block(legacy_lat), lat_block(candidate_lat)
    ratio_p99 = round(c_block["p99"] / l_block["p99"], 3) if l_block["p99"] else 0.0

    return {
        "summary": {
            "total_exchanges": total,
            "identical": identical,
            "divergent": total - identical,
            "consistency_pct": round(consistency, 4),
        },
        "differences_by_category": dict(by_category.most_common()),
        "top_divergent_paths": [
            {"path": p, "count": c} for p, c in by_path.most_common(30)
        ],
        "endpoints": [
            {
                "endpoint": k,
                "total": v["total"],
                "divergent": v["diff"],
                "consistency_pct": round((v["total"] - v["diff"]) / v["total"] * 100, 4)
                if v["total"]
                else 0.0,
            }
            for k, v in sorted(
                endpoint_stats.items(), key=lambda kv: -kv[1]["diff"]
            )
        ],
        "status_distribution": {
            "legacy": dict(sorted(legacy_status.items())),
            "candidate": dict(sorted(candidate_status.items())),
            "new_codes_in_candidate": new_codes,
        },
        "latency_ms": {
            "legacy": l_block,
            "candidate": c_block,
            "p99_ratio": ratio_p99,
            "note": "End-to-end latency. Gate on in-application timers instead; "
                    "container ingress adds a hop and cgroup throttling skews tails.",
        },
        "samples": {k: v for k, v in samples.items()},
    }


def evaluate_gates(
    report: Dict[str, Any],
    min_consistency: float,
    max_latency_ratio: float,
    allow_new_status_codes: bool,
) -> List[Dict[str, Any]]:
    gates = []
    consistency = report["summary"]["consistency_pct"]
    gates.append(
        {
            "gate": "consistency",
            "threshold": f">= {min_consistency}%",
            "actual": f"{consistency}%",
            "passed": consistency >= min_consistency,
        }
    )
    ratio = report["latency_ms"]["p99_ratio"]
    gates.append(
        {
            "gate": "latency_p99_ratio",
            "threshold": f"<= {max_latency_ratio}",
            "actual": ratio,
            "passed": ratio == 0.0 or ratio <= max_latency_ratio,
        }
    )
    new_codes = report["status_distribution"]["new_codes_in_candidate"]
    gates.append(
        {
            "gate": "no_new_status_codes",
            "threshold": "none",
            "actual": new_codes or "none",
            "passed": allow_new_status_codes or not new_codes,
        }
    )
    return gates


def render_markdown(report: Dict[str, Any], gates: List[Dict[str, Any]]) -> str:
    s = report["summary"]
    lines = [
        "# 平行測試比對報告",
        "",
        "## 摘要",
        "",
        "| 項目 | 數值 |",
        "|---|---|",
        f"| 總比對筆數 | {s['total_exchanges']} |",
        f"| 一致 | {s['identical']} |",
        f"| 不一致 | {s['divergent']} |",
        f"| **一致率** | **{s['consistency_pct']}%** |",
        "",
        "## Gate 判定",
        "",
        "| Gate | 門檻 | 實際 | 結果 |",
        "|---|---|---|---|",
    ]
    for g in gates:
        lines.append(
            f"| {g['gate']} | {g['threshold']} | {g['actual']} | "
            f"{'✅ PASS' if g['passed'] else '❌ FAIL'} |"
        )

    lines += ["", "## 差異分類", "", "| 類別 | 次數 |", "|---|---|"]
    for cat, count in report["differences_by_category"].items():
        lines.append(f"| {cat} | {count} |")
    if not report["differences_by_category"]:
        lines.append("| (無) | 0 |")

    lines += ["", "## 延遲（端到端，僅供參考）", "",
              "| 指標 | Legacy | Candidate |", "|---|---|---|"]
    lat = report["latency_ms"]
    for key in ("p50", "p95", "p99", "max"):
        lines.append(f"| {key.upper()} (ms) | {lat['legacy'][key]} | {lat['candidate'][key]} |")
    lines.append("")
    lines.append(f"> P99 比值 = {lat['p99_ratio']}。{lat['note']}")

    lines += ["", "## 差異最多的欄位（Top 15）", "", "| 路徑 | 次數 |", "|---|---|"]
    for item in report["top_divergent_paths"][:15]:
        lines.append(f"| `{item['path']}` | {item['count']} |")
    if not report["top_divergent_paths"]:
        lines.append("| (無) | 0 |")

    lines += ["", "## 各端點一致率", "",
              "| 端點 | 總數 | 不一致 | 一致率 |", "|---|---|---|---|"]
    for ep in report["endpoints"][:25]:
        lines.append(
            f"| `{ep['endpoint']}` | {ep['total']} | {ep['divergent']} | {ep['consistency_pct']}% |"
        )

    lines += ["", "## 差異樣本", ""]
    for path, items in list(report["samples"].items())[:10]:
        lines.append(f"### `{path}`")
        lines.append("")
        lines.append("| uuid | 端點 | legacy | candidate |")
        lines.append("|---|---|---|---|")
        for it in items[:5]:
            lines.append(
                f"| `{it['uuid'][:12]}` | `{it['endpoint']}` | `{it['legacy']}` | `{it['candidate']}` |"
            )
        lines.append("")
    if not report["samples"]:
        lines.append("（無差異樣本）")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Phase 0 baseline learner
# --------------------------------------------------------------------------

def learn_noise_profile(
    results: List[ExchangeResult], min_occurrences: int, total: int
) -> Dict[str, Any]:
    """
    Phase 0 mirrors legacy traffic to a *second legacy instance*. Anything that
    still differs is by definition noise, not behaviour. Paths that diverge
    often enough become ignore_paths candidates; the rest are listed for manual
    review so nobody silently suppresses a real defect.
    """
    counts = Counter()
    for r in results:
        for d in r.differences:
            if d.category in (CAT_BODY, CAT_SHAPE):
                counts[d.path] += 1

    def generalize(path: str) -> str:
        out, i = [], 0
        for seg in path.replace("[", ".[").split("."):
            if not seg:
                continue
            out.append("[*]" if seg.startswith("[") and seg != "[*]" else seg)
        return ".".join(out).replace(".[*]", "[*]")

    generalized = Counter()
    for path, count in counts.items():
        generalized[generalize(path)] += count

    auto = [p for p, c in generalized.items() if c >= min_occurrences]
    review = [
        {"path": p, "count": c, "rate_pct": round(c / total * 100, 3)}
        for p, c in generalized.most_common()
        if c < min_occurrences
    ]
    return {"ignore_paths": sorted(auto), "_review_candidates": review}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load(args) -> Dict[str, Exchange]:
    if not (args.combined or (args.legacy and args.candidate)):
        print("error: provide --combined, or both --legacy and --candidate", file=sys.stderr)
        sys.exit(2)
    return load_exchanges(
        combined=args.combined, legacy_file=args.legacy, candidate_file=args.candidate
    )


def cmd_compare(args) -> int:
    profile = NoiseProfile.load(args.profile)
    exchanges = _load(args)
    results = [
        compare_exchange(ex, profile)
        for ex in exchanges.values()
        if args.include_incomplete or ex.complete
    ]
    if not results:
        print("error: no comparable exchanges found", file=sys.stderr)
        return 2

    report = build_report(results, sample_limit=args.sample_limit)
    gates = evaluate_gates(
        report, args.gate_consistency, args.gate_latency_ratio, args.allow_new_status_codes
    )
    report["gates"] = gates

    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    md = render_markdown(report, gates)
    if args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)
    if not args.quiet:
        print(md)

    failed = [g["gate"] for g in gates if not g["passed"]]
    if failed:
        print(f"GATE FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("ALL GATES PASSED", file=sys.stderr)
    return 0


def cmd_baseline(args) -> int:
    profile = NoiseProfile.load(args.profile)
    exchanges = _load(args)
    results = [compare_exchange(ex, profile) for ex in exchanges.values() if ex.complete]
    if not results:
        print("error: no comparable exchanges found", file=sys.stderr)
        return 2

    learned = learn_noise_profile(results, args.min_occurrences, len(results))
    base = {}
    if args.profile:
        with open(args.profile, "r", encoding="utf-8") as fh:
            import yaml as _yaml
            base = _yaml.safe_load(fh) or {}
    merged = dict(base)
    merged["ignore_paths"] = sorted(
        set(base.get("ignore_paths", []) or []) | set(learned["ignore_paths"])
    )

    import yaml as _yaml
    text = _yaml.safe_dump(merged, allow_unicode=True, sort_keys=False)
    header = (
        "# Generated by diff_engine.py baseline (Phase 0)\n"
        f"# samples={len(results)}  min_occurrences={args.min_occurrences}\n"
        "# 每一條都必須人工複核後才可套用 - 抑制真實缺陷的風險由此而來。\n"
    )
    with open(args.out_profile, "w", encoding="utf-8") as fh:
        fh.write(header + text)

    review_path = args.out_profile + ".review.json"
    with open(review_path, "w", encoding="utf-8") as fh:
        json.dump(learned["_review_candidates"], fh, ensure_ascii=False, indent=2)

    total = len(results)
    identical = sum(1 for r in results if r.identical)
    fp_rate = (total - identical) / total * 100
    print(f"samples={total}  false-positive rate={fp_rate:.3f}%")
    print(f"auto ignore_paths: {len(learned['ignore_paths'])} -> {args.out_profile}")
    print(f"manual review queue: {len(learned['_review_candidates'])} -> {review_path}")
    if fp_rate > args.gate_false_positive:
        print(
            f"GATE FAILED: false-positive rate {fp_rate:.3f}% > {args.gate_false_positive}%",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Parallel-test diff engine")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_inputs(p):
        p.add_argument("--combined", help="single .gor containing type 1/2/3 records")
        p.add_argument("--legacy", help=".gor with legacy responses")
        p.add_argument("--candidate", help=".gor with candidate responses")
        p.add_argument("--profile", help="noise profile YAML")

    c = sub.add_parser("compare", help="diff legacy vs candidate")
    add_inputs(c)
    c.add_argument("--out-json")
    c.add_argument("--out-md")
    c.add_argument("--sample-limit", type=int, default=20)
    c.add_argument("--gate-consistency", type=float, default=99.9)
    c.add_argument("--gate-latency-ratio", type=float, default=1.1)
    c.add_argument("--allow-new-status-codes", action="store_true")
    c.add_argument("--include-incomplete", action="store_true",
                   help="also report exchanges missing one side")
    c.add_argument("--quiet", action="store_true")
    c.set_defaults(func=cmd_compare)

    b = sub.add_parser("baseline", help="Phase 0 noise learning (legacy vs legacy)")
    add_inputs(b)
    b.add_argument("--out-profile", default="noise-profile.generated.yaml")
    b.add_argument("--min-occurrences", type=int, default=3)
    b.add_argument("--gate-false-positive", type=float, default=1.0)
    b.set_defaults(func=cmd_baseline)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
