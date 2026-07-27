#!/usr/bin/env bash
# =====================================================================
# t14-networkpolicy.sh  -  T-14：實測影子 namespace 的 egress 隔離是否真的生效
# ---------------------------------------------------------------------
# 為什麼需要這支腳本：
#   NetworkPolicy 是「宣告」，能不能生效取決於 CNI 有沒有實作它。
#   套用成功不等於擋得住 —— 若 CNI 忽略 policy，`kubectl get networkpolicy`
#   一樣顯示存在，但所有流量照通。這是 PoC 最危險的假通過之一，
#   因為它讓「影子端絕不可連到正式資源」這道防線在無聲中失效。
#
#   因此每個「應被擋」的項目都配一個對照組（t14-control-probe，位於
#   shadow 之外、不受 policy 管）。對照組通、受管 Pod 不通，才能證明
#   阻斷是 policy 造成的，而不是本來就不通。
#
# 用法：
#   ./verify/t14-networkpolicy.sh              # 套用測試 Pod 並驗證
#   ./verify/t14-networkpolicy.sh --cleanup    # 只刪除測試 Pod
#   KUBECTL=/path/to/kubectl ./verify/t14-networkpolicy.sh
#
# 前置：叢集已套用 config/k8s/shadow-namespace.yaml。
#       本機驗證可用 kind：kind create cluster --name shadow-poc
#       （kind v0.33 的 kindnetd 已實作 NetworkPolicy；較舊版本未實作，
#         若本腳本回報阻斷項全數失敗，請改裝 Calico 或 Cilium 後重驗。）
#
# 離開碼：0 = 全部符合預期；1 = 有項目不符（隔離未如預期生效）
# =====================================================================
set -uo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NS="${NS:-shadow}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PODS="${HERE}/t14-test-pods.yaml"
EXTERNAL_IP="${EXTERNAL_IP:-1.1.1.1}"     # 模擬外部收單 / 清算 / 簡訊閘道
EXTERNAL_PORT="${EXTERNAL_PORT:-443}"

if [ "${1:-}" = "--cleanup" ]; then
  "$KUBECTL" delete -f "$PODS" --ignore-not-found >/dev/null 2>&1
  echo "測試 Pod 已刪除。"
  exit 0
fi

PASS=0; FAIL=0

pod_ip() { "$KUBECTL" -n "$1" get pod "$2" -o jsonpath='{.status.podIP}'; }

# 在指定 Pod 內測 TCP 連通性，回傳 OPEN / BLOCKED / REFUSED。
#   BLOCKED（逾時無回應）= 封包被丟棄，NetworkPolicy 的典型行為
#   REFUSED（立即拒絕）  = 有到達對端但沒有服務在聽 -> 測試環境問題，不是隔離
probe_tcp() {  # $1=ns $2=pod $3=ip $4=port
  "$KUBECTL" -n "$1" exec "$2" -- python -c "
import socket
try:
    s = socket.create_connection(('$3', $4), timeout=4); s.close(); print('OPEN')
except socket.timeout:
    print('BLOCKED')
except OSError as e:
    print('REFUSED' if e.errno in (111, 113) else 'BLOCKED')
" 2>/dev/null
}

probe_dns() {  # $1=ns $2=pod
  "$KUBECTL" -n "$1" exec "$2" -- python -c "
import socket
socket.setdefaulttimeout(4)
try:
    socket.gethostbyname('kubernetes.default.svc.cluster.local'); print('OK')
except Exception:
    print('FAIL')
" 2>/dev/null
}

check() {  # $1=名稱 $2=實得 $3=期望
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1)); printf '  ✅ %-56s %s\n' "$1" "$2"
  else
    FAIL=$((FAIL + 1)); printf '  ❌ %-56s 實得=%s 期望=%s\n' "$1" "$2" "$3"
  fi
}

# ---- 前置檢查 -------------------------------------------------------
if ! "$KUBECTL" -n "$NS" get networkpolicy default-deny-egress >/dev/null 2>&1; then
  echo "找不到 NetworkPolicy default-deny-egress。" >&2
  echo "請先執行：kubectl apply -f config/k8s/shadow-namespace.yaml" >&2
  exit 1
fi

echo "叢集：$("$KUBECTL" config current-context)"
echo "CNI ：$("$KUBECTL" -n kube-system get ds -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null)"
echo

echo "── 建立測試 Pod ───────────────────────────────────────────"
"$KUBECTL" apply -f "$PODS" >/dev/null
for p in t14-shadow-db t14-wiremock t14-not-whitelisted t14-newapp-probe t14-rogue-probe; do
  "$KUBECTL" -n "$NS" wait --for=condition=Ready "pod/$p" --timeout=300s >/dev/null || {
    echo "Pod $p 未就緒，中止。" >&2; exit 1; }
done
"$KUBECTL" wait --for=condition=Ready pod/t14-control-probe --timeout=300s >/dev/null || {
  echo "對照組 Pod 未就緒，中止。" >&2; exit 1; }
echo "  全部就緒。"
echo

DB="$(pod_ip "$NS" t14-shadow-db)"
WM="$(pod_ip "$NS" t14-wiremock)"
NW="$(pod_ip "$NS" t14-not-whitelisted)"
echo "目標 IP：shadow-db=$DB  wiremock=$WM  not-whitelisted=$NW"
echo

echo "── 對照組（不受 policy 管，用來排除「本來就不通」）─────────"
check "可連 shadow-db:5432"       "$(probe_tcp default t14-control-probe "$DB" 5432)" OPEN
check "可連 not-whitelisted:8080" "$(probe_tcp default t14-control-probe "$NW" 8080)" OPEN
check "可連外部網路"              "$(probe_tcp default t14-control-probe "$EXTERNAL_IP" "$EXTERNAL_PORT")" OPEN
echo

echo "── app=new-app（白名單放行對象）────────────────────────────"
check "放行：影子 DB TCP 5432"                "$(probe_tcp "$NS" t14-newapp-probe "$DB" 5432)" OPEN
check "放行：WireMock stub TCP 8080"          "$(probe_tcp "$NS" t14-newapp-probe "$WM" 8080)" OPEN
check "放行：DNS（kube-system:53）"           "$(probe_dns "$NS" t14-newapp-probe)" OK
check "阻斷：未列白名單的同 ns Pod"           "$(probe_tcp "$NS" t14-newapp-probe "$NW" 8080)" BLOCKED
check "阻斷：外部網路（收單/清算/簡訊）"      "$(probe_tcp "$NS" t14-newapp-probe "$EXTERNAL_IP" "$EXTERNAL_PORT")" BLOCKED
check "阻斷：白名單 Pod 的非放行埠"           "$(probe_tcp "$NS" t14-newapp-probe "$DB" 8080)" BLOCKED
echo

echo "── app=rogue（不符任何放行規則，只受 default-deny 管）──────"
check "阻斷：影子 DB"                         "$(probe_tcp "$NS" t14-rogue-probe "$DB" 5432)" BLOCKED
check "阻斷：DNS（預設拒絕涵蓋所有 Pod）"     "$(probe_dns "$NS" t14-rogue-probe)" FAIL
check "阻斷：外部網路"                        "$(probe_tcp "$NS" t14-rogue-probe "$EXTERNAL_IP" "$EXTERNAL_PORT")" BLOCKED
echo

echo "══════════════════════════════════════════════════════════"
echo " T-14 結果：${PASS} 符合預期 / ${FAIL} 不符"
echo "══════════════════════════════════════════════════════════"
if [ "$FAIL" -ne 0 ]; then
  echo
  echo "❌ egress 隔離未如預期生效 —— T-14 不通過，不得進入影子環境施測。"
  echo "   若「阻斷」項全數失敗（實得 OPEN），最可能是 CNI 未實作 NetworkPolicy。"
  echo "   請確認 CNI，或改用 Calico / Cilium 後重驗。"
  echo "   測試 Pod 保留供排查；排查完以 --cleanup 刪除。"
  exit 1
fi
echo
echo "✅ egress 白名單隔離實測生效。"
echo "   清理測試 Pod：$0 --cleanup"
