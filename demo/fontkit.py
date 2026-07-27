#!/usr/bin/env python3
"""fontkit.py  -  示範影片的字型、字寬與繪字工具

為什麼需要這一層（每一條都是實測踩到的）：

1. **本機沒有任何 emoji 字型。** 而專案的輸出本體就是 emoji：
   `00-preflight.sh` 的 ✅❌⚠️、`e2e_verify.py` 的 ✅❌。
   直接渲染會得到滿畫面豆腐方塊，因此 ✅❌ 一律改為**手繪**。
2. **`NotoSansMonoCJK-VF.ttc` 有 5 個 face，順序是 JP/KR/SC/TC/HK。**
   繁中是 index=3，不可硬猜；本模組在載入時以 name table 偵測並驗證。
3. **`DroidSansFallbackFull.ttf` 完全沒有 ASCII**（'A'、'0' 皆為 .notdef），
   只能當 CJK 罕用字的最後手段，絕不可當主字型。
4. **`✓`(U+2713) 的 advance 是 23px**（字級 34 時），既非半寬也非全寬，
   會讓整條格線歪掉，因此列入必須替換的清單。
5. **缺字偵測要兩層**：Noto CJK 的 .notdef advance 是全寬，所以只檢查
   advance 會讓豆腐通過檢查。必須另外比對 glyph 點陣是否等於 .notdef。
"""

from __future__ import annotations

import re
import struct
import unicodedata as ud
from functools import lru_cache

from PIL import ImageFont

FONT_TTC = "/usr/share/fonts/google-noto-sans-mono-cjk-vf-fonts/NotoSansMonoCJK-VF.ttc"
FONT_CJK_FALLBACK = "/usr/share/fonts/google-droid-sans-fonts/DroidSansFallbackFull.ttf"

# 字型的 wght 軸範圍是 400–700。本文用 500 而非 400：
# Pillow 的抗鋸齒未做 gamma 校正，淺字在暗底上會顯得比設計更細；
# 且此字型是 CFF2 輪廓、無 TrueType hinting，小字級筆畫偏瘦。
# 用 Medium 補償是一行程式就能拿到的清晰度提升。
WEIGHT_BODY = 500.0
WEIGHT_BOLD = 700.0

# 零寬字元：VS16（emoji variation selector）在此字型是 .notdef，必須先剝掉
ZERO_WIDTH = {"️", "︎", "​", "‌", "‍"}

# 必須手繪或替換的字元 —— 缺字或破格，不可交給字型
# value: (繪製代號, 預設顏色)
HAND_DRAWN = {
    "✅": ("check", "#98C379"),   # U+2705 缺字
    "✔": ("check", "#98C379"),   # 缺字
    "✓": ("check", "#98C379"),   # advance 23px 破格
    "❌": ("cross", "#E06C75"),   # U+274C 缺字
    "✖": ("cross", "#E06C75"),   # 缺字
    "✗": ("cross", "#E06C75"),   # 缺字
    "❗": ("bang", "#E5C07B"),    # 缺字
}


# ---------------------------------------------------------------------------
# face 偵測
# ---------------------------------------------------------------------------

def _face_families(path: str) -> list[str]:
    """讀 TTC 表頭與各 face 的 name table，回傳 family 名稱清單。"""
    with open(path, "rb") as fh:
        data = fh.read()
    tag, _, _, num = struct.unpack(">4sHHI", data[:12])
    if tag != b"ttcf":
        return []
    offsets = struct.unpack(f">{num}I", data[12:12 + 4 * num])
    families = []
    for off in offsets:
        num_tables = struct.unpack(">H", data[off + 4:off + 6])[0]
        name_off = None
        for t in range(num_tables):
            p = off + 12 + t * 16
            rec_tag, _, t_off, _ = struct.unpack(">4sIII", data[p:p + 16])
            if rec_tag == b"name":
                name_off = t_off
                break
        if name_off is None:
            families.append("")
            continue
        _, count, str_off = struct.unpack(">HHH", data[name_off:name_off + 6])
        fam = ""
        for r in range(count):
            p = name_off + 6 + r * 12
            pid, eid, _, nid, ln, so = struct.unpack(">HHHHHH", data[p:p + 12])
            if nid == 1 and pid == 3 and eid == 1:
                fam = data[name_off + str_off + so:
                           name_off + str_off + so + ln].decode("utf-16-be")
                break
        families.append(fam)
    return families


@lru_cache(maxsize=1)
def tc_face_index(path: str = FONT_TTC) -> int:
    """找出繁體中文（TC）那一個 face 的 index。"""
    families = _face_families(path)
    for i, fam in enumerate(families):
        if fam.rstrip().endswith(" TC"):
            return i
    raise RuntimeError(
        f"在 {path} 找不到繁中 face。實際 faces={families}。"
        "此字型的 face 順序依發行版可能不同，不可硬碼。")


# ---------------------------------------------------------------------------
# 字型載入
# ---------------------------------------------------------------------------

@lru_cache(maxsize=16)
def load(size: int, weight: float = WEIGHT_BODY) -> ImageFont.FreeTypeFont:
    """載入指定字級與字重。

    每個 (size, weight) 組合都是獨立物件 —— set_variation_by_axes 會就地
    改動字型且 Pillow 內部有 glyph cache，共用同一個物件來回切字重會拿到錯的字。
    """
    if size % 2:
        raise ValueError(f"字級必須是偶數才能讓 cell 寬為整數，收到 {size}")
    font = ImageFont.truetype(
        FONT_TTC, size, index=tc_face_index(),
        layout_engine=ImageFont.Layout.BASIC)   # BASIC = 關閉 kerning，格線才會準
    font.set_variation_by_axes([weight])
    return font


@lru_cache(maxsize=1)
def _notdef_bytes(size: int = 34) -> tuple[bytes, tuple[int, int]]:
    """取得 .notdef（豆腐）的點陣，用來比對缺字。"""
    f = load(size)
    m = f.getmask("")      # private use area，必定無定義
    return bytes(m), m.size


def is_missing(ch: str, size: int = 34) -> bool:
    """這個字元在字型裡是否缺字（渲染出來會是豆腐）。

    只檢查 advance 不夠：.notdef 的 advance 是全寬，會偽裝成正常的 CJK 字。
    """
    if ch in ZERO_WIDTH or ch.isspace():
        return False
    f = load(size)
    m = f.getmask(ch)
    nd, nd_size = _notdef_bytes(size)
    return m.size == nd_size and bytes(m) == nd


# ---------------------------------------------------------------------------
# 字寬（以 cell 為單位，半形 = 1、全形 = 2）
# ---------------------------------------------------------------------------

def cells(ch: str) -> int:
    if ch in ZERO_WIDTH or ud.combining(ch):
        return 0
    # 'A'（Ambiguous）算 2 格：此字型的 ─ │ █ ● ⚠ → … 全部是全寬，實測確認
    return 2 if ud.east_asian_width(ch) in ("W", "F", "A") else 1


def dwidth(s: str) -> int:
    """字串的顯示寬度，單位是 cell。注意：box drawing（─）在此字型是全寬。"""
    return sum(cells(c) for c in s)


# ---------------------------------------------------------------------------
# 中文斷行（無空白可斷，textwrap 不適用）
# ---------------------------------------------------------------------------

# 不可出現在行首（避讓標點懸掛）
PROHIBIT_START = "，。、；：！？）」』】〉》〕｝…‧・%,.:;!?)]}>"
# 不可出現在行尾
PROHIBIT_END = "（「『【〈《〔｛([{<"

# ASCII 識別項（指令、路徑、旗標）視為不可切斷的原子
_TOKEN = re.compile(r"[A-Za-z0-9_./:@=\-\[\]{}$~*%]+|\s+|.")


def wrap_cjk(text: str, max_cells: int, balance: bool = True) -> list[str]:
    """依顯示寬度斷行，套用中文避頭尾規則，並讓兩行長度接近。

    平衡很重要：貪婪填滿會斷出「⋯⋯後面每 / 一個數字都不可信」這種
    第二行只剩幾個字的版型，讀起來會頓一下。
    """
    lines = _greedy_wrap(text, max_cells)
    if balance and len(lines) == 2:
        target = max(dwidth(text) // 2 + 1, 8)
        for w in range(target, max_cells + 1):
            alt = _greedy_wrap(text, w)
            if len(alt) == 2 and all(dwidth(l) <= max_cells for l in alt):
                if _imbalance(alt) < _imbalance(lines):
                    lines = alt
                break
    return lines


def _imbalance(lines: list[str]) -> int:
    return abs(dwidth(lines[0]) - dwidth(lines[1]))


def _greedy_wrap(text: str, max_cells: int) -> list[str]:
    tokens = _TOKEN.findall(text)
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok.isspace() and not cur:
            continue
        if dwidth(cur + tok) > max_cells and cur:
            lines.append(cur.rstrip())
            cur = "" if tok.isspace() else tok
        else:
            cur += tok
    if cur.strip():
        lines.append(cur.rstrip())

    # 避頭尾修正：標點不可孤懸行首，開括號不可留在行尾
    fixed: list[str] = []
    for line in lines:
        if fixed and line and line[0] in PROHIBIT_START:
            fixed[-1] += line[0]          # 拉回上一行（容許超出 1 格）
            line = line[1:]
        if fixed and fixed[-1] and fixed[-1][-1] in PROHIBIT_END:
            line = fixed[-1][-1] + line   # 開括號推到下一行
            fixed[-1] = fixed[-1][:-1]
        if line:
            fixed.append(line)
    return fixed


# ---------------------------------------------------------------------------
# 文字繪製（run-based：一次畫一段同寬同色的文字）
# ---------------------------------------------------------------------------

def draw_cells(draw, x: int, baseline: int, text: str, cell_w: int,
               font, fill: str, hand_colors: bool = True) -> int:
    """在格線上繪製一段文字，回傳結束的 x 座標。

    手繪字元（✅❌）不交給字型 —— 本機無 emoji 字型，交給字型只會得到豆腐。
    其餘字元逐 run 繪製：同一個 run 內字寬一致，advance 精確等於 cell 寬的
    整數倍，一次 draw.text 就會自動落格（前提是 layout_engine=BASIC）。
    """
    run = ""
    run_x = x
    cx = x

    def flush():
        nonlocal run, run_x
        if run:
            draw.text((run_x, baseline), run, font=font, fill=fill, anchor="ls")
            run = ""

    for ch in text:
        if ch in ZERO_WIDTH:
            continue
        if ch in HAND_DRAWN:
            flush()
            kind, color = HAND_DRAWN[ch]
            _draw_mark(draw, cx, baseline, cell_w, font.size, kind,
                       color if hand_colors else fill)
            cx += cell_w * 2
            run_x = cx
            continue
        if not run:
            run_x = cx
        run += ch
        cx += cell_w * cells(ch)
    flush()
    return cx


def _draw_mark(draw, x: int, baseline: int, cell_w: int, size: int,
               kind: str, color: str) -> None:
    """手繪 ✅／❌／❗，畫在一個全寬（2 cell）的格子中央。"""
    w = cell_w * 2
    box = size * 0.58
    cx = x + w / 2
    cy = baseline - size * 0.34
    half = box / 2
    lw = max(2, int(size * 0.09))

    if kind == "check":
        draw.line([(cx - half * 0.85, cy + half * 0.05),
                   (cx - half * 0.20, cy + half * 0.70),
                   (cx + half * 0.90, cy - half * 0.75)],
                  fill=color, width=lw, joint="curve")
    elif kind == "cross":
        draw.line([(cx - half * 0.7, cy - half * 0.7),
                   (cx + half * 0.7, cy + half * 0.7)], fill=color, width=lw)
        draw.line([(cx + half * 0.7, cy - half * 0.7),
                   (cx - half * 0.7, cy + half * 0.7)], fill=color, width=lw)
    elif kind == "bang":
        draw.line([(cx, cy - half * 0.8), (cx, cy + half * 0.25)],
                  fill=color, width=lw)
        draw.ellipse([cx - lw / 2, cy + half * 0.55,
                      cx + lw / 2, cy + half * 0.55 + lw], fill=color)


# ---------------------------------------------------------------------------
# lint：把缺字與破格變成 build 失敗，而不是成品裡才發現
# ---------------------------------------------------------------------------

def lint_text(text: str, size: int = 34) -> list[str]:
    """檢查一段文字能否正確渲染。回傳問題清單（空 = 沒問題）。"""
    problems = []
    f = load(size)
    cell = size // 2
    for ch in dict.fromkeys(text):          # 去重但保序
        if ch in HAND_DRAWN or ch in ZERO_WIDTH or ch.isspace():
            continue
        if is_missing(ch, size):
            problems.append(
                f"缺字 {ch!r} (U+{ord(ch):04X}) —— 本機字型無此字，"
                f"需加進 HAND_DRAWN 或改寫文案")
            continue
        adv = f.getlength(ch)
        if adv not in (cell, cell * 2):
            problems.append(
                f"破格 {ch!r} (U+{ord(ch):04X}) advance={adv}，"
                f"應為 {cell} 或 {cell * 2} —— 會讓整條格線歪掉")
    return problems


if __name__ == "__main__":
    import sys
    idx = tc_face_index()
    f = load(34)
    print(f"TC face index = {idx}")
    print(f"字型          = {f.getname()}")
    print(f"ASCII advance = {f.getlength('A')}  CJK advance = {f.getlength('測')}")
    print(f"metrics       = {f.getmetrics()}")
    sample = " ".join(sys.argv[1:]) or "✅ 驗證通過 ❌ 失敗 ⚠️ 警告 → 100% PASS=7 FAIL=0"
    print(f"\nlint({sample!r}):")
    for p in lint_text(sample) or ["  無問題"]:
        print(f"  {p}")
    print(f"\ndwidth = {dwidth(sample)} cells")
    for line in wrap_cjk(sample, 22):
        print(f"  |{line}| ({dwidth(line)} cells)")
