# CLAUDE.md

給 Claude Code 的專案指引。動手前請先讀 `docs/01-poc-plan.md` 與相關 ADR。

## 專案性質

新舊應用平行測試 PoC。**舊應用在 VM、新應用在容器（K8s，無 Service Mesh）**，
驗證兩者功能等價性與新應用的性能容量。適用場景為銀行核心交易系統汰換。

主方案是 GoReplay 錄製重放（正線零改動）；即時鏡像為備案。

## 語言慣例

- 文件、註解、CLI 輸出訊息：**繁體中文（zh-TW）**
- 程式碼識別項（變數、函式、類別、檔名）：**全英文**
- 設定檔的 key 沿用上游規格原文，說明用中文註解

## 不可違反的約束

修改任何檔案前先確認不會破壞以下任一項：

1. **影子端絕不可連到正式資源。** `config/k8s/shadow-namespace.yaml` 的
   NetworkPolicy 採預設拒絕 egress + 白名單放行。**不得改為黑名單模式，
   不得加入 `0.0.0.0/0` 放行。**
2. **`X-Shadow-Request: true` 是 dry-run 的觸發條件。** 修改 header 處理邏輯時
   必須同步檢查 `config/vm/nginx-shadow.conf`、`config/k8s/envoy-shadow.yaml`、
   `scripts/02-replay-functional.sh` 三處。
   **影子 header 絕不可套到主線請求上。** 在 Envoy 裡它必須掛在
   `shadow_host_rewrite`（internal listener）的路由上；掛在主路由會讓
   `request_headers_to_add` 同時作用於主線，正式交易被舊應用當成影子請求而
   進入 dry-run —— 簡訊、外部呼叫、寫檔全部靜默不執行。這個錯誤實際發生過，
   且只有實跑流量才看得出來（設定能正常載入、影子端行為也正確）。
3. **noise profile 的 ignore_paths 不得由程式自動套用。** `baseline` 子命令
   只產生候選檔，套用需人工簽核。若被要求「自動化這一步」，先說明風險（ADR-004）
   而不是直接實作。
4. **Nginx 影子 location 的 timeout 不得放寬。** connect 200ms / read 2s。
   理由是**資源佔用**：子請求會綁住 worker 與 upstream 連線，timeout 放長會在
   影子端變慢時累積連線，最終才傷到正線。
   （已實測修正：主線延遲**不會**因影子端變慢而增加 —— 影子端刻意延遲 5s 時，
   主線延遲與關閉鏡像無可測差異。所以不要再用「主連線等子請求完成」當理由，
   那是錯的；但結論不變，timeout 仍不得放寬。）
5. **`mirror` 指令不支援變數。** 寫成 `mirror $var;` 時 nginx 會把 `$var` 當
   字面 URI，鏡像**完全不生效**且 `nginx -t` 照樣通過、access log 照樣顯示
   命中 —— 是會靜默失效的錯。mirror 目標必須是靜態 URI，比例判斷放在
   `location = /shadow` 內。
6. **性能比較以應用內部 timer 為判準**，不是端到端 latency（ADR-005）。
   `04-run-diff.sh` 的 `LATENCY_RATIO` 預設為 0（僅列參考值、不擋關），
   **不得改為預設擋關**。若被要求「拿 latency 直接比」，先說明歸因錯誤的風險。

## 常用指令

```bash
# 驗證比對引擎（改動 diff/ 之後必跑）
cd diff && python3 selftest.py

# 語法檢查
bash -n scripts/*.sh
python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" \
    diff/noise-profile.yaml config/k8s/envoy-shadow.yaml

# 比對（PHASE 決定 Gate 門檻：1=99.9% 2=99.95% 3=100%）
PHASE=1 ./scripts/04-run-diff.sh <replay.gor>
```

改動 `config/` 下的閘道設定後，**必須用真二進位實載驗證**，純 YAML/語法檢查會
漏掉「載入期才報錯」與「載入成功但行為靜默失效」兩類問題（兩者都真的發生過）：

```bash
# Nginx（需先備妥 proxy_ssl_trusted_certificate 的 PEM 與 upstream DNS，否則 -t 會失敗）
wslc.exe run --rm -v "$PWD/config/vm/nginx-shadow.conf:/etc/nginx/conf.d/default.conf" \
    nginx:1.27-alpine sh -c '
      mkdir -p /etc/nginx/ca && cp /etc/ssl/certs/ca-certificates.crt /etc/nginx/ca/internal-ca.pem
      echo "127.0.0.1 k8s-ingress.internal" >> /etc/hosts && nginx -t'

# Envoy（時間欄位是 protobuf Duration：0.2s 合法、200ms 會讓整份 bootstrap 解析失敗）
wslc.exe run --rm -v "$PWD/config/k8s/envoy-shadow.yaml:/etc/envoy/shadow.yaml" \
    envoyproxy/envoy:v1.31-latest envoy --mode validate -c /etc/envoy/shadow.yaml

# K8s manifest（kubectl --dry-run=client 需連 API server，無叢集時不可用）
kubeconform -summary -strict config/k8s/shadow-namespace.yaml
```

操作示範影片（`demo/`）改文案或節奏後必跑：

```bash
.venv/bin/python demo/render_demo.py --dry-run     # 時間軸 + lint，秒級
```

它會擋下缺字、破格、字幕超寬、敏感字串。本機沒有 emoji 字型，
`✅❌` 是手繪的；`NotoSansMonoCJK-VF.ttc` 的繁中 face 是 index=3
（順序為 JP/KR/SC/TC/HK，不可猜）。細節見 `demo/README.md`。

本機容器 runtime 是 `wslc`（WSL Containers CLI），在 Linux shell 中須帶 `.exe`；
`-p` 發佈的埠映射到 Windows 端 loopback，測試客戶端要放進同一容器網路內。
`wslc run` 無 `--privileged`，需要特權的驗證（k3s/kind、`gor --input-raw`）改走 Docker。

## 程式碼結構

| 模組 | 職責 | 修改時注意 |
|---|---|---|
| `diff/gor_parser.py` | .gor 二進位格式解析、HTTP 解碼、依 uuid 配對 | payload separator 是固定 emoji 位元組序列，勿改 |
| `diff/normalizer.py` | JSONPath-lite 比對、遮蔽、陣列排序、數值容忍 | 路徑語法支援 `$.a[*].b`、`$..key`、`$.a.*` |
| `diff/diff_engine.py` | 深度 diff、報告產生、Gate 判定、baseline 學習 | Gate 未過必須回傳離開碼 1，CI 依此擋關 |

新增噪音處理手段時，同步更新 `diff/noise-profile.yaml` 的註解與 ADR-004。

## 測試要求

`diff/selftest.py` 產生含已知噪音與已知缺陷的合成 .gor，驗證：
- 噪音（時間戳、traceId、hostname、陣列順序）被抑制
- 真實缺陷（金額錯誤、status 500）被偵測
- Gate 未過時 CLI 離開碼為 1

**任何對 diff/ 的改動都必須讓 selftest 維持通過。** 若改動使測試失效，
先確認是測試該更新，還是改動引入了漏檢。

## 待辦與已知限制

- `sanitize-gor.py` 的遮蔽規則依台灣金融常見格式撰寫，**上線前須依實際資料校準**
- 時序相依流程（同一 session 連續操作）重放時可能失序，目前未處理
- WebSocket / SSE / 大檔上傳不在範圍內
- 非 JSON 回應（XML、固定長度電文）目前僅做遮蔽後文字比對，未做結構化 diff
