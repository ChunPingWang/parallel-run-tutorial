# ADR-005：性能指標正規化

- **狀態**：已接受
- **日期**：2026-07-25

## 背景

舊應用在 VM、新應用在容器。兩者資源模型不同，**端到端 latency 直接對比會得出錯誤結論**，最常見的錯誤結論是「容器版比較慢，所以新應用性能不佳」。

實際上差異可能全部來自環境而非程式碼：

| 來源 | 影響 |
|---|---|
| cgroup CFS 節流 | P99 週期性尖刺，與程式碼無關 |
| JVM 容器感知 | 預設 heap 僅取 1/4 記憶體，GC 頻繁 |
| Ingress → Service → Pod | 多 0.3–1ms 網路跳數 |
| HPA / 冷啟動 | 重放初期 latency 異常高 |
| keepalive / TLS 握手 | 握手成本被計入 |

## 決策

**以兩邊應用內部埋設的同一組 timer（進入 controller → 回應完成）作為性能判準；端到端 latency 僅作為架構總成本參考，不作為新舊優劣依據。**

同時，比較前必須完成環境對齊：

| 項目 | 要求 |
|---|---|
| CPU | 容器 `requests = limits`，與 legacy VM 規格對等（如 4C ↔ 4 CPU） |
| CFS throttle | `container_cpu_cfs_throttled_seconds_total` 必須為 **0** |
| JVM | `-XX:MaxRAMPercentage=75`，確認 `ActiveProcessorCount` 正確 |
| HPA | PoC 期間關閉，replica 固定 |
| 連線 | 兩邊統一開長連線 |

## Gate

**應用內部耗時 P99 ≤ VM 版 × 1.1，且 CFS throttle = 0，且錯誤率無上升。**

## 理由

`requests = limits` 而非只設 requests，是為了避免容器 burst 用到節點閒置 CPU 讓數字虛高 —— 那個數字在正線滿載時不會重現。

CFS throttle 必須為 0 才能確認測到的是應用能力而非節流假象；若 PoC 期間就出現節流，代表資源配置本身需要重新評估，而不是把節流一併算進性能結論。

## 後果

**正面**
- 性能結論歸因正確，不會誤殺新應用或誤放行
- 環境對齊過程本身就會提早暴露容器化設定問題

**負面**
- 需要在兩邊應用埋點（若舊應用無法改動，此決策需退回為僅比端到端並明確標註侷限）
- 對齊資源與關閉 HPA 意味著測不到自動擴縮的真實行為，該部分需另行驗證
