# 隔離拓樸執行期驗證（檢核表 T-14）

`config/k8s/shadow-namespace.yaml` 的檔頭寫著「套用後務必執行檢核表 T-14 實際驗證，
不可只看設定檔就當作生效」。這個目錄就是那份實測工具。

## 為什麼需要這個

驗證強度有三層，一層比一層硬：

| 層級 | 手段 | 能證明什麼 | 擋不住什麼 |
|---|---|---|---|
| 靜態斷言 | 讀 YAML 字串（`verify/e2e_verify.py` 第 9 節） | 設定「有寫」 | 語法合法但目標程式載不進去；selector 寫錯而完全不生效 |
| Schema 驗證 | `kubeconform -strict` / `kubectl --dry-run=server` | API server 會接受 | NetworkPolicy 套上去卻沒擋任何封包 |
| **執行期強制** | **本目錄** | **封包真的被 drop** | 真實 CNI 差異、正式網路拓樸 |

第三層是唯一能證明「不可違反的約束 #1：影子端絕不可連到正式資源」真的成立的方式。

## 前置條件

需要一個**會執行 NetworkPolicy 的 CNI**。

> ⚠ kind 預設的 kindnet **不強制 NetworkPolicy**。直接沿用會讓所有政策「套上去卻完全
> 不擋」，而驗證腳本仍會顯示綠燈——那是最危險的假通過。必須換成 Calico 或 Cilium。

```bash
kind create cluster --config verify/k8s-isolation/kind-cluster.yaml
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.32.1/manifests/calico.yaml
kubectl -n kube-system rollout status ds/calico-node --timeout=300s
```

## 執行

```bash
SRC=<專案根目錄> SCRIPTS=verify/k8s-isolation \
    bash verify/k8s-isolation/isolation-verify.sh
```

任何一項不符預期即離開碼 1，可直接掛進 CI Gate。

## 驗證項目

1. **server dry-run** —— 真 API server + admission 驗證整份 manifest
2. **對照組**：先移除 NetworkPolicy，確認探針測得到連通
   （沒有這一步，「連不到」可能只是目標本來就不存在）
3. **白名單行為**（`app=new-app`）：正式 DB 與外網被擋；影子 DB 5432、
   wiremock 8080、observability 3100、kube-system DNS 53 可通
4. **預設拒絕**：不帶 `app=new-app` 的 Pod 連白名單目標也全部出不去
5. **ResourceQuota**：超額 Pod、未宣告 requests/limits 的 Pod 皆被擋

探針一律以 **Pod IP 直連**，不經 DNS —— 這樣「連不上」的原因只可能是
NetworkPolicy，不會被 DNS 解析失敗混淆。

## 檔案

| 檔案 | 用途 |
|---|---|
| `kind-cluster.yaml` | kind 叢集設定（停用 kindnet） |
| `testbed.yaml` | 周邊環境：`prod`（正式資源模擬）、`observability`、影子 DB / wiremock / 兩支探針 Pod |
| `probe.py` | Pod 內部的 TCP 可達性探針，逐筆輸出 JSON |
| `isolation-verify.sh` | 主流程與判定 |

`testbed.yaml` 只是驗證用的模擬環境，**不是 PoC 交付物**，不應套進任何實際環境。
