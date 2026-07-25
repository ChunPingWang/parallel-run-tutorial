# 新舊應用平行測試 PoC 套件

舊應用（VM）↔ 新應用（容器 / K8s，無 Service Mesh）的功能與性能平行驗證。

---

## 套件結構

```
.
├── README.md                       本檔
├── CLAUDE.md                       給 Claude Code 的專案指引
├── docs/
│   ├── 01-poc-plan.md            主規劃書（架構、階段、風險）
│   ├── 02-work-checklist.md            32 項工作 + Gate 定義
│   └── adr/
│       ├── ADR-001-traffic-capture-method.md          錄製重放 vs 即時鏡像
│       ├── ADR-002-shadow-gateway-placement.md      VM 側 vs K8s 側
│       ├── ADR-003-side-effect-isolation.md      三層防護
│       ├── ADR-004-noise-baseline-and-diff.md    Phase 0 設計
│       ├── ADR-005-perf-metric-normalization.md        為何不能直接比 latency
│       ├── ADR-006-header-contract.md            shadow header 規格
│       └── ADR-007-pii-masking-and-retention.md      法遵要求
├── scripts/
│   ├── 00-preflight.sh             跨界連通性驗證（W1 必做）
│   ├── 01-record.sh                legacy VM 錄製
│   ├── 02-replay-functional.sh     1x 重放（功能比對）
│   ├── 03-replay-perf.sh           倍率重放（性能驗證）
│   ├── 04-run-diff.sh              比對 + Gate 判定
│   └── sanitize-gor.py             錄製檔個資遮蔽
├── diff/
│   ├── diff_engine.py              比對引擎（compare / baseline）
│   ├── gor_parser.py               .gor 解析
│   ├── normalizer.py               噪音正規化
│   ├── noise-profile.yaml          噪音設定檔
│   └── selftest.py                 自我測試
└── config/
    ├── vm/nginx-shadow.conf        VM 側影子閘道（備案）
    └── k8s/
        ├── envoy-shadow.yaml       Envoy 影子閘道（備案）
        └── shadow-namespace.yaml   隔離用 Namespace/Quota/NetworkPolicy/Deployment
```

---

## 快速開始

### 0. 驗證工具本身可用

```bash
cd diff && python3 selftest.py
```

預期輸出：注入 4 筆真實缺陷全數偵測，196 筆噪音全數抑制。

### 1. W1 跨界連通性（在 legacy VM 上）

```bash
TARGET_HOST=new-app.bank.internal \
XFF_ECHO_PATH=/api/v1/_echo \
./scripts/00-preflight.sh
```

FAIL 為 0 才可繼續。

### 2. 錄製（legacy VM）

```bash
sudo PERCENT=5 DURATION=60m ./scripts/01-record.sh
```

### 3. 遮蔽個資（移出正線主機前必做）

```bash
export SALT=$(cat /etc/shadow-poc/salt)     # 固定值，勿更換
python3 scripts/sanitize-gor.py \
    /data/shadow-capture/legacy_20260725-090000.gor \
    /data/clean/legacy_20260725.gor --report
```

遮蔽報告零命中時**不得放行** —— 代表規則與實際資料格式不符。

### 4. Phase 0 噪音基線（舊 vs 舊）

```bash
# 先重放到 legacy 第二實例
TARGET=http://legacy-2.bank.internal:8080 \
  ./scripts/02-replay-functional.sh /data/clean/legacy_20260725.gor

python3 diff/diff_engine.py baseline \
    --combined /data/shadow-replay/replay_*.gor \
    --profile diff/noise-profile.yaml \
    --out-profile diff/noise-profile.signed.yaml \
    --gate-false-positive 1.0
```

⚠ 產出的 `ignore_paths` 是**候選**，不是結論。逐條人工簽核後才可套用 —— 自動學習會把「排序不穩定的金額欄位」也列進來，那正是真實缺陷會躲藏的位置。

### 5. Phase 1–3 功能比對

```bash
./scripts/02-replay-functional.sh /data/clean/legacy_20260725.gor
PHASE=1 PROFILE=diff/noise-profile.signed.yaml \
  ./scripts/04-run-diff.sh /data/shadow-replay/replay_20260725.gor
```

離開碼 0 = Gate 通過可推進；1 = 停在原階段。

### 6. Phase 4 性能

```bash
LADDER="100 200 500" ./scripts/03-replay-perf.sh /data/clean/legacy_20260725.gor
```

**判準是應用內部 timer，不是端到端 latency**（見 ADR-005）。

---

## 三件最容易出錯的事

1. **Host header 沒改寫** → K8s Ingress 依 SNI/Host 選 backend，回 404，報告上表現為「新應用大量 4xx」，容易誤判為應用缺陷。
2. **拿端到端 latency 直接比** → cgroup 節流、JVM 容器感知、多一跳網路會讓容器版看起來較慢，得出錯誤結論。
3. **把自動學來的 ignore_paths 直接套用** → 這是唯一會讓 PoC「看起來成功但實際失敗」的路徑，也是最難事後發現的。

---

## 依賴

- Python 3.9+、PyYAML
- GoReplay（`gor`）—— 錄製需 root 或 `CAP_NET_RAW`
- （備案）Nginx ≥ 1.13.4 或 Envoy
