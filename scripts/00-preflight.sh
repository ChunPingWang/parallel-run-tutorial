#!/usr/bin/env bash
# =====================================================================
# 00-preflight.sh  -  跨界連通性驗證（VM -> K8s）
# ---------------------------------------------------------------------
# W1 必做。混合拓撲的假陽性有一半來自這裡沒先驗過。
# 在 legacy VM（或 shadow-gw VM）上執行。
#
# 用法：
#   ./00-preflight.sh
#   TARGET_HOST=new-app.bank.internal TARGET_IP=10.20.30.40 ./00-preflight.sh
# =====================================================================
set -uo pipefail

TARGET_HOST="${TARGET_HOST:-new-app.bank.internal}"
TARGET_IP="${TARGET_IP:-}"
TARGET_PORT="${TARGET_PORT:-443}"
SCHEME="${SCHEME:-https}"
PROBE_PATH="${PROBE_PATH:-/actuator/health}"
LEGACY_DB_HOST="${LEGACY_DB_HOST:-prod-db.bank.internal}"
LEGACY_DB_PORT="${LEGACY_DB_PORT:-1521}"

PASS=0; FAIL=0; WARN=0
ok()   { echo "  ✅ $*"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠️  $*"; WARN=$((WARN+1)); }
sec()  { echo; echo "── $* ─────────────────────────────"; }

echo "跨界連通性驗證  target=${SCHEME}://${TARGET_HOST}:${TARGET_PORT}"

# ---------------------------------------------------------------------
sec "1. DNS 解析"
RESOLVED="$(getent hosts "${TARGET_HOST}" 2>/dev/null | awk '{print $1}' | head -1)"
if [ -n "$RESOLVED" ]; then
  ok "解析成功 -> ${RESOLVED}"
  TARGET_IP="${TARGET_IP:-$RESOLVED}"
else
  bad "無法解析 ${TARGET_HOST}。VM 網段需能查到 K8s Ingress，或直接改用 LB VIP。"
fi

# ---------------------------------------------------------------------
sec "2. TCP 連通"
if timeout 5 bash -c "cat < /dev/null > /dev/tcp/${TARGET_HOST}/${TARGET_PORT}" 2>/dev/null; then
  ok "TCP ${TARGET_PORT} 可達"
else
  bad "TCP ${TARGET_PORT} 不可達 - 檢查防火牆 / NodePort / LB"
fi

# ---------------------------------------------------------------------
sec "3. MTU（Overlay 網路常見陷阱）"
# Calico VXLAN 1450 / Flannel 1450；VM 網段通常 1500。
# 大 POST body 跨界時可能 fragment 或黑洞。
if [ -n "$TARGET_IP" ]; then
  for SIZE in 1472 1422 1372; do
    if ping -c 2 -W 2 -M do -s "$SIZE" "$TARGET_IP" >/dev/null 2>&1; then
      ok "payload ${SIZE} bytes 通過（實際 MTU >= $((SIZE+28))）"
      break
    else
      warn "payload ${SIZE} bytes 不通（MTU < $((SIZE+28))）"
    fi
  done
  echo "     若最大只到 1372，代表路徑 MTU 約 1400，"
  echo "     請對大 body 端點另行驗證，或調整 Overlay MTU。"
else
  warn "略過 - 無 IP"
fi

# ---------------------------------------------------------------------
sec "4. TLS / SNI（Ingress 依 SNI 選 backend）"
if command -v openssl >/dev/null 2>&1 && [ "$SCHEME" = "https" ]; then
  TLS_OUT="$(echo | timeout 8 openssl s_client -connect "${TARGET_HOST}:${TARGET_PORT}" \
             -servername "${TARGET_HOST}" 2>/dev/null)"
  if echo "$TLS_OUT" | grep -q "BEGIN CERTIFICATE"; then
    SUBJ="$(echo "$TLS_OUT" | openssl x509 -noout -subject 2>/dev/null)"
    EXP="$(echo "$TLS_OUT" | openssl x509 -noout -enddate 2>/dev/null)"
    ok "TLS 握手成功 ${SUBJ} ${EXP}"
  else
    bad "TLS 握手失敗 - 確認 SNI 名稱與 Ingress TLS secret 一致"
  fi
else
  warn "略過 TLS 檢查"
fi

# ---------------------------------------------------------------------
sec "5. Host header 路由"
CODE_CORRECT="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
  -H "Host: ${TARGET_HOST}" "${SCHEME}://${TARGET_HOST}:${TARGET_PORT}${PROBE_PATH}" 2>/dev/null)"
CODE_WRONG="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
  -H "Host: legacy.bank.internal" "${SCHEME}://${TARGET_HOST}:${TARGET_PORT}${PROBE_PATH}" 2>/dev/null)"
echo "     正確 Host -> ${CODE_CORRECT} ；沿用舊 Host -> ${CODE_WRONG}"
if [ "$CODE_CORRECT" = "200" ]; then
  ok "以正確 Host 可達應用"
else
  bad "正確 Host 回 ${CODE_CORRECT}"
fi
if [ "$CODE_WRONG" = "404" ] || [ "$CODE_WRONG" = "421" ]; then
  ok "沿用舊 Host 如預期被拒 -> 重放時務必改寫 Host header"
else
  warn "沿用舊 Host 回 ${CODE_WRONG}，Ingress 可能有 catch-all，仍建議明確指定"
fi

# ---------------------------------------------------------------------
sec "6. X-Forwarded-For 鏈路"
# 經 Ingress + Service NAT 後，Pod 看到的 remote addr 是節點 IP。
# 新應用若依 client IP 做風控，取值索引會與舊系統不同 -> 大量假陽性。
XFF_ECHO="${XFF_ECHO_PATH:-}"
if [ -n "$XFF_ECHO" ]; then
  RESP="$(curl -sk --max-time 8 -H "Host: ${TARGET_HOST}" \
    -H "X-Forwarded-For: 203.0.113.7" \
    "${SCHEME}://${TARGET_HOST}:${TARGET_PORT}${XFF_ECHO}" 2>/dev/null)"
  echo "     echo 端點回應：${RESP}"
  if echo "$RESP" | grep -q "203.0.113.7"; then
    ok "XFF 有傳遞到應用"
  else
    bad "XFF 遺失 - Ingress 需開 use-forwarded-headers，或設 externalTrafficPolicy: Local"
  fi
else
  warn "未設 XFF_ECHO_PATH，略過。強烈建議在新應用暫時開一個 echo 端點驗證此項。"
fi

# ---------------------------------------------------------------------
sec "7. 影子端出網隔離（最重要的安全檢查）"
# 新應用網段「不得」連到正式 DB 或外部端點。
# 此檢查應在新應用的 Pod 內執行；此處驗證 VM 側可達性作為對照。
echo "     請在新應用 Pod 內執行以下指令，預期為『不通』："
echo "       kubectl -n shadow exec deploy/new-app -- \\"
echo "         timeout 3 bash -c '</dev/tcp/${LEGACY_DB_HOST}/${LEGACY_DB_PORT}' ; echo \$?"
echo "     回傳非 0 才代表 NetworkPolicy 生效。若為 0 → 立即停止 PoC。"
warn "此項需人工在 Pod 內確認並記錄於檢核表 T-14"

# ---------------------------------------------------------------------
sec "8. Shadow header 端到端"
SHADOW_CODE="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
  -H "Host: ${TARGET_HOST}" -H "X-Shadow-Request: true" \
  "${SCHEME}://${TARGET_HOST}:${TARGET_PORT}${PROBE_PATH}" 2>/dev/null)"
if [ "$SHADOW_CODE" = "200" ]; then
  ok "帶 X-Shadow-Request 可正常回應（應用需據此進 dry-run）"
else
  bad "帶 X-Shadow-Request 回 ${SHADOW_CODE}"
fi

# ---------------------------------------------------------------------
echo
echo "=============================================="
echo " PASS=${PASS}  FAIL=${FAIL}  WARN=${WARN}"
echo "=============================================="
[ "$FAIL" -eq 0 ] || { echo "❌ W1 Gate 未通過"; exit 1; }
echo "✅ W1 連通性 Gate 通過（WARN 項仍需人工確認）"
