# 平行測試 PoC 套件驗證報告

**驗證方法的完整說明在 `README.md` 的「驗證」一節**（含名詞速查、分層理由、
逐項指令與如何讀輸出）。本檔是**帶日期的結果記錄**，供日後回溯。

**環境**

- 第一輪（2026-07-25）：macOS（Darwin 25.4.0）、Python 3.14 + PyYAML 6.0.3、
  kubectl（無叢集）、Node 26
- 第二輪（2026-07-26）：WSL2 容器（wslc 2.9.4）內 Ubuntu 22.04 + Envoy v1.31
  + nginx 1.18 + kubeconform v0.6.7 + Python 3.6；隔離拓樸另在 FedoraLinux-44
  的 kind 叢集（k8s v1.36.1 + Calico v3.32.1，2 節點）實測
- 第三輪（2026-07-26～27）：WSL2 / Fedora 44 原生 + wslc 容器，補上閘道與
  GoReplay 的**執行期**行為（打真流量、量正線副作用），以及示範影片工具鏈

**驗證方式** 靜態檢查 + `diff/selftest.py` + `verify/e2e_verify.py` 本機端到端
模擬 + 設定檔真二進位載入 + 閘道執行期打流量 + K8s 叢集內隔離拓樸實測

---

## 1. 三輪驗證的範圍差異

| | 第一輪 07-25 | 第二輪 07-26 | 第三輪 07-26～27 |
|---|---|---|---|
| L1 靜態 | ✅ | ✅ | ✅ |
| L2 `selftest.py` | ✅ | ✅ | ✅ |
| L3 `e2e_verify.py` | ✅ 49/49 | ✅ 53/53 | ✅ **62/62** |
| 設定檔真二進位載入 | ❌ 無二進位 | ✅ 抓出 2 項缺陷 | ✅ |
| 閘道**執行期**打流量 | ❌ | ❌ | ✅ **抓出 12 項缺陷** |
| T-14 egress 隔離實測 | ❌ 無叢集 | ✅ 17/17 | ✅ |

每一輪都是在**前一輪全綠**的情況下，靠加深驗證強度而抓出新缺陷：

- 第二輪把設定檔檢查從「讀字串斷言」升級為「用目標程式實際載入」，
  抓出 Envoy 的兩個載入錯誤（見 3.9）。
- 第三輪再往前一步 —— **實際起閘道打流量，並量測正線的副作用** ——
  又抓出 12 項，其中 6 項會讓對應元件在真實環境直接不可用或靜默失效。

其中最嚴重的一項會讓**正線的正式交易靜默不執行副作用**（#11）。

這三輪最重要的一課：**「模擬所有外部元件」的端到端測試，無法驗證那些被模擬掉
的元件**；而「設定能載入」也不等於「行為正確」。驗證分層必須一路做到真二進位
打真流量，並且**同時量測正線**，否則只會看到影子端一切正常。

---

## 2. 結論（2026-07-27）

- 比對引擎與噪音抑制機制**成立**：真 gor 重放 60 筆，經專案 diff 引擎比對
  得 59/59 一致（一致率 100%），噪音全數抑制、Gate 全過。
- 副作用隔離（ADR-003）**成立**：真 nginx mirror 下，正線 20 筆簡訊照發、
  影子端 0 筆；真 gor 重放 30 筆轉帳，影子端 0 筆。
- header 契約（ADR-006）**成立**：真 nginx、真 Envoy、真 gor 三條路徑都正確套上
  Host 改寫、`X-Shadow-Request`、`X-Origin-Platform`、`Accept-Encoding: identity`、
  `Idempotency-Key` 後綴、`traceparent` 清空，零違規；且**只作用於鏡像流量**，
  正線副作用照常發生（修正 #11 後實測正線簡訊 15/15）。
- 影子端 egress 隔離（T-14，Gate 阻擋項）**成立**：白名單放行的通、未列白名單
  的 Pod／埠／外網全數擋下，且有對照組排除「本來就不通」。
- 但**設定檔與腳本原本有 12 項缺陷**，已全部修正並重驗（見第 4 節）。

---

## 3. 關鍵數據

### 3.1 L2 / L3

| 項目 | 結果 |
|---|---|
| `diff/selftest.py` | 4 筆注入缺陷全偵測、196 筆噪音全抑制、Gate 未過離開碼 1 |
| `verify/e2e_verify.py` | 62/62；300 筆樣本一致率 100%；注入 8 筆缺陷 divergent=8（無漏檢無誤報） |
| Phase 0 基線 | 無 profile 假陽性 100% → Gate 擋下；套簽核 profile 後 0.000% |
| 不可違反約束 #3 | `baseline` 未自動改寫 `diff/noise-profile.yaml`，僅產生候選檔 ✅ |
| Phase 3 容忍歸零 | 0.5 元 CDC 落差在 Phase 1（abs=1.0）被容忍、Phase 3 全數偵測（150/150）|

### 3.2 L4 容器實測

| 項目 | 結果 |
|---|---|
| Nginx 實載 | 1.27.5 `-t` 通過；`ngx_http_mirror_module` 已編入 |
| Envoy 實載（靜態）| v1.31.10 `--mode validate` 通過、警告 0（修正前無法載入）|
| Envoy 執行期契約（30 筆）| 修正前 8/11；修正後 **11/11**，與 Nginx 路徑一致 |
| 鏡像契約（40 筆 ×2 輪穩態）| 11/11 通過；影子端 40/40、header 零違規、簡訊 0 |
| 鏡像比例 `split_clients 10%` | 20/200 = 10.0%（另 18/200 = 9.0%）|
| 真 gor 重放 | 影子端 60/60、契約零違規；輸出 type1=60 / type2=59 / type3=60 |
| 真 gor → 專案 diff 引擎 | 59/59 一致（100%），Gate 全過，離開碼 0 |
| K8s manifest schema | kubeconform `-strict` 5/5 valid |

### 3.3 主線延遲：影子端會不會拖累正線（各 30 筆）

| 設定 | median | p95 | max | 影子端收到 |
|---|---|---|---|---|
| A 鏡像關閉（基準）| 1.7ms | 44.2ms | 44.6ms | 0 |
| B 鏡像 100% + 影子端健康 | 1.3ms | 47.8ms | 48.1ms | 24/30 |
| C 鏡像 100% + 影子端延遲 5s | 1.5ms | 44.2ms | 44.2ms | 30/30 |

**影子端延遲 5s 對主線無可測影響。** CLAUDE.md 約束 #4 原本的理由
（「主連線在子請求結束前不會釋放」）不成立；結論（timeout 不得放寬）不變，
但理由已改寫為資源佔用（子請求綁住 worker 與 upstream 連線）。

### 3.4 缺陷偵測

注入 8 筆真實缺陷（GET 金額 -1 且回 500 ×6、POST 手續費截斷 ×2）：

- divergent = 8，恰等於注入數（無漏檢、無誤報）✅
- 分類正確：`STATUS_MISMATCH` ×6、`availableBalance` 與 `fee` 路徑上榜 ✅
- 候選端新增 status code（500）被標記 ✅
- Phase 1 Gate 擋下，離開碼 1 ✅

### 3.5 Phase 3 容忍歸零（ADR-005，T-30）

模擬 CDC 同步落差（餘額 -0.5）：

- Phase 1 + `abs: 1.0` 容忍 → 一致率 100%，Gate 通過（合理容忍）✅
- Phase 3 + 容忍歸零 → 150 筆 GET 全數偵測，Gate 擋下 ✅

### 3.6 解析器邊界

gzip 回應解壓、chunked 回應重組皆正確 ✅

### 3.7 設定檔（不可違反約束 #1 / #4）

- NetworkPolicy：預設拒絕 egress、放行清單皆 namespace/pod 白名單、
  無 `ipBlock`、無 `0.0.0.0/0` ✅
- Deployment：`APP_MODE=shadow`、scheduler 關閉、requests = limits、
  Kafka `shadow.` 前綴 ✅
- Nginx：影子 timeout 維持 connect 200ms / read 2s、不重試、
  location internal、trace 隔離 ✅
- Envoy：影子 cluster connect ≤ 200ms（Duration 格式合法）、`max_retries: 0`、
  關閉鏡像 Host 的 `-shadow` 後綴、`runtime_key` 熱調 ✅

### 3.8 簡報腳本

`docs/build-deck.js` 以 pptxgenjs 實際執行成功，產出檔與
`dist/poc-exec-deck.pptx` 位元組數完全一致（346,266 bytes），產物可重現。

### 3.9 設定檔真二進位驗證（新增，R7）

第一輪的設定檔檢查全部是「讀檔比對字串」。第二輪改由目標程式實際載入，
結果如下：

| 設定 | 方法 | 結果 |
|---|---|---|
| `config/k8s/shadow-namespace.yaml` | `kubeconform -strict`（k8s 1.31 schema） | ✅ 5 份資源全數 valid |
| `config/vm/nginx-shadow.conf` | nginx 1.18 `nginx -t` 實際載入 | ✅ 通過 |
| `config/k8s/envoy-shadow.yaml` | Envoy v1.31 `--mode validate` | ❌ → 修正後 ✅ |

**Envoy 設定原本載入失敗，兩處真實缺陷：**

1. `connect_timeout: 200ms` —— Envoy 用 protobuf Duration，只接受秒為單位的
   寫法，`200ms` 讓整份 bootstrap 解析失敗。已改為等值的 `0.2s`
   （**數值未放寬**，仍是 200ms）。Nginx 的 `200ms` 是合法的，此坑僅限 Envoy。
2. `host_rewrite_literal` 被寫在 Cluster 上 —— Cluster 沒有這個欄位
   （`no such field`）。原始意圖（覆寫 Envoy 自動加的 `-shadow` Host 後綴）
   的正確做法是在 mirror policy 上設 `disable_shadow_host_suffix_append: true`，
   已改用該欄位並實測通過驗證。

**為什麼第一輪沒抓到：** `verify/e2e_verify.py` 當時斷言的是字串
`connect_timeout == "200ms"`，正是這條斷言讓一份 Envoy 根本載不進去的設定拿到
✅。斷言已改為解析數值後比對上限，並新增第 10 節在有二進位時實際載入驗證。

反向測試皆已確認驗證會咬人：kubeconform 對注入的未知欄位判 invalid；
nginx 對非法 `proxy_connect_timeout` 值報 emerg。

### 3.10 隔離拓樸執行期驗證（T-14，不可違反的約束 #1）

在 kind + Calico 的 2 節點叢集實測，工具見 `verify/k8s-isolation/`：

| 情境 | 期望 | 實測 |
|---|---|---|
| **對照組**（先移除 NetworkPolicy） | 可連正式 DB、可連外網 | ✅ 兩者皆通 |
| `app=new-app` → 正式 DB 5432 | 被擋 | ✅ 逾時 |
| `app=new-app` → 外部網際網路 1.1.1.1:443 | 被擋 | ✅ 逾時 |
| `app=new-app` → 影子 DB / wiremock / observability / DNS | 可通 | ✅ 四項皆通 |
| 未帶 `app=new-app` 的 Pod → 全部目標 | 全擋（含 DNS） | ✅ 六項皆逾時 |
| 超額 Pod（20 CPU / 40Gi） | 被 ResourceQuota 擋 | ✅ `exceeded quota` |
| 未宣告 requests/limits 的 Pod | 被擋 | ✅ `must specify` |
| 整份 manifest `--dry-run=server` | 通過 | ✅ |

方法上的兩個要點：

1. **對照組不可省。** 沒有「移除 policy 後確實連得到」這一步，「連不到」可能只是
   目標本來就不存在，綠燈毫無意義。
2. **探針一律用 Pod IP 直連、不經 DNS。** 否則 DNS 被擋時所有目標都會失敗，
   無法分辨是 NetworkPolicy 生效還是名稱解析失敗。

**必要前提：CNI 必須真的執行 NetworkPolicy。** 本次以
`disableDefaultCNI: true` 建立叢集並改裝 Calico v3.32.1，17 項全數符合預期。

補充一則實測更正：**kind v0.33 的預設 CNI（kindnetd `v20260528`）已經會執行
NetworkPolicy** —— 同一份 policy 在它上面也擋得住。較早的 kindnetd 確實不執行，
這正是「必須實測而非假設」的理由：換一個叢集、換一版 CNI 結果就可能不同。
若本工具回報「阻斷」項**全數**失敗（實測 reachable=yes），第一個要懷疑的就是 CNI。

---

## 4. 發現的缺陷與修正

明細（含證據字串與修法）見 README「發現的缺陷」表。摘要：

（依檔案分組，編號沿用 README 的缺陷編號）

| # | 檔案 | 嚴重度 | 摘要 | 狀態 |
|---|---|---|---|---|
| 1 | `envoy-shadow.yaml` | 高 | `200ms` 不是合法 Duration，整份 bootstrap 解析失敗 | ✅ 改 `0.2s` |
| 2 | `envoy-shadow.yaml` | 高 | `host_rewrite_literal` 不是 Cluster 欄位 | ✅ 移除 |
| 3 | `envoy-shadow.yaml` | 高 | 原寫法達不到「覆寫 `-shadow` Host 後綴」的宣稱目的 | ✅ 改用 internal listener + `disable_shadow_host_suffix_append` |
| 4 | `envoy-shadow.yaml` | 低 | deprecated `json_format`、未設 `internal_address_config` | ✅ 修正，警告 0 |
| 11 | `envoy-shadow.yaml` | **最高** | 影子 header 加在主路由，**正線正式交易也進 dry-run**（副作用靜默不執行）。設定合法、影子端全正常，只有量測正線才發現 | ✅ 移到 internal listener 路由 |
| 12 | `envoy-shadow.yaml` | 中 | Envoy 路徑未實作 ADR-006 的 `Idempotency-Key` 後綴與 `traceparent` 清空，與 Nginx 路徑契約不一致 | ✅ 補上 |
| 5 | `nginx-shadow.conf` | **最高** | `mirror $var;` 導致鏡像完全不生效，且 `nginx -t` 通過、log 顯示命中 | ✅ 改靜態 URI + 比例判斷移入 location |
| 6 | `CLAUDE.md` #4 | 中 | timeout 不得放寬的**理由**錯誤，可能被誤用來論證放寬 | ✅ 改寫理由 |
| 7 | `01-record.sh` | 高 | 2 個 flag 在 gor 1.3.3 不存在，錄製會立即失敗 | ✅ 改正名稱 |
| 8 | `02-replay-functional.sh` | 中 | gor 輸出檔名含 chunk 序號，下一步指令指向不存在的檔案 | ✅ 改取實際檔名 |
| 9 | `04-run-diff.sh` | 中 | 預設拿端到端 latency 擋 Gate，與 ADR-005 衝突 | ✅ `LATENCY_RATIO` 預設 0（僅參考）|
| 10 | `04-run-diff.sh` | 中 | 引擎崩潰被誤報為「Gate 未通過」 | ✅ 新增離開碼 2，訊息分離 |

修正後為防止復發，`verify/e2e_verify.py` 新增 11 項回歸檢查（49 → 60），
包含「mirror 目標必須是靜態 URI」「Envoy 時間欄位必須是合法 Duration」
「Cluster 上不得出現 `host_rewrite_literal`」「主路由不得出現影子 header」等，
都是這次真的踩到的坑。

---

## 5. 觀察到但無法穩定重現

1. **影子子請求靜默逾時損失**：閘道啟動後第一輪固定掉 3–6 筆（分屬不同
   nginx worker、同一秒、卡在 `while sending to client` 滿 2s），之後穩態 0。
   因 `proxy_next_upstream off` 不重試，只在 error log 留痕。
2. **真 gor 檔案輸出掉訊息**：輸入 60 個 type-2、輸出 59 個（1/120）。

兩者造成的是「樣本缺漏」而非「判定錯誤」，但真實環境**必須對帳**：比較
錄製筆數與影子端實收筆數，差異超過門檻要當資料品質問題處理，不可默默用
少掉的樣本算一致率。

---

## 6. 仍未能實測

| 項目 | 原因 | 對應檢核 |
|---|---|---|
| `gor --input-raw` 實際錄製 | 需 `CAP_NET_RAW`，`wslc run` 無此開關。**重放已用真 gor 1.3.3 實測**，錄製僅驗證 flag 組合 | T-10 |
| 正式環境 CNI 下的 NetworkPolicy 行為 | 已在 kind + Calico 實測生效（3.10）；正式叢集的 CNI、既有政策與網段仍需複驗 | T-14 |
| VM 側實際拓樸（L4 VIP、shadow-gw、真實 Ingress） | 模擬環境無 VM 與 Ingress；Host 路由以「影子端檢查收到的 Host」代替 | T-12 |
| 影子線 TLS（`proxy_ssl_*`、SNI、內部 CA）| 測試環境未架內部 CA，實測時移除該四行 | R7 |
| Envoy `runtime_key` 熱調與緊急回退 | 只驗證欄位存在與 100% 生效，未實測熱調過程 | R7 |
| CDC 實際延遲分布、gor CPU 佔用、正線 P99 增幅 | 需真實環境與真實負載 | T-11、T-21、T-24 |

---

## 7. 其他觀察

1. `04-run-diff.sh` 報告檔名為秒級時間戳，同一秒重跑會互相覆蓋。自動化批次
   呼叫時請自行指定 `REPORT_DIR`（`verify/e2e_verify.py` 即如此處理）。
2. `sanitize-gor.py` 的 PAN 規則（13–19 碼）會先於 ACCOUNT 規則吃掉 16 碼帳號，
   遮蔽前綴為 `PAN_` —— 行為一致故不影響比對，但報表命中統計歸類會偏向 PAN，
   校準規則時（CLAUDE.md 已知限制）需留意。
3. 套件需 PyYAML；本機 Python 3.7 / 3.14 皆未內建，README 依賴一節已列明，
   驗證時以 venv 安裝。
4. **字串斷言會製造假通過。** 3.9 的 Envoy 缺陷是被自己的斷言掩護住的：
   斷言比對 `"200ms"` 這個字串，而該值正是讓 Envoy 載入失敗的原因。
   凡是「設定內容」的檢查，只要目標程式提供驗證模式（`nginx -t`、
   `envoy --mode validate`、`kubeconform`），就應優先用它取代字串比對。
5. **驗證一定要有對照組。** 3.10 先移除 NetworkPolicy 確認連得到、再套回確認被擋；
   缺了前半段，「被擋」與「目標根本不存在」在報告上長得一模一樣。

6. `docs/build-deck.js` 需 Node。第三輪的 WSL 環境無 Node，未重跑；
   7-25 於 macOS 執行成功，產出與 `dist/poc-exec-deck.pptx` 位元組數
   一致（346,266 bytes），產物可重現。

## 8. 重跑方式

### 8.1 主套件

```bash
python3 -m venv .venv && .venv/bin/pip install pyyaml
.venv/bin/python verify/e2e_verify.py --out-json tmp-verify/results.json
cd diff && ../.venv/bin/python selftest.py
```

`verify/e2e_verify.py` 第 10 節會在偵測到 `envoy` / `kubeconform` / `nginx`
時實際載入設定驗證；未安裝則記為略過。要讓這三項真的執行，可用容器：

```bash
# 映像需含 envoy + nginx + kubeconform + python3(PyYAML)
wslc run --rm -v "$PWD:/src:ro" <image> python3 /src/verify/e2e_verify.py
```

### 8.2 隔離拓樸（T-14）

見 `verify/k8s-isolation/README.md`。需要一個會執行 NetworkPolicy 的 CNI
（kind 預設的 kindnet 不會，須改裝 Calico 或 Cilium）。
