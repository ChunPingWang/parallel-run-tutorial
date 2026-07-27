# 檔名對照

檔名一律使用英文以避免下載與跨平台解壓問題；檔案內容維持繁體中文。

| 檔案 | 內容 |
|---|---|
| `docs/01-poc-plan.md` | PoC 規劃書（架構、階段、風險） |
| `docs/02-work-checklist.md` | 工作檢核表（32 項 + Gate 定義） |
| `docs/build-deck.js` | 管理層簡報生成腳本（pptxgenjs） |
| `docs/adr/ADR-001-traffic-capture-method.md` | 流量取得方式：錄製重放 vs 即時鏡像 |
| `docs/adr/ADR-002-shadow-gateway-placement.md` | 影子閘道部署位置：VM 側 vs K8s 側 |
| `docs/adr/ADR-003-side-effect-isolation.md` | 副作用與資料隔離（三層防護） |
| `docs/adr/ADR-004-noise-baseline-and-diff.md` | 噪音基線與比對策略（Phase 0） |
| `docs/adr/ADR-005-perf-metric-normalization.md` | 性能指標正規化 |
| `docs/adr/ADR-006-header-contract.md` | Shadow 流量 Header 契約 |
| `docs/adr/ADR-007-pii-masking-and-retention.md` | 錄製檔個資遮蔽與保留政策 |
| `docs/03-verification-report.md` | 驗證報告（L1–L4 分層結果與發現的缺陷） |
| `verify/e2e_verify.py` | 本機端到端驗證工具（模擬新舊服務） |
| `verify/k8s-isolation/` | 隔離拓樸執行期驗證（T-14）：kind + Calico 叢集內實測 NetworkPolicy 是否真的擋 |
| `demo/demo-script.yaml` | 操作示範影片的內容腳本（章節／字幕／指令／輸出） |
| `demo/render_demo.py` | 示範影片渲染器（Pillow 畫格 → ffmpeg 出 mp4） |
| `demo/fontkit.py` | 字型與字寬工具（缺字偵測、CJK 斷行、手繪符號） |
| `demo/README.md` | 影片重跑方式、節奏調整、字型與 ffmpeg 限制說明 |
| `dist/poc-exec-deck.pptx` / `.pdf` | 管理層簡報產出物（可由 build-deck.js 重現） |
| `dist/poc-demo.mp4` / `.zh-TW.srt` | 操作示範影片與字幕（不入版控，可由 demo/ 重現） |
| `dist/poc-package.zip` | 對外交付打包 |
