#!/usr/bin/env bash
# =====================================================================
# 04-run-diff.sh  -  執行比對並依 Phase 套用對應 Gate 門檻
# ---------------------------------------------------------------------
# 用法：
#   ./04-run-diff.sh /data/shadow-replay/replay_20260725.gor
#   PHASE=3 ./04-run-diff.sh <file>
#
# Phase 對應門檻：
#   0  噪音基線（走 baseline 子命令，不在此腳本）
#   1  唯讀 GET      一致率 >= 99.9%
#   2  非核心寫入    一致率 >= 99.95%
#   3  核心交易      一致率 = 100%
#
# 離開碼：0 = Gate 通過（可推進下一階段）；1 = Gate 未過（停在原階段）
# =====================================================================
set -euo pipefail

INPUT="${1:?usage: $0 <replay.gor>}"
PHASE="${PHASE:-1}"
DIFF_DIR="${DIFF_DIR:-$(cd "$(dirname "$0")/../diff" && pwd)}"
PROFILE="${PROFILE:-${DIFF_DIR}/noise-profile.yaml}"
REPORT_DIR="${REPORT_DIR:-/data/reports}"
STAMP="$(date +%Y%m%d-%H%M%S)"

case "$PHASE" in
  1) CONSISTENCY=99.9  ;;
  2) CONSISTENCY=99.95 ;;
  3) CONSISTENCY=100   ;;
  *) CONSISTENCY="${CONSISTENCY:-99.9}" ;;
esac

mkdir -p "$REPORT_DIR"
JSON="${REPORT_DIR}/phase${PHASE}_${STAMP}.json"
MD="${REPORT_DIR}/phase${PHASE}_${STAMP}.md"

echo "Phase ${PHASE}  |  一致率門檻 ${CONSISTENCY}%  |  profile: ${PROFILE}"
echo

set +e
python3 "${DIFF_DIR}/diff_engine.py" compare \
  --combined "${INPUT}" \
  --profile "${PROFILE}" \
  --out-json "${JSON}" \
  --out-md "${MD}" \
  --gate-consistency "${CONSISTENCY}" \
  --gate-latency-ratio "${LATENCY_RATIO:-1.1}" \
  --quiet
RC=$?
set -e

echo "報告：${MD}"
echo "      ${JSON}"

if [ "$RC" -ne 0 ]; then
  echo
  echo "❌ Phase ${PHASE} Gate 未通過 - 不得推進下一階段。"
  echo "   請人工複核差異樣本，判定為缺陷或噪音："
  echo "   - 缺陷 -> 開單修正後重跑"
  echo "   - 噪音 -> 經簽核後加入 ${PROFILE} 的 ignore_paths"
  exit 1
fi

echo
echo "✅ Phase ${PHASE} Gate 通過。"
echo "   提醒：一致率達標仍需人工抽樣複核 30 筆，確認不是被 profile 過度抑制。"
