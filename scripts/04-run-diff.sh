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
# 離開碼：0 = Gate 通過（可推進下一階段）
#         1 = Gate 未過（停在原階段，有報告可複核）
#         2 = 比對引擎未能完成（環境問題，沒有報告 —— 不是 Gate 判定）
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

# LATENCY_RATIO 預設 0 = 端到端 latency 只列為參考值，不擋關。
# ADR-005 / CLAUDE.md 約束 #5：性能判準是應用內部 timer。重放器開銷、
# 容器多一跳網路、cgroup 節流都會讓端到端 latency 失真（實測曾出現
# 一致率 100% 卻因 P99 比 37x 被擋的情況）。
# 若確實要把它當 Gate，明確指定，例：LATENCY_RATIO=1.1 ./04-run-diff.sh <file>
PYTHON_BIN="${PYTHON_BIN:-python3}"

set +e
"$PYTHON_BIN" "${DIFF_DIR}/diff_engine.py" compare \
  --combined "${INPUT}" \
  --profile "${PROFILE}" \
  --out-json "${JSON}" \
  --out-md "${MD}" \
  --gate-consistency "${CONSISTENCY}" \
  --gate-latency-ratio "${LATENCY_RATIO:-0}" \
  --quiet
RC=$?
set -e

# 引擎崩潰（缺依賴、檔案格式錯誤等）與「Gate 未過」是兩件事，訊息不可混用：
# 前者沒有報告可看，把它說成「請人工複核差異樣本」會把人帶往錯誤方向。
if [ "$RC" -ne 0 ] && [ ! -s "${JSON}" ]; then
  echo
  echo "❌ 比對引擎未能完成（離開碼 ${RC}），沒有產生報告 —— 這不是 Gate 判定結果。"
  echo "   常見原因："
  echo "   - 缺 PyYAML：pip install pyyaml，或用 PYTHON_BIN 指定有裝的解譯器"
  echo "     例：PYTHON_BIN=.venv/bin/python ./04-run-diff.sh <file>"
  echo "   - 輸入檔不是 gor 格式，或缺少 type 3（重放回應）"
  echo "   請先排除上述問題再重跑；此結果不得視為 Gate 未通過。"
  exit 2
fi

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
