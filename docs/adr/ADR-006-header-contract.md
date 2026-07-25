# ADR-006：Shadow 流量 Header 契約

- **狀態**：已接受
- **日期**：2026-07-25

## 背景

影子流量需讓新應用「知道自己在跑什麼」，並讓兩邊回應能事後配對。混合拓撲（VM → K8s）另有 Host、XFF 等必須處理的項目。

## 決策

### 必加

| Header | 值 | 用途 |
|---|---|---|
| `X-Correlation-Id` | `$request_id` / gor uuid | diff 配對鍵，兩邊必須相同 |
| `X-Shadow-Request` | `true` | 新應用據此進 dry-run |
| `X-Origin-Platform` | `vm` | 日誌分流 |
| `Host` | 新應用 Ingress vhost | 混合拓撲必改，否則 404 |
| `Accept-Encoding` | `identity` | 免解壓即可 diff |
| `X-Forwarded-For` / `-Proto` / `X-Real-IP` | 原值鏈 | 新應用依 IP / scheme 的邏輯 |

### 必須改寫

| Header | 處理 | 原因 |
|---|---|---|
| `Idempotency-Key` | 加 `-shadow` 後綴 | 否則被防重放機制擋下 |
| `traceparent` | 產生新 trace + span link | 沿用會讓 APM 兩條 trace 互相污染 |

### 需個案處理

| 項目 | 處理 |
|---|---|
| JWT `jti` / nonce / CSRF token | shadow 模式下開白名單 |
| `Authorization` | 新系統若為獨立 auth realm，需 token exchange |

## 理由

`X-Shadow-Request` 是整套設計中**最關鍵的單一 header** —— 它是應用層 dry-run 的觸發條件，直接決定會不會重複發簡訊、重複呼叫收單。但它也**不能是唯一防線**（見 ADR-003）。

`Host` 改寫是混合拓撲特有：K8s Ingress Controller 依 Host/SNI 選 backend，沿用舊 Host 會直接 404，且這個錯誤在報告上會表現為「新應用大量 4xx」，容易被誤判為應用缺陷。

## 後果

**正面**
- 契約明確，閘道與重放腳本設定可直接對照
- 配對鍵統一，diff 管線不需猜測

**負面**
- 新應用需為 shadow 模式增加分支邏輯，該邏輯本身需要測試
- shadow 分支若誤在正線啟用，會造成交易被跳過 —— 需確保 `X-Shadow-Request` 在正線入口被剝除
