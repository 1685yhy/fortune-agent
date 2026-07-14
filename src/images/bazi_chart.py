"""八字命盘图表生成 - Premium commercial-grade Bazi chart generator.

Output: 2400x1600 PNG at 150 DPI — a dark, luxury-financial-dashboard aesthetic
with warm gold accents (#d4a853) on a deep GitHub-dark background (#0d1117→#161b22).
"""

import math
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import numpy as np

# ── Chinese font setup ──────────────────────────────────────────────
_FONT_CANDIDATES = [
    "WenQuanYi Zen Hei",
    "SimHei",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "AR PL UMing CN",
    "Source Han Sans CN",
    "Source Han Serif CN",
    "Noto Serif CJK SC",
    "Droid Sans Fallback",
]
_CHINESE_FONT = None
for name in _FONT_CANDIDATES:
    try:
        prop = fm.FontProperties(family=name)
        if prop.get_name() != "sans-serif":
            _CHINESE_FONT = name
            break
    except Exception:
        continue
if _CHINESE_FONT is None:
    _CHINESE_FONT = "sans-serif"

plt.rcParams["font.family"] = _CHINESE_FONT
plt.rcParams["axes.unicode_minus"] = False

# ── Premium colour palette ──────────────────────────────────────────
BG_PRIMARY = "#0d1117"
BG_SECONDARY = "#161b22"
BG_CARD = "#1c2333"
BG_CARD_HOVER = "#212b42"
GOLD = "#d4a853"
GOLD_LIGHT = "#e8c97a"
GOLD_DIM = "#b8943d"
GOLD_MUTED = "#5c4a28"
WHITE = "#f0f0f0"
WHITE_DIM = "#8b949e"
WHITE_MUTED = "#484f58"
BORDER_SUBTLE = "#21262d"
BORDER_MUTED = "#30363d"

# 五行 colours (vibrant, premium palette)
WUXING_COLORS = {
    "金": "#e2e8f0",
    "木": "#4ade80",
    "水": "#60a5fa",
    "火": "#f87171",
    "土": "#fbbf24",
}
WUXING_COLORS_DIM = {
    "金": "#334155",
    "木": "#14532d",
    "水": "#1e3a5f",
    "火": "#7f1d1d",
    "土": "#713f12",
}

# Canvas
W = 2400
H = 1600
DPI = 150

CHARTS_DIR = Path("/mnt/d/fortune-data/charts")

# ── 藏干 mapping ────────────────────────────────────────────────────
CANGGAN_MAP = {
    "子": "癸", "丑": "己癸辛", "寅": "甲丙戊", "卯": "乙",
    "辰": "戊乙癸", "巳": "丙庚戊", "午": "丁己", "未": "己丁乙",
    "申": "庚壬戊", "酉": "辛", "戌": "戊辛丁", "亥": "壬甲",
}

TG_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火",
    "戊": "土", "己": "土", "庚": "金", "辛": "金",
    "壬": "水", "癸": "水",
}

DZ_WUXING = {
    "寅": "木", "卯": "木", "巳": "火", "午": "火",
    "辰": "土", "戌": "土", "丑": "土", "未": "土",
    "申": "金", "酉": "金", "子": "水", "亥": "水",
}


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _lighten(color: str, factor: float = 0.3) -> tuple:
    rgb = _hex_to_rgb(color)
    return tuple(min(1.0, c + (1.0 - c) * factor) for c in rgb)


def _darken(color: str, factor: float = 0.3) -> tuple:
    rgb = _hex_to_rgb(color)
    return tuple(max(0.0, c - c * factor) for c in rgb)


# ═════════════════════════════════════════════════════════════════════
#  Drawing helpers
# ═════════════════════════════════════════════════════════════════════

def _draw_premium_bg(fig: plt.Figure) -> None:
    """White outer margin + dark gradient only inside the gold frame."""
    ax = fig.add_axes([0, 0, 1, 1], zorder=0)
    ax.axis("off")

    # White background card (outer area)
    outer_card = mpatches.FancyBboxPatch(
        (0, 0), 1, 1,
        boxstyle="round,pad=0.02",
        facecolor="white", edgecolor="none", linewidth=0,
    )
    ax.add_patch(outer_card)

    # Dark content area (inside the gold border zone)
    margin = 0.020
    c1 = _hex_to_rgb(BG_PRIMARY)
    c2 = _hex_to_rgb(BG_SECONDARY)

    # Build dark area via vertical strips
    inner_left = margin
    inner_right = 1 - margin
    inner_bottom = margin
    inner_top = 1 - margin
    n = 80
    for i in range(n):
        t = i / n
        color = tuple(c1[j] * (1 - t) + c2[j] * t for j in range(3))
        y0 = inner_bottom + (inner_top - inner_bottom) * (i / n)
        y1 = inner_bottom + (inner_top - inner_bottom) * ((i + 1) / n)
        ax.axhspan(y0, y1, inner_left, inner_right,
                    facecolor=color, linewidth=0)


def _draw_outer_frame(fig: plt.Figure) -> None:
    """White outer canvas + elegant gold border (premium card mount)."""
    ax = fig.add_axes([0, 0, 1, 1], zorder=999)
    ax.axis("off")

    # Outer gold border
    m = 0.018
    rect = mpatches.FancyBboxPatch(
        (m, m), 1 - 2 * m, 1 - 2 * m,
        boxstyle="round,pad=0.008",
        facecolor="none", edgecolor=GOLD, linewidth=2.5,
    )
    ax.add_patch(rect)

    # Inner thin gold border
    m2 = m + 0.010
    rect2 = mpatches.FancyBboxPatch(
        (m2, m2), 1 - 2 * m2, 1 - 2 * m2,
        boxstyle="round,pad=0.007",
        facecolor="none", edgecolor=GOLD_DIM, linewidth=0.8, alpha=0.5,
    )
    ax.add_patch(rect2)

    # Corner ornaments
    corner_margin = 0.035
    for cx, cy in [(corner_margin, corner_margin),
                    (1 - corner_margin, corner_margin),
                    (corner_margin, 1 - corner_margin),
                    (1 - corner_margin, 1 - corner_margin)]:
        c = mpatches.Circle(
            (cx, cy), radius=0.006,
            facecolor=GOLD, edgecolor=_lighten(GOLD, 0.3),
            linewidth=0.8, transform=ax.transAxes,
        )
        ax.add_patch(c)


# ═════════════════════════════════════════════════════════════════════
#  1. Header — top 12%
# ═════════════════════════════════════════════════════════════════════

def _draw_header(fig: plt.Figure, result) -> None:
    """Large '八字命盘' title with gold underline + subtitle row at top."""
    ax = fig.add_axes([0.03, 0.86, 0.94, 0.13], zorder=10)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Pull fields
    geju = getattr(result, "geju", "")
    yongshen = getattr(result, "yongshen", "")
    day_master = getattr(result, "day_master", "")

    # ── Main title ──
    fp_title = fm.FontProperties(family=_CHINESE_FONT, size=54, weight="bold")
    ax.text(0.50, 0.72, "八 字 命 盘", ha="center", va="center",
            color=GOLD, fontproperties=fp_title)

    # Gold underline accent bar (wide + narrow center)
    ax.plot([0.35, 0.65], [0.50, 0.50], color=GOLD_MUTED, linewidth=2,
            alpha=0.4, transform=ax.transAxes)
    ax.plot([0.42, 0.58], [0.50, 0.50], color=GOLD, linewidth=3,
            alpha=0.9, transform=ax.transAxes)

    # ── Subtitle ──
    parts = []
    if day_master:
        parts.append(f"日主: {day_master}")
    if geju:
        parts.append(f"格局: {geju}")
    if yongshen:
        parts.append(f"用神: {yongshen}")
    subtitle = "  |  ".join(parts) if parts else "命理分析"

    fp_sub = fm.FontProperties(family=_CHINESE_FONT, size=16)
    ax.text(0.50, 0.30, subtitle, ha="center", va="center",
            color=GOLD_LIGHT, fontproperties=fp_sub)

    # ── Date (right-aligned) ──
    now_str = datetime.now().strftime("%Y年 %m月 %d日")
    fp_date = fm.FontProperties(family=_CHINESE_FONT, size=11)
    ax.text(0.94, 0.12, now_str, ha="right", va="bottom",
            color=WHITE_DIM, fontproperties=fp_date)


# ═════════════════════════════════════════════════════════════════════
#  2. Core pillar cards  +  Side panel  (12–60 % from top)
# ═════════════════════════════════════════════════════════════════════

def _draw_pillar_cards(fig: plt.Figure, result) -> None:
    """Four pillar cards (left ~70 %) — each with wuxing-coloured tiangan."""
    ax = fig.add_axes([0.03, 0.50, 0.66, 0.36], zorder=10)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    bazi = getattr(result, "bazi", []) or []
    tiangan = [p[0] if len(p) > 0 else "" for p in bazi]
    dizhi = [p[1] if len(p) > 1 else "" for p in bazi]
    # Pad to 4 pillars
    while len(tiangan) < 4:
        tiangan.append("")
    while len(dizhi) < 4:
        dizhi.append("")

    shishen = getattr(result, "shishen", []) or []
    nayin = getattr(result, "nayin", []) or []
    while len(shishen) < 4:
        shishen.append("")
    while len(nayin) < 4:
        nayin.append("")

    hidden_gan = getattr(result, "hidden_gan", []) or []
    canggan = []
    for i, d in enumerate(dizhi):
        if i < len(hidden_gan):
            canggan.append(hidden_gan[i])
        else:
            canggan.append(CANGGAN_MAP.get(d, ""))

    pillar_labels = ["年 柱", "月 柱", "日 柱", "时 柱"]

    n_cards = 4
    card_w = 0.205
    card_h = 0.88
    gap = 0.035
    total_w = n_cards * card_w + (n_cards - 1) * gap
    start_x = (1.0 - total_w) / 2
    card_y = 0.06

    for i in range(4):
        cx = start_x + i * (card_w + gap)
        is_day = i == 2

        # ── Shadow layer ──
        for si in (3, 2, 1):
            shadow = mpatches.FancyBboxPatch(
                (cx + 0.003 * si, card_y - 0.003 * si), card_w, card_h,
                boxstyle="round,pad=0.015",
                facecolor="black", edgecolor="none", linewidth=0,
                alpha=0.04 + 0.02 * si,
            )
            ax.add_patch(shadow)

        # ── Card background ──
        face = BG_CARD if not is_day else "#1e2840"
        border = BORDER_MUTED if not is_day else GOLD_DIM
        border_w = 1.0 if not is_day else 1.5

        card = mpatches.FancyBboxPatch(
            (cx, card_y), card_w, card_h,
            boxstyle="round,pad=0.015",
            facecolor=face, edgecolor=border, linewidth=border_w,
        )
        ax.add_patch(card)

        # ── Day pillar: multi-ring gold glow ──
        if is_day:
            for gi in range(3):
                ga = 0.10 - gi * 0.03
                gw = 0.018 + gi * 0.010
                g = mpatches.FancyBboxPatch(
                    (cx - gw, card_y - gw), card_w + 2 * gw, card_h + 2 * gw,
                    boxstyle="round,pad=0.02",
                    facecolor="none", edgecolor=GOLD, linewidth=gi + 0.5,
                    alpha=max(ga, 0.01),
                )
                ax.add_patch(g)

        # ── Pillar label ──
        fp_p = fm.FontProperties(family=_CHINESE_FONT, size=12, weight="bold")
        ax.text(cx + card_w / 2, card_y + card_h - 0.035, pillar_labels[i],
                ha="center", va="top", color=GOLD, fontproperties=fp_p)

        # ── 天干 (large, colour-coded by wuxing) ──
        tg = tiangan[i]
        wx = TG_WUXING.get(tg, "")
        tg_color = WUXING_COLORS.get(wx, WHITE)
        fp_tg = fm.FontProperties(family=_CHINESE_FONT, size=44, weight="bold")
        ax.text(cx + card_w / 2, card_y + card_h * 0.72, tg,
                ha="center", va="center", color=tg_color, fontproperties=fp_tg)

        # ── 地支 (smaller, also wuxing-coloured) ──
        dz = dizhi[i]
        wx_dz = DZ_WUXING.get(dz, "")
        dz_color = WUXING_COLORS.get(wx_dz, GOLD)
        fp_dz = fm.FontProperties(family=_CHINESE_FONT, size=32, weight="bold")
        ax.text(cx + card_w / 2, card_y + card_h * 0.48, dz,
                ha="center", va="center", color=dz_color, fontproperties=fp_dz)

        # ── 十神 ──
        ss = shishen[i] if i < len(shishen) else ""
        fp_ss = fm.FontProperties(family=_CHINESE_FONT, size=12)
        ax.text(cx + card_w / 2, card_y + card_h * 0.28, ss,
                ha="center", va="center", color=WHITE_DIM, fontproperties=fp_ss)

        # ── 纳音 ──
        ny = nayin[i] if i < len(nayin) else ""
        fp_ny = fm.FontProperties(family=_CHINESE_FONT, size=10)
        ax.text(cx + card_w / 2, card_y + card_h * 0.15, ny,
                ha="center", va="center", color=GOLD_DIM, fontproperties=fp_ny)

        # ── 藏干 ──
        cg = canggan[i] if i < len(canggan) else ""
        if cg:
            fp_cg = fm.FontProperties(family=_CHINESE_FONT, size=9)
            ax.text(cx + card_w / 2, card_y + card_h * 0.06,
                    f"藏: {cg}", ha="center", va="center",
                    color=WHITE_MUTED, fontproperties=fp_cg)

        # ── Small wuxing dot (top-right corner of card) ──
        if wx:
            ax.scatter([cx + card_w - 0.025], [card_y + card_h - 0.025],
                       color=WUXING_COLORS.get(wx, WHITE_DIM),
                       s=18, alpha=0.7, transform=ax.transAxes, zorder=15)

        # ── Subtle separator below 十神 ──
        sep_y = card_y + card_h * 0.22
        ax.plot([cx + 0.035, cx + card_w - 0.035], [sep_y, sep_y],
                color=WHITE_MUTED, linewidth=0.5, alpha=0.25)


def _draw_side_panel(fig: plt.Figure, result) -> None:
    """Right sidebar (~30 %) — day-master circle, geju, yongshen, shensha."""
    ax = fig.add_axes([0.72, 0.50, 0.25, 0.36], zorder=10)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Panel card background
    bg = mpatches.FancyBboxPatch(
        (0, 0), 1, 1,
        boxstyle="round,pad=0.02",
        facecolor=BG_CARD, edgecolor=BORDER_MUTED, linewidth=0.8,
    )
    ax.add_patch(bg)

    day_master = getattr(result, "day_master", "")

    # ── Day-master circle ──
    if day_master:
        wx = TG_WUXING.get(day_master, "")
        dm_color = WUXING_COLORS.get(wx, GOLD)

        circle_bg = mpatches.Circle(
            (0.50, 0.80), 0.14,
            facecolor=BG_CARD_HOVER, edgecolor=dm_color, linewidth=2.5,
        )
        ax.add_patch(circle_bg)

        # Inner glow ring
        inner_ring = mpatches.Circle(
            (0.50, 0.80), 0.12,
            facecolor="none", edgecolor=_lighten(dm_color, 0.4),
            linewidth=0.8, alpha=0.4,
        )
        ax.add_patch(inner_ring)

        fp_dm = fm.FontProperties(family=_CHINESE_FONT, size=40, weight="bold")
        ax.text(0.50, 0.80, day_master, ha="center", va="center",
                color=dm_color, fontproperties=fp_dm)

        fp_label = fm.FontProperties(family=_CHINESE_FONT, size=10)
        ax.text(0.50, 0.91, "日 主", ha="center", va="center",
                color=WHITE_DIM, fontproperties=fp_label)

    # ── 格局 ──
    geju = getattr(result, "geju", "")
    if geju:
        fp_sec = fm.FontProperties(family=_CHINESE_FONT, size=10, weight="bold")
        ax.text(0.12, 0.63, "格 局", ha="left", va="center",
                color=GOLD_DIM, fontproperties=fp_sec)

        badge = mpatches.FancyBboxPatch(
            (0.10, 0.54), 0.80, 0.078,
            boxstyle="round,pad=0.01",
            facecolor=GOLD_MUTED, edgecolor=GOLD_DIM, linewidth=0.8, alpha=0.6,
        )
        ax.add_patch(badge)

        fp_geju = fm.FontProperties(family=_CHINESE_FONT, size=14, weight="bold")
        ax.text(0.50, 0.579, geju, ha="center", va="center",
                color=GOLD_LIGHT, fontproperties=fp_geju)

    # ── 用神 ──
    yongshen = getattr(result, "yongshen", "")
    if yongshen:
        fp_sec = fm.FontProperties(family=_CHINESE_FONT, size=10, weight="bold")
        ax.text(0.12, 0.45, "用 神", ha="left", va="center",
                color=GOLD_DIM, fontproperties=fp_sec)

        ys_wx = TG_WUXING.get(yongshen, "") or DZ_WUXING.get(yongshen, "")
        ys_color = WUXING_COLORS.get(ys_wx, GOLD)
        ys_dim = WUXING_COLORS_DIM.get(ys_wx, GOLD_MUTED)

        badge = mpatches.FancyBboxPatch(
            (0.10, 0.36), 0.80, 0.078,
            boxstyle="round,pad=0.01",
            facecolor=ys_dim, edgecolor=ys_color, linewidth=0.8, alpha=0.7,
        )
        ax.add_patch(badge)

        fp_ys = fm.FontProperties(family=_CHINESE_FONT, size=14, weight="bold")
        ax.text(0.50, 0.399, yongshen, ha="center", va="center",
                color=ys_color, fontproperties=fp_ys)

    # ── 神煞 ──
    shensha = getattr(result, "shensha", [])
    if shensha:
        fp_sec = fm.FontProperties(family=_CHINESE_FONT, size=10, weight="bold")
        ax.text(0.12, 0.29, "神 煞", ha="left", va="center",
                color=GOLD_DIM, fontproperties=fp_sec)

        max_show = min(len(shensha), 5)
        for idx in range(max_show):
            by = 0.20 - idx * 0.045
            badge = mpatches.FancyBboxPatch(
                (0.10, by), 0.80, 0.037,
                boxstyle="round,pad=0.008",
                facecolor="none", edgecolor=WHITE_MUTED, linewidth=0.5, alpha=0.4,
            )
            ax.add_patch(badge)

            fp_ss = fm.FontProperties(family=_CHINESE_FONT, size=10)
            ax.text(0.50, by + 0.0185, shensha[idx],
                    ha="center", va="center",
                    color=WHITE_DIM, fontproperties=fp_ss)

        if len(shensha) > 5:
            fp_more = fm.FontProperties(family=_CHINESE_FONT, size=9)
            ax.text(0.50, 0.20 - 5 * 0.045, f"+{len(shensha) - 5}",
                    ha="center", va="center",
                    color=GOLD_DIM, fontproperties=fp_more)


# ═════════════════════════════════════════════════════════════════════
#  3. Wu Xing energy bars (60–75 % from top)
# ═════════════════════════════════════════════════════════════════════

def _draw_wuxing_bars(fig: plt.Figure, result) -> None:
    """Five horizontal energy bars with glossy highlights."""
    ax = fig.add_axes([0.03, 0.30, 0.94, 0.19], zorder=10)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Section title
    fp_sec = fm.FontProperties(family=_CHINESE_FONT, size=16, weight="bold")
    ax.text(0.03, 0.92, "五行能量", ha="left", va="top",
            color=GOLD, fontproperties=fp_sec)
    ax.plot([0.03, 0.20], [0.84, 0.84], color=GOLD_DIM, linewidth=1,
            alpha=0.5, transform=ax.transAxes)

    wuxing = getattr(result, "wuxing", {})
    wx_order = ["木", "火", "土", "金", "水"]
    max_val = max(wuxing.get(w, 0) for w in wx_order) or 1
    max_val = max(max_val, 4)

    bar_h = 0.11
    bar_gap = 0.03
    bar_left = 0.16
    bar_right = 0.88
    bar_range = bar_right - bar_left
    bar_top = 0.80

    for i, wn in enumerate(wx_order):
        val = wuxing.get(wn, 0)
        frac = min(val / max_val, 1.0)
        by = bar_top - i * (bar_h + bar_gap)
        color = WUXING_COLORS[wn]
        dim_color = WUXING_COLORS_DIM.get(wn, BG_CARD)

        # Track background
        track = mpatches.FancyBboxPatch(
            (bar_left, by), bar_range, bar_h,
            boxstyle="round,pad=0.006",
            facecolor=BG_CARD, edgecolor=BORDER_SUBTLE, linewidth=0.5,
        )
        ax.add_patch(track)

        # Filled bar
        fill_w = bar_range * frac
        if fill_w > 0.002:
            bar = mpatches.FancyBboxPatch(
                (bar_left, by), fill_w, bar_h,
                boxstyle="round,pad=0.006",
                facecolor=color, edgecolor=_lighten(color, 0.2),
                linewidth=0.5, alpha=0.85,
            )
            ax.add_patch(bar)

            # Glossy highlight overlay (top half of bar)
            hl = mpatches.FancyBboxPatch(
                (bar_left + 0.002, by + bar_h * 0.60),
                max(fill_w - 0.004, 0), bar_h * 0.35,
                boxstyle="round,pad=0.004",
                facecolor=_lighten(color, 0.4),
                edgecolor="none", linewidth=0, alpha=0.25,
            )
            ax.add_patch(hl)

        # Label
        fp_lbl = fm.FontProperties(family=_CHINESE_FONT, size=14, weight="bold")
        ax.text(bar_left - 0.040, by + bar_h / 2, wn,
                ha="center", va="center", color=color,
                fontproperties=fp_lbl)

        # Value
        fp_val = fm.FontProperties(family=_CHINESE_FONT, size=13, weight="bold")
        ax.text(bar_left + fill_w + 0.015, by + bar_h / 2, str(val),
                ha="left", va="center", color=WHITE,
                fontproperties=fp_val)

        # Small wuxing character on right cap (decorative)
        if fill_w > 0.08:
            fp_cap = fm.FontProperties(family=_CHINESE_FONT, size=10)
            cap_x = bar_left + fill_w - 0.025
            ax.text(cap_x, by + bar_h / 2, wn, ha="center", va="center",
                    color=_lighten(color, 0.5), fontproperties=fp_cap, alpha=0.4)


# ═════════════════════════════════════════════════════════════════════
#  4. Dayun timeline (75–90 % from top)
# ═════════════════════════════════════════════════════════════════════

def _draw_dayun_timeline(fig: plt.Figure, result) -> None:
    """Decade-by-decade visual timeline with diamond markers."""
    ax = fig.add_axes([0.03, 0.10, 0.94, 0.19], zorder=10)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Section title
    fp_sec = fm.FontProperties(family=_CHINESE_FONT, size=16, weight="bold")
    ax.text(0.03, 0.92, "大运流年", ha="left", va="top",
            color=GOLD, fontproperties=fp_sec)
    ax.plot([0.03, 0.20], [0.84, 0.84], color=GOLD_DIM, linewidth=1,
            alpha=0.5, transform=ax.transAxes)

    dy_data = getattr(result, "dayun", [])
    if not dy_data:
        fp_none = fm.FontProperties(family=_CHINESE_FONT, size=14)
        ax.text(0.50, 0.50, "暂无大运信息",
                ha="center", va="center", color=WHITE_DIM,
                fontproperties=fp_none)
        return

    # Timeline coords
    tl_y = 0.35
    tl_h = 0.14
    tl_left = 0.10
    tl_right = 0.92
    tl_range = tl_right - tl_left

    ages = [d[0] for d in dy_data if isinstance(d, (list, tuple)) and len(d) >= 2]
    if not ages:
        return
    max_age = max(ages)
    max_display = max(max_age, 100) * 1.1

    # Track background
    track = mpatches.FancyBboxPatch(
        (tl_left, tl_y), tl_range, tl_h,
        boxstyle="round,pad=0.015",
        facecolor=BG_CARD, edgecolor=BORDER_MUTED, linewidth=0.8,
    )
    ax.add_patch(track)

    # Inner glow line
    inner = mpatches.FancyBboxPatch(
        (tl_left + 0.005, tl_y + tl_h * 0.38),
        tl_range - 0.01, tl_h * 0.24,
        boxstyle="round,pad=0.005",
        facecolor=GOLD_DIM, edgecolor="none", linewidth=0, alpha=0.15,
    )
    ax.add_patch(inner)

    # ── Draw segments + nodes ──
    for idx, d in enumerate(dy_data):
        if not isinstance(d, (list, tuple)) or len(d) < 2:
            continue
        age, ganzhi = d[0], d[1]
        x = tl_left + (age / max_display) * tl_range

        # Connecting line
        if idx > 0:
            prev = dy_data[idx - 1]
            if isinstance(prev, (list, tuple)) and len(prev) >= 2:
                px = tl_left + (prev[0] / max_display) * tl_range
                ax.plot([px, x], [tl_y + tl_h / 2, tl_y + tl_h / 2],
                        color=GOLD_DIM, linewidth=1.8, alpha=0.5)

        # Outer diamond
        diamond = mpatches.RegularPolygon(
            (x, tl_y + tl_h / 2),
            numVertices=4, radius=0.020,
            orientation=math.pi / 4,
            facecolor=GOLD, edgecolor=GOLD_LIGHT, linewidth=1,
        )
        ax.add_patch(diamond)

        # Inner diamond (smaller, same colour as track)
        inner_d = mpatches.RegularPolygon(
            (x, tl_y + tl_h / 2),
            numVertices=4, radius=0.007,
            orientation=math.pi / 4,
            facecolor=GOLD_LIGHT, edgecolor="none", linewidth=0,
        )
        ax.add_patch(inner_d)

        # Ganzhi above
        fp_lbl = fm.FontProperties(family=_CHINESE_FONT, size=12, weight="bold")
        ax.text(x, tl_y + tl_h + 0.07, ganzhi,
                ha="center", va="bottom", color=GOLD,
                fontproperties=fp_lbl)

        # Age below
        fp_age = fm.FontProperties(family=_CHINESE_FONT, size=10)
        ax.text(x, tl_y - 0.06, f"{age}岁",
                ha="center", va="top", color=WHITE_DIM,
                fontproperties=fp_age)

    # Dashed trail to end
    if dy_data:
        last = dy_data[-1]
        if isinstance(last, (list, tuple)):
            end_x = tl_left + (last[0] / max_display) * tl_range
            ax.plot([end_x, tl_right], [tl_y + tl_h / 2, tl_y + tl_h / 2],
                    color=GOLD_DIM, linewidth=1, alpha=0.2, linestyle="--")


# ═════════════════════════════════════════════════════════════════════
#  5. Footer (90–100 %)
# ═════════════════════════════════════════════════════════════════════

def _draw_footer(fig: plt.Figure) -> None:
    """Classical quote + brand name separated by a fine line."""
    ax = fig.add_axes([0.03, 0.00, 0.94, 0.09], zorder=10)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Separator
    ax.plot([0.05, 0.95], [0.88, 0.88], color=BORDER_SUBTLE, linewidth=0.5,
            transform=ax.transAxes)

    # Quote of the day
    quotes = [
        "一命二运三风水，四积阴德五读书",
        "命里有时终须有，命里无时莫强求",
        "君子知命，达人知运",
        "天道酬勤，地道酬善",
        "顺天应时，知运掌命",
    ]
    idx = datetime.now().day % len(quotes)

    fp_q = fm.FontProperties(family=_CHINESE_FONT, size=13, style="italic")
    ax.text(0.50, 0.62, f"「{quotes[idx]}」",
            ha="center", va="center", color=GOLD_DIM,
            fontproperties=fp_q, alpha=0.55)

    # Brand
    fp_b = fm.FontProperties(family=_CHINESE_FONT, size=11, weight="bold")
    ax.text(0.50, 0.20, "易 理 明 灯",
            ha="center", va="center", color=GOLD_MUTED,
            fontproperties=fp_b, alpha=0.7)

    # Decorative dots flanking brand
    for p in (0.35, 0.42, 0.58, 0.65):
        ax.scatter([p], [0.20], color=GOLD_DIM, s=5, alpha=0.25,
                   transform=ax.transAxes)


# ═════════════════════════════════════════════════════════════════════
#  Save helper
# ═════════════════════════════════════════════════════════════════════

def _save_or_return(fig: plt.Figure, output_path: Optional[str] = None) -> str:
    """Save figure to given path (or auto-generate) and return absolute path."""
    if output_path is None:
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(CHARTS_DIR / f"bazi_{timestamp}.png")
    fig.savefig(
        output_path, dpi=DPI, bbox_inches="tight",
        facecolor="white", edgecolor="none",
        pad_inches=0.015,
    )
    plt.close(fig)
    return os.path.abspath(output_path)


# ═════════════════════════════════════════════════════════════════════
#  Main generator class
# ═════════════════════════════════════════════════════════════════════

class BaziChartGenerator:
    """八字命盘图表生成器 — premium commercial-grade BaZi chart."""

    def generate(self, result, output_path: Optional[str] = None) -> str:
        """Generate a premium shareable BaZi chart as PNG.

        Args:
            result: BaziResult object with attributes (bazi, day_master, geju,
                    yongshen, wuxing, shishen, nayin, hidden_gan, dayun, shensha).
            output_path: Output file path. Auto-generated if None.

        Returns:
            Absolute PNG file path.
        """
        fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
        fig.patch.set_facecolor("white")

        # ── Layers (bottom to top) ──
        _draw_premium_bg(fig)                # Dark gradient
        _draw_header(fig, result)            # 1. Title
        _draw_pillar_cards(fig, result)      # 2a. Four pillar cards
        _draw_side_panel(fig, result)        # 2b. Side panel
        _draw_wuxing_bars(fig, result)       # 3. Energy bars
        _draw_dayun_timeline(fig, result)    # 4. Life timeline
        _draw_footer(fig)                    # 5. Quote + brand
        _draw_outer_frame(fig)               # Gold border (topmost)

        return _save_or_return(fig, output_path)
