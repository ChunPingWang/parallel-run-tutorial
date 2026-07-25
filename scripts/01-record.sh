#!/usr/bin/env bash
# =====================================================================
# 01-record.sh  -  在 legacy VM 上錄製生產流量
# ---------------------------------------------------------------------
# 特性：不改動任何流量路徑，直接監聽網卡。正線零風險。
#
# 前置：
#   - gor binary（GoReplay）已安裝，需 root 或 CAP_NET_RAW
#   - 錄製檔目錄有足夠磁碟（見 SIZE_ESTIMATE 說明）
#   - 法遵已核准 body 落地與保留天數
#
# 用法：
#   sudo ./01-record.sh
#   sudo PERCENT=100 DURATION=30m ./01-record.sh
# =====================================================================
set -euo pipefail

APP_PORT="${APP_PORT:-8080}"
OUT_DIR="${OUT_DIR:-/data/shadow-capture}"
PERCENT="${PERCENT:-5}"              # 取樣百分比，正式環境建議從 5 開始
DURATION="${DURATION:-60m}"          # 錄製時長，例：30m / 2h
ROTATE_SIZE="${ROTATE_SIZE:-512mb}"  # 單檔大小上限
GOR_BIN="${GOR_BIN:-/usr/local/bin/gor}"

# 只錄目標 API，排除健康檢查、靜態資源、上傳類端點
INCLUDE_PATH="${INCLUDE_PATH:-^/api/}"
EXCLUDE_PATH="${EXCLUDE_PATH:-(/health|/actuator|/metrics|/static/|/upload)}"

mkdir -p "$OUT_DIR"

echo "=============================================="
echo " GoReplay 錄製"
echo "   port      : ${APP_PORT}"
echo "   sampling  : ${PERCENT}%"
echo "   duration  : ${DURATION}"
echo "   output    : ${OUT_DIR}"
echo "=============================================="
echo
echo "⚠ 錄製內容含 request/response body。確認："
echo "  1) 保留天數已設定（見 90-retention.cron）"
echo "  2) 目錄權限 700、磁碟已加密"
echo "  3) 落盤後須跑 sanitize-gor.py 遮蔽個資才可移出本機"
echo

# --input-raw-track-response：同時錄下舊系統的回應，供離線 diff 當基準
exec "$GOR_BIN" \
  --input-raw ":${APP_PORT}" \
  --input-raw-track-response \
  --input-raw-engine raw_socket \
  --http-allow-url "${INCLUDE_PATH}" \
  --http-disallow-url "${EXCLUDE_PATH}" \
  --http-allow-method GET --http-allow-method POST \
  --http-allow-method PUT --http-allow-method PATCH \
  --output-file "${OUT_DIR}/legacy_%Y%m%d-%H%M%S.gor" \
  --output-file-append \
  --output-file-size-limit "${ROTATE_SIZE}" \
  --output-file-max-size 50gb \
  --exit-after "${DURATION}" \
  --stats --output-file-stats
