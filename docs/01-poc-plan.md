# 新舊應用平行測試 PoC 規劃書

**版本** v1.0
**日期** 2026-07-25
**拓撲** 舊應用（VM）↔ 新應用（容器 / K8s，無 Service Mesh）
**適用** 銀行、製造、零售等對交易正確性有高要求的核心系統汰換

---

## 1. 目的與範圍

### 1.1 目的

在**不影響正線**的前提下，以生產真實流量驗證新應用（容器化重寫版）與舊應用（VM 版）的：

1. **功能等價性** — 相同輸入是否產生語意相同的回應
2. **性能可承受性** — 新應用在 1x / 2x / 5x 生產負載下的容量與瓶頸

### 1.2 範圍內

- HTTP/HTTPS 同步 API（REST / JSON）
- 唯讀查詢 → 非核心寫入 → 核心交易，分階段推進

### 1.3 範圍外（需另行規劃）

| 項目 | 原因 |
|---|---|
| WebSocket / SSE | 長連線無法有意義地重放 |
| 大檔上傳 / 下載 | body buffer 成本高，且錄製檔膨脹失控 |
| 批次 / 排程作業 | 不走 HTTP，PoC 期間新應用一律關閉 scheduler |
| MQ 非同步流程 | 需獨立的訊息重放機制，建議第二期 |
| 前端 UI 行為 | 本 PoC 只驗 API 契約層 |

### 1.4 明確的非目標

**本 PoC 不產生「新應用可以上線」的結論**，只產生「在已驗證的流量樣本範圍內，新舊行為一致 / 不一致」的證據。切換決策另需納入營運、法遵、災備演練等面向。

---

## 2. 技術選型結論

### 2.1 候選方案比較

| 方案 | 鏡像流量 | 比例取樣 | 倍率放大 | 回應比對 | 對正線風險 |
|---|---|---|---|---|---|
| Nginx `mirror` | ✅ | ⚠️ 需 `split_clients` | ❌ | ❌ 回應丟棄 | 中 |
| OpenResty + Lua | ✅ | ✅ | ❌ | ✅ 自行實作 | 中 |
| Envoy `request_mirror_policies` | ✅ | ✅ `runtime_fraction` | ❌ | ❌ | 低（真非同步） |
| Istio VirtualService | ✅ | ✅ | ❌ | ❌ | — 本案無 mesh |
| APISIX / Kong `proxy-mirror` | ✅ | ✅ | ❌ | ❌ | 中 |
| Diffy | ✅ | ✅ | ❌ | ✅ 內建噪音消除 | 中 |
| **GoReplay** | 錄製+重放 | ✅ | ✅ | ✅ 離線 diff | **最低（零改動）** |

### 2.2 結論

| 用途 | 選用 | 理由 |
|---|---|---|
| **主方案 — 功能比對** | GoReplay 錄製 → 離線重放 | 不改動正線流量路徑、不新增故障點；同一份錄製檔可重複使用 |
| **備案 — 需即時鏡像時** | VM 側 Nginx/Envoy 影子閘道 | 當「必須即時發現差異」為硬需求時採用 |
| **性能驗證** | GoReplay 倍率重放 | mirror 無法放大倍率、無背壓控制，不適合容量驗證 |

> 詳見 ADR-001（流量取得方式）、ADR-002（閘道位置）

### 2.3 為什麼閘道不放 K8s 側

若把正線流量繞進 K8s 再導回 VM，等於為了測試把正線可用性押在尚未驗證的新叢集上。**銀行環境不採用此做法。**

---

## 3. 目標架構

### 3.1 主方案（GoReplay，建議起手）

```
                    ┌─────────────────────────────────┐
   正線流量  ───────▶│  Legacy App (VM)                │───▶ 正式 DB / 外部系統
                    │  + gor agent（監聽網卡，唯讀）   │
                    └──────────────┬──────────────────┘
                                   │ 錄製 .gor（含新舊回應）
                                   ▼
                    ┌─────────────────────────────────┐
                    │  sanitize-gor.py（個資遮蔽）     │
                    └──────────────┬──────────────────┘
                                   │ 離線重放
                                   ▼
   ┌───────────────────────────────────────────────────────────┐
   │  K8s namespace: shadow                                     │
   │                                                            │
   │   Ingress ──▶ New App (容器)  ──┬──▶ 影子 DB（CDC 副本）   │
   │                                 ├──▶ WireMock（外部 stub）  │
   │                                 └──▶ shadow.* Kafka topic   │
   │                                                            │
   │   NetworkPolicy: egress 禁止連正式 DB / 外部端點            │
   └───────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                    diff_engine.py ──▶ 一致率報告 + Gate 判定
```

### 3.2 備案（VM 側影子閘道，需即時鏡像時）

```
   既有 LB ──▶ shadow-gw VM ×2 ──┬──▶ Legacy VM pool（回應給 client）
              (Nginx / Envoy)    └──▶ K8s Ingress ──▶ New App（丟棄回應）
```

閘道以 Active/Standby 掛在既有 L4 VIP 後方。**timeout 必須壓在 connect 200ms / read 2s 以內**，否則新系統一慢會拖住主線。

---

## 4. 混合拓撲（VM → 容器）的特殊處理

這是本案與同質平行測試最大的差異，也是假陽性的主要來源。

### 4.1 網路層

| 項目 | 問題 | 處理 |
|---|---|---|
| **Host header** | Ingress 依 Host/SNI 選 backend，沿用原 Host 直接 404 | 重放時強制改寫 `Host` |
| **來源 IP** | 經 Service（iptables/IPVS）NAT 後，Pod 看到節點 IP | `externalTrafficPolicy: Local` 或依 `X-Forwarded-For`（Ingress 需開 `use-forwarded-headers`） |
| **XFF 鏈變長** | 多一層閘道後，取 client IP 的索引位置不同 → 大量假陽性 | W1 用 echo 端點驗證；新應用取值邏輯需與舊系統對齊 |
| **MTU** | Overlay（Calico/Flannel VXLAN 1450）< VM 網段 1500，大 body 可能黑洞 | `00-preflight.sh` 第 3 節驗證 |
| **TLS 終止點** | 舊在 F5 終止走 HTTP，新在 Ingress 終止 | 新應用若依 `X-Forwarded-Proto` 產生 redirect URL，Location header 會不同 |
| **DNS** | VM 網段需能解析 K8s 服務名 | CoreDNS 對外，或直接寫 LB VIP |

### 4.2 資料層 — CDC 延遲

影子 DB 若以 Debezium / GoldenGate 從正式庫單向同步，**同步延遲會造成餘額、狀態欄位的假陽性**。

處理順序：

1. W2 先量測 CDC lag 分布（P50/P95/P99）
2. Phase 1–2 對餘額類欄位設容忍窗口（`noise-profile.yaml` 的 `numeric_tolerance`）
3. **Phase 3 核心交易必須把容忍窗口歸零**，改以時間點還原的靜態副本比對

### 4.3 性能數字不可直接比 —— 最容易得出錯誤結論的地方

VM 與容器資源模型不同，**端到端 latency 對比沒有意義**。

| 差異來源 | 症狀 | 處理 |
|---|---|---|
| cgroup CFS 節流 | P99 週期性尖刺，與程式碼無關 | 監控 `container_cpu_cfs_throttled_seconds_total`；PoC 期間 limit 放寬，確認 throttle = 0 |
| JVM 容器感知 | 預設 heap 只取 1/4 記憶體，GC 頻繁 | 明確設 `-XX:MaxRAMPercentage=75`，確認 `ActiveProcessorCount` |
| 多一跳網路 | Ingress → Service → Pod 多 0.3–1ms | 改比「應用內部耗時」 |
| HPA / 冷啟動 | 重放初期 latency 異常高 | PoC 期間關閉 HPA，固定 replica |
| keepalive / TLS 握手 | 握手成本被算進 latency | 兩邊統一開長連線 |

**做法**：兩邊應用內埋同一組 timer（進入 controller → 回應完成），**以此為判準**。端到端數字另記，作為架構總成本參考，不作為新舊優劣依據。

**資源基準**：legacy VM 4C/8G ↔ 容器 `requests = limits = 4 CPU / 8Gi`（requests = limits 避免 burst 讓數字虛高）。

> 詳見 ADR-005

---

## 5. 副作用隔離（銀行場景的生死線）

| 層 | 措施 |
|---|---|
| **資料庫** | 新應用連影子 DB（正式庫還原副本或 CDC 同步）。**絕不共用正式庫** |
| **外部系統** | 收單、清算、核心主機、簡訊/Email 一律 WireMock / Hoverfly stub |
| **MQ / Kafka** | 發往 `shadow.` 前綴 topic，獨立 consumer group |
| **排程 / 批次** | PoC 期間全數關閉 |
| **應用層** | 收到 `X-Shadow-Request: true` 進 dry-run：跳過發送類動作、跳過冪等鍵檢查、log 標記 `shadow=1` |
| **網路層（技術性保險）** | NetworkPolicy 阻斷 `shadow` namespace egress 至正式 DB 與外部端點 |

> **不能只靠應用自律**。網路層阻斷是最後一道防線，且必須實際驗證（檢核表 T-14）。

> 詳見 ADR-003

---

## 6. Header 契約

### 6.1 必加

| Header | 值 | 用途 |
|---|---|---|
| `X-Correlation-Id` | `$request_id` / gor uuid | 兩邊同一組值，事後 diff 的配對鍵 |
| `X-Shadow-Request` | `true` | 新應用據此進 dry-run。**銀行場景最關鍵一條** |
| `X-Origin-Platform` | `vm` | 標示流量來源，便於日誌分流 |
| `Host` | 新應用的 Ingress vhost | 混合拓撲必改，否則 404 |
| `Accept-Encoding` | `identity` | 免解壓即可 diff body |
| `X-Forwarded-For` / `-Proto` / `X-Real-IP` | 原值鏈 | 新應用若有依 IP 或 scheme 的邏輯 |

### 6.2 必須處理的坑

| 項目 | 問題 | 處理 |
|---|---|---|
| **防重放** | `Idempotency-Key`、JWT `jti`、nonce、CSRF token 會被判定重複而拒絕 | 重放時改寫（加 `-shadow` 後綴），或 shadow 模式下開白名單 |
| **Tracing** | 沿用同一 `traceparent` 會讓 APM 兩條 trace 互相污染 | 產生新 trace + span link 關聯，或 `tracestate` 標記 `shadow=1` |
| **Authorization** | 新系統若為獨立 auth realm，token 不通用 | 先做 token exchange，或 shadow 模式放行 |

> 詳見 ADR-006

---

## 7. 比對方法

### 7.1 噪音消除（Phase 0，成敗關鍵）

**先跑「舊系統 vs 舊系統的第二份實例」**，凡是仍然不同的欄位，依定義就是噪音而非行為差異。

```
gor 錄製 ──▶ 重放至 legacy 第二實例 ──▶ diff_engine.py baseline
                                          │
                                          ├─▶ noise-profile.generated.yaml（自動候選）
                                          └─▶ *.review.json（需人工複核）
```

**自動產生的 ignore_paths 一律不得直接套用。** 自動學習會把「因排序不穩定而每次都不同的金額欄位」也列為候選 —— 那正是真實缺陷會躲藏的地方。每一條都需簽核。

常見噪音來源：時間戳、UUID、流水號、主機名（容器是 Pod name）、版本號、JSON array 順序、快取命中差異、CDC lag。

**Gate：假陽性率 < 1%** 才可進入 Phase 1。

### 7.2 比對維度

| 維度 | 說明 |
|---|---|
| HTTP status | 不一致即記為 `STATUS_MISMATCH` |
| 業務錯誤碼 | 新應用不得出現舊系統沒有的 code |
| Response body | JSON 正規化後深度比對；非 JSON 走遮蔽後文字比對 |
| 指定 header | 白名單比對（`content-type`、`cache-control` 等） |
| Latency | 端到端僅供參考，判準用應用內部 timer |

> 詳見 ADR-004

---

## 8. 環境清單

### 8.1 VM 側（舊應用）

- `gor` agent 安裝於既有 legacy VM（CPU 佔用需實測，經驗值 3–8%，**上線前必須在壓測環境量過**）
- 錄製檔儲存：`平均 payload × TPS × 取樣率 × 時長`
  - 例：5KB × 200 TPS × 5% × 3600s ≈ **180 MB/hr**
  - 磁碟目錄 700 權限、加密、設定保留天數
- （備案）shadow-gw VM ×2，4C/8G 起

### 8.2 K8s 側（新應用）

| 元件 | 說明 |
|---|---|
| namespace `shadow` | 獨立 ResourceQuota，避免影子端搶正線資源 |
| new-app Deployment | `APP_MODE=shadow`；固定 replica，關閉 HPA |
| 影子 DB | StatefulSet 或外部 RDS 副本 |
| WireMock Deployment | 外部系統 stub |
| NetworkPolicy | egress 禁止正式 DB / 外部端點 |
| Fluent Bit DaemonSet | → Loki / MinIO |
| （備案）Envoy Deployment ×2 | 影子閘道 + ConfigMap 控制鏡像比例 |

---

## 9. 階段規劃與 Gate

| 週 | 階段 | 內容 | Gate（未過則停在該階段） |
|---|---|---|---|
| **W1** | 環境建置 + 跨界驗證 | 影子 DB、stub、log 管線、`00-preflight.sh` | MTU / DNS / TLS / Host / XFF 全數通過；手動打 10 筆走通全鏈路 |
| **W2** | Phase 0 噪音基線 | 舊 vs 舊，建立 ignore_paths | **假陽性率 < 1%**；CDC lag 分布已量測 |
| **W3** | Phase 1 唯讀 | GET 類 API，10% → 50% → 100% | 一致率 ≥ **99.9%**；正線 P99 增幅 < 2% |
| **W4** | Phase 2 非核心寫入 | 查詢紀錄、偏好設定類 | 一致率 ≥ **99.95%**；CDC lag < 1s |
| **W5** | Phase 3 核心交易 | 轉帳、扣款等，10% 起 | 一致率 = **100%**；無資料外洩；容忍窗口已歸零 |
| **W6** | Phase 4 性能 | 1x / 2x / 5x 倍率重放 | 正規化後應用內部耗時 P99 ≤ VM × 1.1；CFS throttle = 0；錯誤率無上升 |

**每個 Gate 未過就停，不往下推。** 一致率達標仍須人工抽樣複核 30 筆，確認不是被 noise profile 過度抑制。

---

## 10. 風險與緩解

| # | 風險 | 影響 | 緩解 |
|---|---|---|---|
| R1 | 副作用外洩至正式環境（重複記帳、重複發送） | **極高** | 影子 DB + stub + NetworkPolicy 三層；T-14 實際驗證 |
| R2 | 錄製檔含個資落地 | 高（法遵） | `sanitize-gor.py` 一致性雜湊遮蔽；保留天數；目錄加密；法遵事前核准 |
| R3 | gor agent 佔用正線 CPU | 中 | 壓測環境先量測；取樣率從 5% 起；設 CPU cgroup 上限 |
| R4 | 噪音導致誤判（假陽性淹沒真缺陷） | 高 | Phase 0 必做；ignore_paths 逐條簽核；人工抽樣複核 |
| R5 | noise profile 過度抑制真缺陷 | **高（最隱蔽）** | 自動候選不得直接套用；每階段抽樣複核；核心交易容忍窗口歸零 |
| R6 | 性能結論錯誤（誤判「容器比較慢」） | 中 | 以應用內部 timer 為判準；資源基準對齊；CFS/JVM 參數先校正 |
| R7 | 影子端拖累正線（僅備案適用） | 高 | 短 timeout；Envoy 非同步；Nginx 版需壓測驗證 |
| R8 | CDC lag 造成資料面假陽性 | 中 | W2 量測分布；容忍窗口；Phase 3 改用靜態時間點副本 |

### 回退機制

| 方案 | 回退動作 | 生效時間 |
|---|---|---|
| GoReplay | 停止 agent（`systemctl stop gor-capture`） | 立即，正線無感 |
| Envoy 閘道 | ConfigMap 熱更新 `shadow.mirror_fraction` = 0 | < 30s |
| Nginx 閘道 | `mirror` 指向 `/dev/null_mirror` + `nginx -s reload` | < 30s |

---

## 11. 交付物

| 類別 | 項目 |
|---|---|
| 文件 | 本規劃書、ADR-001~007、工作檢核表 |
| 設定 | `config/vm/nginx-shadow.conf`、`config/k8s/envoy-shadow.yaml`、NetworkPolicy、Deployment |
| 腳本 | `00-preflight.sh`、`01-record.sh`、`02-replay-functional.sh`、`03-replay-perf.sh`、`04-run-diff.sh`、`sanitize-gor.py` |
| 工具 | `diff_engine.py`（含 `compare` / `baseline` 子命令）、`noise-profile.yaml`、`selftest.py` |
| 報告 | 各 Phase 一致率報告（Markdown + JSON）、性能容量曲線、最終建議書 |

---

## 12. 決策記錄索引

| ADR | 主題 |
|---|---|
| ADR-001 | 流量取得方式：錄製重放 vs 即時鏡像 |
| ADR-002 | 影子閘道部署位置：VM 側 vs K8s 側 |
| ADR-003 | 副作用與資料隔離策略 |
| ADR-004 | 噪音基線與比對策略 |
| ADR-005 | 性能指標正規化 |
| ADR-006 | Shadow 流量 Header 契約 |
| ADR-007 | 錄製檔個資遮蔽與保留政策 |
