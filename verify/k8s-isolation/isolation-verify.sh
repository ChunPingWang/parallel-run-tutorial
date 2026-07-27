#!/usr/bin/env bash
# 隔離拓樸執行期驗證：把 shadow-namespace.yaml 套進真叢集，實測「誰連得到誰」。
#
# 與靜態斷言的差別：靜態斷言只確認 YAML 裡寫了 default-deny；這支腳本是從
# shadow namespace 的 Pod 內部實際發 TCP 連線，看封包有沒有真的被 drop。
set -u
# 預設值是為了容器內執行（以 root 身分）；在容器外跑時沿用既有的 KUBECONFIG。
export KUBECONFIG=${KUBECONFIG:-/root/.kube/config}

SRC=${SRC:?請設定 SRC=專案根目錄}
SCRIPTS=${SCRIPTS:?請設定 SCRIPTS=腳本目錄}
FAIL=0
pass() { echo "  ✅ $*"; }
fail() { echo "  ❌ $*"; FAIL=1; }
sect() { echo ""; echo "── $* ──────────────────────"; }

expect() {  # expect <期望 reachable: yes|no> <JSON 行> <說明>
    local want=$1 line=$2 desc=$3
    local got
    got=$(printf '%s' "$line" | python3 -c 'import json,sys; print("yes" if json.load(sys.stdin)["reachable"] else "no")')
    if [ "$got" = "$want" ]; then pass "$desc（實測 reachable=$got）"
    else fail "$desc（期望 $want，實測 $got）：$line"; fi
}

probe() {  # probe <pod> <targets...> -> 每行一筆 JSON
    local pod=$1; shift
    kubectl exec -n shadow "$pod" -- python3 /tmp/probe.py "$@" 2>/dev/null
}

# ---------------------------------------------------------------------------
sect "1. 真 API server 驗證（--dry-run=server，含 admission）"
# 這份 manifest 自帶 Namespace。server dry-run 不會真的建立 ns，後面的 namespaced
# 物件會因 "namespaces shadow not found" 而失敗——所以先單獨套用第一份 ns 文件。
awk '/^---/{exit} {print}' "$SRC/config/k8s/shadow-namespace.yaml" > /tmp/ns-only.yaml
kubectl apply -f /tmp/ns-only.yaml
kubectl apply --dry-run=server -f "$SRC/config/k8s/shadow-namespace.yaml"
if [ $? -eq 0 ]; then pass "shadow-namespace.yaml 通過 server dry-run（真 API server + admission）"
else fail "server dry-run 失敗"; fi

sect "2. 實際套用 PoC manifest 與測試周邊"
kubectl apply -f "$SRC/config/k8s/shadow-namespace.yaml"
kubectl apply -f "$SCRIPTS/testbed.yaml"

echo "等待 Pod 就緒…"
kubectl wait --for=condition=Ready pod/probe pod/probe-nolabel pod/shadow-db pod/wiremock \
    -n shadow --timeout=180s
kubectl wait --for=condition=Ready pod/prod-db -n prod --timeout=180s
kubectl wait --for=condition=Ready pod/loki -n observability --timeout=180s

ip() { kubectl get pod "$2" -n "$1" -o jsonpath='{.status.podIP}'; }
PROD_DB=$(ip prod prod-db)
LOKI=$(ip observability loki)
SHADOW_DB=$(ip shadow shadow-db)
WIREMOCK=$(ip shadow wiremock)
COREDNS=$(kubectl get pod -n kube-system -l k8s-app=kube-dns -o jsonpath='{.items[0].status.podIP}')
echo "prod-db=$PROD_DB loki=$LOKI shadow-db=$SHADOW_DB wiremock=$WIREMOCK coredns=$COREDNS"

# 一律用 Pod IP 直連，避開 DNS，讓「被擋」的原因只可能是 NetworkPolicy
TARGETS=(
    "正式DB=$PROD_DB:5432"
    "外部網際網路=1.1.1.1:443"
    "影子DB=$SHADOW_DB:5432"
    "wiremock=$WIREMOCK:8080"
    "可觀測性Loki=$LOKI:3100"
    "DNS=$COREDNS:53"
)

for p in probe probe-nolabel; do
    kubectl cp "$SCRIPTS/probe.py" "shadow/$p:/tmp/probe.py" >/dev/null 2>&1
done

# ---------------------------------------------------------------------------
sect "3. 對照組：先移除 NetworkPolicy，確認探針真的測得到連通"
kubectl delete networkpolicy --all -n shadow >/dev/null
sleep 3
CTRL=$(probe probe "${TARGETS[@]}")
echo "$CTRL"
expect yes "$(printf '%s\n' "$CTRL" | grep 正式DB)"       "無 policy 時：影子端可連正式 DB"
expect yes "$(printf '%s\n' "$CTRL" | grep 外部網際網路)" "無 policy 時：影子端可連外網"

# ---------------------------------------------------------------------------
sect "4. 套回 NetworkPolicy，實測 app=new-app 的白名單行為"
kubectl apply -f "$SRC/config/k8s/shadow-namespace.yaml" >/dev/null
sleep 5
R=$(probe probe "${TARGETS[@]}")
echo "$R"
expect no  "$(printf '%s\n' "$R" | grep 正式DB)"       "影子端連正式 DB 被擋（不可違反約束 #1）"
expect no  "$(printf '%s\n' "$R" | grep 外部網際網路)" "影子端連外網被擋（無 0.0.0.0/0 放行）"
expect yes "$(printf '%s\n' "$R" | grep 影子DB)"       "白名單：影子 DB 5432 可通"
expect yes "$(printf '%s\n' "$R" | grep wiremock)"     "白名單：wiremock 8080 可通"
expect yes "$(printf '%s\n' "$R" | grep Loki)"         "白名單：observability 3100 可通"
expect yes "$(printf '%s\n' "$R" | grep DNS)"          "白名單：kube-system DNS 53 可通"

sect "5. 預設拒絕：不帶 app=new-app 的 Pod 應該全部出不去"
R2=$(probe probe-nolabel "${TARGETS[@]}")
echo "$R2"
for t in 正式DB 外部網際網路 影子DB wiremock Loki DNS; do
    expect no "$(printf '%s\n' "$R2" | grep "$t")" "未標記 Pod：$t 被擋"
done

# ---------------------------------------------------------------------------
sect "6. ResourceQuota 實際生效"
cat <<'EOF' > /tmp/over-quota.yaml
apiVersion: v1
kind: Pod
metadata: { name: over-quota, namespace: shadow }
spec:
  containers:
  - name: c
    image: python:3.13-alpine
    command: ["sleep", "60"]
    resources:
      requests: { cpu: "20", memory: 40Gi }
      limits:   { cpu: "20", memory: 40Gi }
EOF
OUT=$(kubectl apply -f /tmp/over-quota.yaml 2>&1)
echo "$OUT" | head -3
if echo "$OUT" | grep -q "exceeded quota"; then
    pass "超額 Pod 被 ResourceQuota 擋下"
else
    fail "超額 Pod 未被擋：$OUT"
fi

OUT2=$(kubectl run no-resources --image=python:3.13-alpine -n shadow \
        --restart=Never --command -- sleep 60 2>&1)
if echo "$OUT2" | grep -qi "must specify"; then
    pass "未宣告 requests/limits 的 Pod 被 Quota 擋下（ADR-005 資源基準可強制）"
else
    fail "未宣告 requests/limits 的 Pod 竟被接受：$OUT2"
fi

# ---------------------------------------------------------------------------
sect "7. PoC Deployment 本身"
kubectl -n shadow get deploy new-app -o wide 2>&1 | tail -2
kubectl -n shadow get pods -l app=new-app --no-headers 2>&1 | head -4

echo ""
echo "════════ 隔離拓樸驗證總結 ════════"
if [ "$FAIL" -eq 0 ]; then echo "ISOLATION TOPOLOGY VERIFIED"; else echo "ISOLATION VERIFICATION FAILED"; fi
exit "$FAIL"
