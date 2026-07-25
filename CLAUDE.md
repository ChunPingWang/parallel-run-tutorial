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
3. **noise profile 的 ignore_paths 不得由程式自動套用。** `baseline` 子命令
   只產生候選檔，套用需人工簽核。若被要求「自動化這一步」，先說明風險（ADR-004）
   而不是直接實作。
4. **Nginx 影子 location 的 timeout 不得放寬。** connect 200ms / read 2s 是
   避免影子端拖累正線的關鍵，Nginx 主連線在子請求結束前不會釋放。
5. **性能比較以應用內部 timer 為判準**，不是端到端 latency（ADR-005）。
   若被要求「拿 latency 直接比」，先說明歸因錯誤的風險。

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
