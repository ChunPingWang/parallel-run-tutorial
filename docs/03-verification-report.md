# 平行測試 PoC 套件驗證報告

**驗證方法的完整說明在 `README.md` 的「驗證」一節**（含名詞速查、分層理由、
逐項指令與如何讀輸出）。本檔是**帶日期的結果記錄**，供日後回溯。

---

## 1. 兩次驗證的範圍差異

| | 2026-07-25 | 2026-07-26 |
|---|---|---|
| 環境 | macOS（Darwin 25.4.0）、Python 3.14 + PyYAML 6.0.3 | WSL2 / Fedora 44、Python 3.14.6 + PyYAML 6.0.3 |
| L1 靜態 | ✅ | ✅ |
| L2 `selftest.py` | ✅ | ✅ |
| L3 `e2e_verify.py` | ✅ 49/49 | ✅ **60/60**（+11 項回歸檢查） |
| L4 容器實測（真 Nginx / Envoy / gor） | ❌ 未做（本機無二進位） | ✅ **做了 —— 抓出 12 項缺陷** |
| T-14 egress 隔離實測 | ❌ 無叢集 | ✅ **kind 實測 12/12，兩種 CNI 各驗一次** |

**7-25 的結論「49/49 全通過」在其範圍內成立，但範圍不足**：它完全沒有驗證
Nginx / Envoy / gor 本體。7-26 補上容器層後，在前三層全綠的情況下仍抓出
12 項缺陷，其中 6 項會讓對應元件在真實環境直接不可用或靜默失效 ——
包含一項會讓**正線的正式交易靜默不執行副作用**（#11）。

這是本次驗證最重要的一課：**「模擬所有外部元件」的端到端測試，無法驗證那些
被模擬掉的元件。** 分層必須包含真二進位。

---

## 2. 結論（2026-07-26）

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
| `verify/e2e_verify.py` | 60/60；300 筆樣本一致率 100%；注入 8 筆缺陷 divergent=8（無漏檢無誤報） |
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

### 3.4 T-14 egress 隔離（12 項，兩種 CNI 各跑一次）

| CNI | 版本 | 結果 |
|---|---|---|
| kindnet（kind 預設）| `kindest/kindnetd:v20260528` | 12/12 —— 現版已實作 NetworkPolicy |
| Calico | v3.32.1（`disableDefaultCNI: true` 後裝）| 12/12 |

驗證內容：白名單放行（影子 DB 5432、WireMock 8080、DNS）全通；未列白名單的
同 namespace Pod、白名單 Pod 的非放行埠、外部網路全擋；`app=rogue` 這種
不符任何放行規則的 Pod 連 DNS 都不通（`default-deny-egress` 涵蓋所有 Pod）。
每個「應被擋」項目都有 shadow namespace 外的對照組，以排除「本來就不通」。

工具已收進 repo：`verify/t14-networkpolicy.sh`、`verify/t14-test-pods.yaml`。

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
| `gor --input-raw` 實際錄製 | 需 `CAP_NET_RAW`，`wslc run` 無此開關；改以應用層產生等價 `.gor` | T-10 |
| 影子線 TLS（`proxy_ssl_*`、SNI、內部 CA）| 測試環境未架內部 CA，實測時移除該四行 | R7 |
| Envoy `runtime_key` 熱調與緊急回退 | 只驗證欄位存在與 100% 生效，未實測熱調過程 | R7 |
| K8s Ingress 依 Host/SNI 選 backend 的實際行為 | 以「影子端檢查收到的 Host」代替真 Ingress | T-12 |
| CDC 實際延遲分布、gor CPU 佔用、正線 P99 增幅 | 需真實環境與真實負載 | T-11、T-21、T-24 |

---

## 7. 其他觀察

1. `04-run-diff.sh` 報告檔名為秒級時間戳，同一秒重跑會互相覆蓋。自動化批次
   呼叫時請自行指定 `REPORT_DIR`（`verify/e2e_verify.py` 即如此處理）。
2. `sanitize-gor.py` 的 PAN 規則（13–19 碼）會先於 ACCOUNT 規則吃掉 16 碼帳號，
   遮蔽前綴為 `PAN_` —— 行為一致故不影響比對，但報表命中統計會偏向 PAN，
   校準規則時需留意。
3. 套件需 PyYAML；Python 3.9 / 3.14 皆未內建，驗證時以 venv 安裝。
4. `docs/build-deck.js` 需 Node（本次 WSL 環境無 Node，未重跑）。7-25 於 macOS
   實際執行成功，產出與 `dist/poc-exec-deck.pptx` 位元組數一致（346,266 bytes）。
