#!/usr/bin/env bash
# =====================================================================
# 02-replay-functional.sh  -  1x 重放至容器版新應用（功能比對）
# ---------------------------------------------------------------------
# 混合拓撲重點（VM -> K8s）：
#   * Host header 必須改寫，否則 Ingress 依 SNI/Host 找不到 backend -> 404
#   * X-Shadow-Request 讓新應用進 dry-run（不發簡訊、不呼叫外部、不寫主檔）
#   * Idempotency-Key 加後綴，避免被防重放機制擋掉
#   * Accept-Encoding: identity 讓 body 免解壓即可 diff
#
# 用法：
#   ./02-replay-functional.sh /data/shadow-capture/legacy_20260725-090000.gor
# =====================================================================
set -euo pipefail

INPUT="${1:?usage: $0 <capture.gor>}"
TARGET="${TARGET:-https://new-app.k8s.internal}"
VHOST="${VHOST:-new-app.bank.internal}"     # K8s Ingress 用的 Host
OUT_DIR="${OUT_DIR:-/data/shadow-replay}"
WORKERS="${WORKERS:-20}"
TIMEOUT="${TIMEOUT:-5s}"
GOR_BIN="${GOR_BIN:-/usr/local/bin/gor}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT_DIR"
OUTPUT="${OUT_DIR}/replay_${STAMP}.gor"

echo "input : ${INPUT}"
echo "target: ${TARGET} (Host: ${VHOST})"
echo "output: ${OUTPUT}"
echo

"$GOR_BIN" \
  --input-file "${INPUT}" \
  --input-file-loop=false \
  --output-http "${TARGET}" \
  --output-http-track-response \
  --output-http-workers "${WORKERS}" \
  --output-http-timeout "${TIMEOUT}" \
  --output-http-response-buffer 10mb \
  --http-set-header "Host: ${VHOST}" \
  --http-set-header "X-Shadow-Request: true" \
  --http-set-header "X-Origin-Platform: vm" \
  --http-set-header "Accept-Encoding: identity" \
  --http-rewrite-header "Idempotency-Key: ^(.+)$,\$1-shadow" \
  --output-file "${OUTPUT}" \
  --stats

# gor 會在副檔名前插入 chunk 序號：replay_x.gor -> replay_x_0.gor（可能多個）。
# 直接沿用 ${OUTPUT} 會指到不存在的檔案，因此改取實際落地的檔名。
ACTUAL="$(ls -t "${OUT_DIR}"/replay_${STAMP}_*.gor 2>/dev/null | head -1 || true)"
if [ -z "$ACTUAL" ]; then
  echo "⚠ 找不到 gor 產出的檔案（預期 ${OUTPUT%.gor}_N.gor）。" >&2
  echo "  請確認 gor 是否成功啟動，以及 ${OUT_DIR} 是否可寫。" >&2
  exit 1
fi

echo
echo "實際產出：${ACTUAL}"
echo "完成。接著執行："
echo "  ./04-run-diff.sh ${ACTUAL}"
