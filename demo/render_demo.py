#!/usr/bin/env python3
"""render_demo.py  -  由 demo-script.yaml 產生操作示範影片（mp4）

設計上的三個硬性選擇，都是被環境逼出來的，改動前請先讀 demo/README.md：

1. **所有時間都是整數影格**，不是秒。總長精確、SRT 時間碼精確、
   `--chapter N` 的偏移可對齊全片、沒有浮點漂移。
2. **文字全部由 Pillow 畫好才進 ffmpeg。** 本機 ffmpeg 沒有
   drawtext / subtitles / ass 濾鏡，它畫不了任何一個字。
3. **rawvideo 從 stdin 餵給 ffmpeg**，不落任何中間檔。天然 CFR，
   不會產生 VFR 造成剪輯軟體時間軸位移。

用法：
    python3 demo/render_demo.py --dry-run            # 只印時間軸與 lint，不渲染
    python3 demo/render_demo.py --chapter 1          # 只出第 1 章
    python3 demo/render_demo.py --at 65.0 -o f.png   # 抽某秒的單張畫面
    python3 demo/render_demo.py -o dist/poc-demo.mp4 # 全片
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

import yaml
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontkit as fk  # noqa: E402

# ---------------------------------------------------------------------------
# 規格
# ---------------------------------------------------------------------------

FPS = 30
W, H = 1920, 1080

TERM_SIZE = 34                      # 字級必須偶數，cell 才是整數
CELL_W = TERM_SIZE // 2             # 17
LINE_H = 52
COLS, ROWS = 88, 12

SUB_SIZE = 46
SUB_LINE_H = 62
SUB_MAX_CELLS = 44                  # = 22 個全形字
SUB_MAX_LINES = 2

# 版面（實際算過會閉合的一組；字幕帶下緣不低於 y=990，避開播放器控制列）
TERM_X, TERM_Y = 178, 96
TERM_W = COLS * CELL_W + 68         # 1496 + 68 = 1564
TERM_TITLE_H = 40
TERM_TEXT_X = TERM_X + 34
TERM_TEXT_TOP = TERM_Y + TERM_TITLE_H + 20
TERM_H = TERM_TITLE_H + 20 + ROWS * LINE_H + 28
SUB_Y0, SUB_Y1 = 836, 990
PROGRESS_Y = 1074

# 色彩：文字接近無彩色，4:2:0 色度取樣才不會讓細字發糊。
# 強調色一律選高 luma 的，避免純飽和色出現彩色鑲邊。
BG = "#0B0D11"
TERM_BG = "#14171C"                 # 不用純黑：純黑會讓抗鋸齒邊緣出現 banding
TERM_BORDER = "#2A2F3A"
TERM_TITLEBAR = "#1B1F27"
FG = "#D7DAE0"
DIM = "#7A828F"
GREEN = "#98C379"
AMBER = "#E5C07B"
RED = "#E06C75"
BLUE = "#61AFEF"
NAVY = "#0C2D4F"                    # 沿用 docs/build-deck.js 的色盤
ACCENT = "#0F62FE"
TEAL = "#009C97"

HOST_STYLES = {                     # 每個指令跑在哪台機器 —— 這個專案最常見的錯誤來源
    "legacy": ("legacy VM", "#E5C07B"),
    "shadow": ("k8s shadow ns", "#61AFEF"),
    "local": ("本機", "#98C379"),
    "gw": ("shadow-gw", "#C678DD"),
}

# 節奏
LEAD_IN = 12                        # 字幕比畫面動作早 0.4s 出現
LEAD_IN_FIRST = 30                  # 每章第一張卡給多一點（1.0s）
TYPE_CHARS_PER_FRAME = 3
TYPE_MIN_FRAMES = 8
HOLD_AFTER_TYPE = 45                # 打完到按 Enter：真正給人讀指令的時間
ENTER_BEAT = 6
OUT_FRAMES_PER_LINE = 2
OUT_KEY_HOLD = 24
OUT_SETTLE = 30
CHAPTER_CARD = 75                   # 2.5s
CURSOR_CYCLE = 24
TAIL_HOLD = 60                      # 片尾多停 2s，避免播放器吃掉最後一格

DWELL_MIN, DWELL_MAX = 2.6, 6.5

_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_./:@=\-]{2,}")

# 敏感字串 lint：一支對外可傳播的 mp4 比 markdown 更容易外流，
# 而這支影片本身就在示範個資遮蔽 —— 洩漏未遮蔽資料會非常難解釋。
_SENSITIVE = [
    (re.compile(r"\b(?!10\.|127\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|172\.1[6-9]\.|172\.2\d\.|172\.3[01]\.)"
                r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "疑似真實 IP（請用 TEST-NET 或 RFC1918）"),
    (re.compile(r"\b[A-Z]\d{9}\b"), "疑似身分證字號"),
    (re.compile(r"\b09\d{8}\b"), "疑似手機號碼"),
    (re.compile(r"\b\d{13,19}\b"), "疑似卡號／帳號"),
]


# ---------------------------------------------------------------------------
# 時間軸模型
# ---------------------------------------------------------------------------

def dwell_frames(text: str, override: float | None = None) -> int:
    """字幕停留影格數。

    公式用「顯示寬度」而非 len()：`--gate-false-positive` 這種字串
    以字元數算會被當成 24 個字而超時。內嵌的 ASCII 識別項另外加時間，
    因為技術詞彙是逐字母解析的，不適用中文的 4 字/秒。
    """
    if override is not None:
        return max(1, round(override * FPS))
    eff = fk.dwidth(text) / 2
    ascii_tokens = len(_ASCII_TOKEN.findall(text))
    sec = 1.2 + eff / 4.0 + 0.35 * ascii_tokens
    sec = max(DWELL_MIN, min(DWELL_MAX, sec))
    return round(sec * FPS)


@dataclass
class Seg:
    """一段畫面狀態，持續 n 影格。"""
    n: int
    term: tuple = ()                # 終端機內容（每列一個 (text, color) 串列）
    sub: tuple = ()                 # 字幕行
    chapter: str = ""               # 章節卡標題（非空 = 全螢幕章節卡）
    chapter_sub: str = ""
    hud: tuple = ("", 0)            # (章節標題, 章節序號)
    host: str = "local"
    cursor: bool = False            # 是否顯示閃爍游標
    illustrative: bool = False      # 右上角標「示意輸出」
    key_row: int = -1               # 高亮的輸出列


@dataclass
class Timeline:
    segs: list = field(default_factory=list)
    subs: list = field(default_factory=list)   # (start_f, end_f, text) 供 SRT

    @property
    def total(self) -> int:
        return sum(s.n for s in self.segs)


def build_timeline(script: dict, only_chapter: int | None = None) -> Timeline:
    tl = Timeline()
    chapters = script["chapters"]
    if only_chapter is not None:
        chapters = [c for c in chapters if c["id"] == only_chapter]
        if not chapters:
            raise SystemExit(f"找不到章節 {only_chapter}")

    n_ch = len(script["chapters"])
    for ch in chapters:
        hud = (ch["title"], ch["id"])
        # ---- 章節卡 ----
        tl.segs.append(Seg(n=CHAPTER_CARD, chapter=ch["title"],
                           chapter_sub=ch.get("lead", ""), hud=hud))
        tl.subs.append((tl.total - CHAPTER_CARD, tl.total, ch.get("lead", ch["title"])))

        term: list = []
        host = ch.get("host", "local")
        first = True
        # 「示意輸出」標記要跟著**畫面上的內容**走，不是跟著產生它的那個 beat。
        # 否則後續的 note beat 仍顯示同一份示意輸出，標記卻消失了 ——
        # 觀眾會以為那是實跑結果。
        term_illustrative = False

        for beat in ch.get("beats", []):
            kind = beat.get("kind", "note")
            host = beat.get("host", host)
            sub_text = beat.get("subtitle", "")
            sub_lines = tuple(fk.wrap_cjk(sub_text, SUB_MAX_CELLS)) if sub_text else ()
            if kind == "output":
                term_illustrative = beat.get("source", "real") != "real"
            elif kind == "clear":
                term_illustrative = False
            illustrative = term_illustrative
            lead = LEAD_IN_FIRST if first else LEAD_IN
            first = False

            sub_start = tl.total
            hold = dwell_frames(sub_text, beat.get("dwell"))

            if kind == "clear":
                term = []
                tl.segs.append(Seg(n=max(6, lead), term=(), sub=sub_lines, hud=hud,
                                   host=host, cursor=True))
                continue

            # 字幕先出現、畫面還沒動 —— 先讀完再看
            if sub_lines:
                tl.segs.append(Seg(n=lead, term=tuple(term), sub=sub_lines, hud=hud,
                                   host=host, cursor=True, illustrative=illustrative))

            if kind == "cmd":
                cmd = beat["cmd"]
                lines = cmd.split("\n")
                # 逐字打出（觀眾是打完才讀，所以打字要快、打完要停）
                typed_base = list(term)
                for li, line in enumerate(lines):
                    steps = max(TYPE_MIN_FRAMES,
                                -(-len(line) // TYPE_CHARS_PER_FRAME))
                    for s in range(1, steps + 1):
                        shown = line[:min(len(line), s * TYPE_CHARS_PER_FRAME)]
                        cur = typed_base + [
                            _prompt_line(lines[:li], li) + [] ] if False else None
                        partial = typed_base + \
                            [_cmd_row(l, i == 0) for i, l in enumerate(lines[:li])] + \
                            [_cmd_row(shown, li == 0)]
                        tl.segs.append(Seg(n=1, term=tuple(partial), sub=sub_lines,
                                           hud=hud, host=host, cursor=True,
                                           illustrative=illustrative))
                term = typed_base + [_cmd_row(l, i == 0) for i, l in enumerate(lines)]
                tl.segs.append(Seg(n=HOLD_AFTER_TYPE, term=tuple(term), sub=sub_lines,
                                   hud=hud, host=host, cursor=True,
                                   illustrative=illustrative))
                tl.segs.append(Seg(n=ENTER_BEAT, term=tuple(term), sub=sub_lines,
                                   hud=hud, host=host, illustrative=illustrative))

            elif kind == "output":
                for raw in beat.get("lines", []):
                    if isinstance(raw, dict):
                        text, key = raw["text"], raw.get("key", False)
                    else:
                        text, key = raw, False
                    term = term + [_out_row(text)]
                    term = term[-ROWS:]
                    tl.segs.append(Seg(n=OUT_FRAMES_PER_LINE, term=tuple(term),
                                       sub=sub_lines, hud=hud, host=host,
                                       illustrative=illustrative,
                                       key_row=len(term) - 1 if key else -1))
                    if key:
                        tl.segs.append(Seg(n=OUT_KEY_HOLD, term=tuple(term),
                                           sub=sub_lines, hud=hud, host=host,
                                           illustrative=illustrative,
                                           key_row=len(term) - 1))
                tl.segs.append(Seg(n=OUT_SETTLE, term=tuple(term), sub=sub_lines,
                                   hud=hud, host=host, cursor=True,
                                   illustrative=illustrative))

            # 字幕的剩餘停留時間
            used = tl.total - sub_start
            remain = max(FPS // 2, hold - used + lead)
            tl.segs.append(Seg(n=remain, term=tuple(term), sub=sub_lines, hud=hud,
                               host=host, cursor=True, illustrative=illustrative))
            if sub_lines:
                tl.subs.append((sub_start, tl.total, sub_text))

    tl.segs.append(Seg(n=TAIL_HOLD, term=(), sub=(), hud=("", 0)))
    return tl


def _cmd_row(text: str, with_prompt: bool) -> tuple:
    return (("$ " if with_prompt else "    ") + text, "cmd")


def _out_row(text: str) -> tuple:
    return (text, "out")


def _prompt_line(*a):     # 保留給未來擴充
    return []


# ---------------------------------------------------------------------------
# 繪製
# ---------------------------------------------------------------------------

class Renderer:
    def __init__(self, total_frames: int):
        self.total = total_frames
        self.f_term = fk.load(TERM_SIZE, fk.WEIGHT_BODY)
        self.f_term_b = fk.load(TERM_SIZE, fk.WEIGHT_BOLD)
        self.f_sub = fk.load(SUB_SIZE, fk.WEIGHT_BODY)
        self.f_hud = fk.load(24, fk.WEIGHT_BODY)
        self.f_chap = fk.load(72, fk.WEIGHT_BOLD)
        self.f_chap_sub = fk.load(38, fk.WEIGHT_BODY)
        self.base = self._make_base()
        self._cache: dict = {}

    def _make_base(self) -> Image.Image:
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([TERM_X, TERM_Y, TERM_X + TERM_W, TERM_Y + TERM_H],
                            radius=10, fill=TERM_BG, outline=TERM_BORDER, width=2)
        d.rounded_rectangle([TERM_X, TERM_Y, TERM_X + TERM_W, TERM_Y + TERM_TITLE_H],
                            radius=10, fill=TERM_TITLEBAR)
        d.rectangle([TERM_X, TERM_Y + TERM_TITLE_H - 10,
                     TERM_X + TERM_W, TERM_Y + TERM_TITLE_H], fill=TERM_TITLEBAR)
        d.line([TERM_X, TERM_Y + TERM_TITLE_H, TERM_X + TERM_W, TERM_Y + TERM_TITLE_H],
               fill=TERM_BORDER, width=2)
        for i, c in enumerate(("#E06C75", "#E5C07B", "#98C379")):
            cx = TERM_X + 26 + i * 26
            cy = TERM_Y + TERM_TITLE_H // 2
            d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=c)
        d.rectangle([0, SUB_Y0, W, SUB_Y1], fill=NAVY)
        d.line([0, SUB_Y0, W, SUB_Y0], fill=ACCENT, width=3)
        return im

    def frame(self, seg: Seg, idx: int) -> Image.Image:
        cursor_on = seg.cursor and (idx % CURSOR_CYCLE) < CURSOR_CYCLE // 2
        prog_px = int(W * idx / max(1, self.total))
        key = (seg.term, seg.sub, seg.chapter, seg.chapter_sub, seg.hud, seg.host,
               cursor_on, seg.illustrative, seg.key_row, prog_px)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        im = self._draw(seg, cursor_on, prog_px)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = im
        return im

    def _draw(self, seg: Seg, cursor_on: bool, prog_px: int) -> Image.Image:
        if seg.chapter:
            im = Image.new("RGB", (W, H), NAVY)
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, W, 6], fill=ACCENT)
            t = seg.chapter
            tw = fk.dwidth(t) * (72 // 2)
            fk.draw_cells(d, (W - tw) // 2, H // 2 - 10, t, 72 // 2, self.f_chap, "#FFFFFF")
            if seg.chapter_sub:
                for i, line in enumerate(fk.wrap_cjk(seg.chapter_sub, 52)):
                    lw = fk.dwidth(line) * (38 // 2)
                    fk.draw_cells(d, (W - lw) // 2, H // 2 + 80 + i * 52, line,
                                  38 // 2, self.f_chap_sub, "#AFC3DA")
            self._progress(d, prog_px)
            return im

        im = self.base.copy()
        d = ImageDraw.Draw(im)

        # HUD：左上章節、右上進度指示
        title, num = seg.hud
        if title:
            fk.draw_cells(d, TERM_X, 60, f"{num} / 7  {title}", 12, self.f_hud, DIM)
        for i in range(8):
            x = W - 178 - (7 - i) * 26
            on = i <= num
            d.rectangle([x, 44, x + 18, 50], fill=ACCENT if on else "#243043")

        # 終端機標題列：主機 badge —— 「這個指令跑在哪台機器」是本專案最常見的錯誤來源
        label, color = HOST_STYLES.get(seg.host, HOST_STYLES["local"])
        bx = TERM_X + 110
        bw = fk.dwidth(label) * 12 + 28
        d.rounded_rectangle([bx, TERM_Y + 8, bx + bw, TERM_Y + TERM_TITLE_H - 8],
                            radius=6, outline=color, width=2)
        fk.draw_cells(d, bx + 14, TERM_Y + 28, label, 12, self.f_hud, color)

        if seg.illustrative:
            tag = "※ 示意輸出"
            tw = fk.dwidth(tag) * 12
            fk.draw_cells(d, TERM_X + TERM_W - tw - 20, TERM_Y + 28, tag, 12,
                          self.f_hud, AMBER)

        # 終端機內容
        for r, row in enumerate(seg.term):
            text, kind = row
            y = TERM_TEXT_TOP + r * LINE_H
            baseline = y + 38
            if r == seg.key_row:
                d.rectangle([TERM_X + 12, y - 4, TERM_X + TERM_W - 12, y + LINE_H - 8],
                            fill="#1E2530")
            if kind == "cmd":
                x = TERM_TEXT_X
                if text.startswith("$ "):
                    fk.draw_cells(d, x, baseline, "$ ", CELL_W, self.f_term_b, TEAL)
                    x += 2 * CELL_W
                    text = text[2:]
                else:
                    x += 4 * CELL_W
                    text = text[4:]
                fk.draw_cells(d, x, baseline, text, CELL_W, self.f_term_b, "#FFFFFF")
            else:
                fk.draw_cells(d, TERM_TEXT_X, baseline, text, CELL_W,
                              self.f_term, _out_color(text))

        if cursor_on:
            r = max(0, len(seg.term) - 1)
            last = seg.term[-1][0] if seg.term else ""
            cx = TERM_TEXT_X + fk.dwidth(last) * CELL_W
            cy = TERM_TEXT_TOP + r * LINE_H
            d.rectangle([cx, cy + 6, cx + CELL_W, cy + 42], fill="#5C6370")

        # 字幕（固定在螢幕下方）
        n = len(seg.sub)
        top = SUB_Y0 + (SUB_Y1 - SUB_Y0 - n * SUB_LINE_H) // 2
        for i, line in enumerate(seg.sub):
            lw = fk.dwidth(line) * (SUB_SIZE // 2)
            fk.draw_cells(d, (W - lw) // 2, top + i * SUB_LINE_H + 46, line,
                          SUB_SIZE // 2, self.f_sub, "#FFFFFF")

        self._progress(d, prog_px)
        return im

    def _progress(self, d, prog_px: int) -> None:
        d.rectangle([0, PROGRESS_Y, W, PROGRESS_Y + 3], fill="#1B2430")
        if prog_px > 0:
            d.rectangle([0, PROGRESS_Y, prog_px, PROGRESS_Y + 3], fill=ACCENT)


def _out_color(text: str) -> str:
    if any(m in text for m in ("✅", "✓", "PASS", "OK", "通過", "成功")):
        return GREEN
    if any(m in text for m in ("❌", "✗", "FAILED", "未通過", "不得", "錯誤")):
        return RED
    if any(m in text for m in ("⚠", "WARN", "警告", "注意")):
        return AMBER
    if text.strip().startswith(("──", "==", "  ─")):
        return DIM
    return FG


# ---------------------------------------------------------------------------
# lint / dry-run
# ---------------------------------------------------------------------------

def lint(script: dict, tl: Timeline) -> list[str]:
    problems = []

    def check_text(text: str, where: str, size: int, max_cells: int | None):
        for p in fk.lint_text(text, size):
            problems.append(f"{where}: {p}")
        if max_cells and fk.dwidth(text) > max_cells:
            problems.append(f"{where}: 寬度 {fk.dwidth(text)} 超過 {max_cells} cells")
        for pat, why in _SENSITIVE:
            m = pat.search(text)
            if m:
                problems.append(f"{where}: {why} -> {m.group(0)!r}")

    for ch in script["chapters"]:
        where0 = f"第 {ch['id']} 章"
        check_text(ch["title"], f"{where0} 標題", 72, None)
        if ch.get("lead"):
            check_text(ch["lead"], f"{where0} 導言", 38, None)
        for bi, beat in enumerate(ch.get("beats", [])):
            where = f"{where0} beat{bi}"
            sub = beat.get("subtitle", "")
            if sub:
                check_text(sub, f"{where} 字幕", SUB_SIZE, None)
                lines = fk.wrap_cjk(sub, SUB_MAX_CELLS)
                if len(lines) > SUB_MAX_LINES:
                    problems.append(
                        f"{where} 字幕斷成 {len(lines)} 行（上限 {SUB_MAX_LINES}）："
                        f"{sub[:30]}… —— 請改寫成更短的句子")
                d = dwell_frames(sub, beat.get("dwell")) / FPS
                if d > DWELL_MAX + 0.01:
                    problems.append(f"{where} 字幕停留 {d:.1f}s 超過 {DWELL_MAX}s，應拆卡")
            for line in beat.get("cmd", "").split("\n"):
                if line:
                    check_text(line, f"{where} 指令", TERM_SIZE, COLS - 4)
            for raw in beat.get("lines", []):
                text = raw["text"] if isinstance(raw, dict) else raw
                check_text(text, f"{where} 輸出", TERM_SIZE, COLS)
    return problems


def dry_run(script: dict, tl: Timeline) -> int:
    print(f"{'章節':<26}{'起':>9}{'迄':>9}{'長度':>8}")
    print("─" * 56)
    f = 0
    by_ch: dict = {}
    for seg in tl.segs:
        title = seg.chapter or seg.hud[0]
        by_ch.setdefault(title, [f, f])
        by_ch[title][1] = f + seg.n
        f += seg.n
    for title, (a, b) in by_ch.items():
        if not title:
            continue
        print(f"{title:<26}{_tc(a):>9}{_tc(b):>9}{(b-a)/FPS:>7.1f}s")
    total = tl.total
    print("─" * 56)
    print(f"{'總計':<26}{'':>9}{_tc(total):>9}{total/FPS:>7.1f}s  ({total} 影格)")

    print(f"\n字幕：{len(tl.subs)} 張")
    worst = sorted(tl.subs, key=lambda s: s[1] - s[0])[:3]
    for a, b, t in worst:
        print(f"  最短 {(b-a)/FPS:4.1f}s  {t[:40]}")

    problems = lint(script, tl)
    if problems:
        print(f"\n❌ lint 發現 {len(problems)} 個問題：")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\n✅ lint 通過（無缺字、無破格、無超寬、無敏感字串）")

    sec = total / FPS
    if not (360 <= sec <= 480):
        print(f"⚠️  總長度 {sec/60:.1f} 分鐘，落在目標 6–8 分鐘之外")
    return 1 if problems else 0


def _tc(f: int) -> str:
    return f"{f//FPS//60:02d}:{f//FPS%60:02d}.{f%FPS:02d}"


def write_srt(tl: Timeline, path: str) -> None:
    def ts(f):
        ms = int(f * 1000 / FPS)
        return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"
    with open(path, "w", encoding="utf-8") as fh:
        for i, (a, b, text) in enumerate(tl.subs, 1):
            fh.write(f"{i}\n{ts(a)} --> {ts(b)}\n")
            fh.write("\n".join(fk.wrap_cjk(text, SUB_MAX_CELLS)) + "\n\n")


# ---------------------------------------------------------------------------
# 輸出
# ---------------------------------------------------------------------------

def encode(tl: Timeline, out: str, preset: str) -> int:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{W}x{H}", "-r", str(FPS),
        "-i", "pipe:0",
        # 完全沒有音軌的 mp4 在 PowerPoint / Teams 內嵌時可能不播或顯示 0:00
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", preset, "-tune", "stillimage", "-crf", "18",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.0",
        # colorprim/transfer/colormatrix 要寫進 x264 才會進 H.264 的 VUI；
        # 只給 ffmpeg 的 -color_* 旗標的話，容器層有標但位元流裡沒有。
        "-x264-params",
        "deblock=-1,-1:aq-mode=3:keyint=60:min-keyint=30"
        ":colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000",
        "-movflags", "+faststart", out,
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    # stderr 不接管：接了又不讀會在 4KB 緩衝滿時死鎖，
    # 而且 ffmpeg 啟動失敗時的真正錯誤訊息會被 BrokenPipeError 蓋掉。
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    r = Renderer(tl.total)
    idx = 0
    try:
        for seg in tl.segs:
            for _ in range(seg.n):
                buf = r.frame(seg, idx).tobytes()
                proc.stdin.write(buf)
                idx += 1
    except BrokenPipeError:
        proc.wait()
        print(f"\nffmpeg 提前結束（returncode={proc.returncode}），"
              f"錯誤訊息在上方 ffmpeg 的輸出裡。", file=sys.stderr)
        return proc.returncode or 1
    proc.stdin.close()
    proc.wait()
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--script", default=os.path.join(here, "demo-script.yaml"))
    ap.add_argument("-o", "--out", default="dist/poc-demo.mp4")
    ap.add_argument("--dry-run", action="store_true", help="只印時間軸與 lint")
    ap.add_argument("--chapter", type=int, help="只渲染指定章節")
    ap.add_argument("--at", type=float, help="抽某秒的單張 PNG（配合 -o *.png）")
    ap.add_argument("--preset", default="slow",
                    help="x264 preset。瓶頸不在編碼，slow 幾乎免費")
    ap.add_argument("--srt", help="另外輸出 SRT 字幕檔")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as fh:
        script = yaml.safe_load(fh)
    tl = build_timeline(script, args.chapter)

    if args.dry_run:
        return dry_run(script, tl)

    problems = lint(script, tl)
    if problems:
        print(f"❌ lint 未通過（{len(problems)} 項），先修好再渲染：", file=sys.stderr)
        for p in problems[:10]:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.at is not None:
        target = int(args.at * FPS)
        r = Renderer(tl.total)
        f = 0
        for seg in tl.segs:
            if f + seg.n > target:
                r.frame(seg, target).save(args.out)
                print(f"已輸出 {args.out}（{_tc(target)}）")
                return 0
            f += seg.n
        print("指定秒數超過片長", file=sys.stderr)
        return 1

    print(f"總長 {tl.total/FPS:.1f}s（{tl.total} 影格）→ {args.out}")
    rc = encode(tl, args.out, args.preset)
    if rc == 0:
        srt = args.srt or os.path.splitext(args.out)[0] + ".zh-TW.srt"
        write_srt(tl, srt)
        print(f"\n完成：{args.out}\n字幕：{srt}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
