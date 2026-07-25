#!/usr/bin/env bash
# =====================================================================
# 03-replay-perf.sh  -  倍率重放（性能驗證）
# ---------------------------------------------------------------------
# 為什麼性能驗證不能用 mirror：
#   mirror 只能給 1x 生產負載形狀，無法放大倍率，也沒有背壓控制。
#   容量驗證一定要走「錄製 -> 倍率重放」。
#
# ⚠ 數字解讀前必讀：
#   VM 與容器的資源模型不同，端到端 latency 不可直接比。
#   請以兩邊應用「內部 timer」（進 controller -> 回應完成）為判準，
#   並確認容器端：
#     - CPU limit = request，PoC 期間放寬或移除，確認 CFS throttle = 0
#     - JVM 設 -XX:MaxRAMPercentage=75，ActiveProcessorCount 正確
#     - HPA 關閉、replica 固定
#     - 規格與 legacy VM 對等（4C/8G <-> requests 4 CPU / 8Gi）
#
# 用法：
#   ./03-replay-perf.sh /data/shadow-capture/legacy_20260725.gor
#   LADDER="100 200 300 500" ./03-replay-perf.sh <file>
# =====================================================================
set -euo pipefail

INPUT="${1:?usage: $0 <capture.gor>}"
TARGET="${TARGET:-https://new-app.k8s.internal}"
VHOST="${VHOST:-new-app.bank.internal}"
OUT_DIR="${OUT_DIR:-/data/perf-replay}"
LADDER="${LADDER:-100 200 500}"          # 百分比：1x, 2x, 5x
COOLDOWN="${COOLDOWN:-180}"              # 每階段間冷卻秒數，讓指標回穩
WORKERS="${WORKERS:-50}"
GOR_BIN="${GOR_BIN:-/usr/local/bin/gor}"

mkdir -p "$OUT_DIR"

for PCT in $LADDER; do
  STAMP="$(date +%Y%m%d-%H%M%S)"
  OUTPUT="${OUT_DIR}/perf_${PCT}pct_${STAMP}.gor"
  echo "=============================================="
  echo " 階段 ${PCT}%  ->  ${OUTPUT}"
  echo " 開始時間 $(date -Is)   （記下來，用於對齊 Grafana 區間）"
  echo "=============================================="

  "$GOR_BIN" \
    --input-file "${INPUT}|${PCT}%" \
    --input-file-loop=false \
    --output-http "${TARGET}" \
    --output-http-track-response \
    --output-http-workers "${WORKERS}" \
    --output-http-timeout 10s \
    --http-set-header "Host: ${VHOST}" \
    --http-set-header "X-Shadow-Request: true" \
    --http-set-header "X-Perf-Stage: ${PCT}" \
    --http-rewrite-header "Idempotency-Key: ^(.+)\$,\$1-perf-${PCT}" \
    --output-file "${OUTPUT}" \
    --stats

  echo "階段 ${PCT}% 結束 $(date -Is)"
  echo "檢查：CFS throttle / GC pause / 連線池飽和 / 錯誤率"
  echo "冷卻 ${COOLDOWN}s..."
  sleep "${COOLDOWN}"
done

echo
echo "全部階段完成。產出："
ls -lh "${OUT_DIR}"
