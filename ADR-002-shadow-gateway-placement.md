# ADR-002：影子閘道部署位置（備案採用時）

- **狀態**：已接受（條件性）
- **日期**：2026-07-25
- **關聯**：ADR-001

## 背景

若 ADR-001 的備案被啟用（需要即時鏡像），閘道有三種放置方式：

- **A. VM 側**：新增 Nginx/Envoy VM，插在既有 LB 與 legacy pool 之間
- **B. K8s 側**：正線流量全繞進 K8s，再導回 VM
- **C. legacy VM 上的 gor agent**：不改流量路徑（即 ADR-001 主方案）

## 決策

**採 A（VM 側影子閘道）。明確排除 B。**

## 理由

方案 B 等於**為了測試把正線可用性押在尚未驗證的新叢集上**。K8s 叢集若發生 Ingress 異常、節點驅逐、網路策略誤設，直接影響正線核心交易。銀行環境不接受此風險。

方案 A 的閘道以 Active/Standby 掛在既有 L4 VIP 後方，故障時可切回直連 legacy pool，爆炸半徑可控。

## 後果

**正面**
- 正線相依性不進入新叢集
- 回退路徑明確（VIP 切回直連）

**負面**
- 正線多一跳，需驗證延遲增幅 < 2%
- 新增 2 台 VM 的維運成本
- 影子端變慢仍可能拖累主線 —— **timeout 必須壓在 connect 200ms / read 2s**

## 實作要點（VM → K8s 特有）

```nginx
location = /shadow {
    internal;
    proxy_pass https://new_k8s$request_uri;

    # K8s Ingress 依 SNI/Host 選 backend，沿用原 Host 會 404
    proxy_set_header Host new-app.bank.internal;
    proxy_ssl_server_name on;
    proxy_ssl_name        new-app.bank.internal;

    proxy_connect_timeout 200ms;
    proxy_read_timeout    2s;
}
```

若改用 Envoy，優勢是 `runtime_fraction` 可熱調比例且鏡像為真非同步；但注意 Envoy 會自動將鏡像請求的 `Host` 改為 `<host>-shadow`，新應用若有 Host-based 路由或多租戶邏輯需另行處理。
