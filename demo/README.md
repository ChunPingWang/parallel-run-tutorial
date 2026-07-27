# demo — 操作示範影片

由 `demo-script.yaml` 產生一支 1080p30 的 mp4：終端機模擬畫面配上固定在
螢幕下方的中文字幕，逐步示範 README「快速開始」的 7 個步驟。

```bash
python3 -m venv .venv && .venv/bin/pip install pillow pyyaml

.venv/bin/python demo/render_demo.py --dry-run          # 只印時間軸 + lint（秒級）
.venv/bin/python demo/render_demo.py --chapter 1        # 只出第 1 章
.venv/bin/python demo/render_demo.py --at 66 -o f.png   # 抽某秒的單張畫面
.venv/bin/python demo/render_demo.py -o dist/poc-demo.mp4
```

產出 `dist/poc-demo.mp4` 與 `dist/poc-demo.zh-TW.srt`（sidecar 字幕，
可搜尋、可翻譯、審稿時可直接 diff）。

---

## 改文案與節奏

**只需要動 `demo-script.yaml`。** 改完先跑 `--dry-run`，它會：

- 印出每章的起訖時間碼與長度、全片總長
- 檢查缺字、破格、字幕超寬、字幕停留過久、敏感字串
- 總長度落在 6–8 分鐘之外時警告

字幕停留時間由公式算出，不需要手填：

```
dwell = clamp(1.2 + 顯示寬度/2/4.0 + 0.35 × ASCII 詞數, 2.6, 6.5) 秒
```

用**顯示寬度**而非字元數，否則 `--gate-false-positive` 會被當成 24 個字。
ASCII 識別項另外加時間，因為技術詞彙是逐字母解析的，不適用中文的 4 字/秒。
個別字幕要覆寫就在該 beat 加 `dwell: 4.0`。

其他節奏參數在 `render_demo.py` 檔頭的常數區：字幕比畫面動作早 0.4 秒出現、
打字每影格 3 字元、打完停 1.5 秒（觀眾是打完才讀指令，所以打字要快、停留要長）。

---

## 為什麼是這個技術路線

三個選擇都是被環境逼出來的，不是偏好：

**1. 文字全部由 Pillow 畫好才進 ffmpeg。**
本機的 ffmpeg build 沒有 `drawtext` / `subtitles` / `ass` 濾鏡
（configuration 缺 `--enable-libfreetype`、`--enable-libass`），它畫不了任何一個字。

**2. rawvideo 從 stdin 餵給 ffmpeg，不落中間檔。**
逐格 PNG 會產生 12,000 多張 1080p 圖檔，光 PNG 編解碼就要 30 分鐘以上。
concat demuxer 不吃 stdin，且會產生 VFR 需要另外強制轉 CFR。
rawvideo 天然是 CFR，且「畫面沒變就重複寫同一份 buffer」讓實際繪製次數
遠低於影格數。

**3. 所有時間都是整數影格，不是秒。**
總長精確、SRT 時間碼與畫面精確同步、`--chapter N` 的偏移可對齊全片、
沒有浮點漂移。

---

## 字型：三個會毀掉成品的坑

全部在 `fontkit.py` 裡處理，改動前務必先讀。

**① 本機沒有任何 emoji 字型。**
而專案的輸出本體就是 emoji：`00-preflight.sh` 的 `✅❌⚠️`、
`e2e_verify.py` 的 `✅❌`。實測 U+2705 與 U+274C 在本機所有字型裡都缺字，
直接渲染會得到滿畫面豆腐方塊。因此 `✅❌` 一律**手繪**（`HAND_DRAWN`）。

**② `NotoSansMonoCJK-VF.ttc` 有 5 個 face，順序是 JP / KR / SC / TC / HK。**
繁中是 **index=3**，不可猜。`fontkit.tc_face_index()` 會讀 name table 偵測，
找不到就讓 build 失敗。

**③ `DroidSansFallbackFull.ttf` 完全沒有 ASCII。**
實測 `'A'`、`'0'` 都是 `.notdef`。它是 fontconfig 意義上的 fallback-only 字型，
**絕不可當主字型**，否則會產出「全英文都是豆腐」的影片。

另外兩個細節：

- **缺字偵測要兩層。** Noto CJK 的 `.notdef` advance 是全寬，只檢查字寬會讓
  豆腐通過檢查。所以還要比對 glyph 點陣是否等於 `.notdef`。
- **`✓`(U+2713) 的 advance 是 23px**（字級 34 時），既非半寬也非全寬，
  會讓整條格線歪掉，因此也列入手繪。

字寬規則：ASCII 半寬（17px）、CJK 全寬（34px），精確 2:1。
**box drawing（`─`）在這支字型是全寬** —— `00-preflight.sh` 的
`sec()` 分隔線在畫面上是 83 格寬，不是 51 個字元。

---

## 畫面上的輸出是真的還是示意的

有些步驟在開發機上無法真的執行（跨界連通性需要真的 K8s target、
錄製需要 legacy VM 與 `CAP_NET_RAW`、性能驗證需要真實負載）。因此
`demo-script.yaml` 的每個 output block 都標 `source`：

| 值 | 意義 |
|---|---|
| `real` | 逐字取自實際執行結果 |
| `illustrative` | 文字取自腳本自己的 `echo`，數值為示意值。**畫面右上角會標「※ 示意輸出」** |

技術團隊一眼看得出假輸出，而假輸出會連帶讓人懷疑整份 PoC。改動腳本時
請維持這個標記的正確性。

---

## 內容與 ADR 的一致性

這支影片是文件產出物，會跟著 ADR 一起腐化。每章的 `adr:` 欄位標出對應的
決策記錄。改動這些 ADR 時要一併檢查本檔，特別是 CLAUDE.md 的不可違反約束：

- **約束 3**：第 5 章（Phase 0 基線）必須說明 `ignore_paths` 是**候選、需人工簽核**。
  影片若讓人以為自動產出即可套用，就是在教錯的東西。
- **約束 6**：第 7 章（Phase 4 性能）必須強調判準是**應用內部 timer**，
  不是端到端 latency。

---

## 已知限制

- 無旁白語音（本機無 TTS），字幕即敘述載體
- 終端機不做平滑捲動：30fps 逐像素捲動會讓文字糊掉。每步的輸出必須塞得進
  12 列可視區，超出請在腳本裡精簡或分成兩個 beat
- `--dry-run` 的敏感字串檢查是保守的樣式比對，不能取代人工複核
