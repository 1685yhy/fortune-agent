"""八字命盘图表生成 - Bazi chart generator with matplotlib."""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
from matplotlib.table import Table

# ── Chinese font setup ──────────────────────────────────────────────
_FONT_CANDIDATES = [
    "WenQuanYi Zen Hei",
    "SimHei",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "AR PL UMing CN",
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

CHARTS_DIR = Path("/mnt/d/fortune-data/charts")


def _save_or_return(fig: plt.Figure, output_path: Optional[str] = None) -> str:
    """Save figure to path (auto-generating if needed) and return the path."""
    if output_path is None:
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(CHARTS_DIR / f"bazi_{timestamp}.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return os.path.abspath(output_path)


class BaziChartGenerator:
    """八字命盘图表生成器"""

    def generate(self, result, output_path: Optional[str] = None) -> str:
        """生成八字命盘PNG图表

        Args:
            result: BaziResult 对象
            output_path: 输出文件路径，None时自动生成

        Returns:
            str: PNG文件路径
        """
        fig = plt.figure(figsize=(14, 10))
        fig.patch.set_facecolor("white")

        # ── Title ────────────────────────────────────────────────────
        geju = getattr(result, "geju", "")
        yongshen = getattr(result, "yongshen", "")
        title_str = f"八字命盘  |  {geju}  |  {yongshen}"
        fig.suptitle(title_str, fontsize=16, fontweight="bold", y=0.98,
                     fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        # ── Grid layout ──────────────────────────────────────────────
        # Upper table area
        ax_table = fig.add_axes([0.05, 0.52, 0.90, 0.38])
        ax_table.axis("off")

        # ── Pillars table ────────────────────────────────────────────
        pillars = ["年柱", "月柱", "日柱", "时柱"]
        bazi = getattr(result, "bazi", [])
        tiangan = [p[0] if len(p) > 0 else "" for p in bazi]
        dizhi = [p[1] if len(p) > 1 else "" for p in bazi]

        shishen = getattr(result, "shishen", [])
        # Pad shishen to 4 entries
        while len(shishen) < 4:
            shishen.append("")
        nayin = getattr(result, "nayin", [])
        while len(nayin) < 4:
            nayin.append("")

        # 藏干 (simplified)
        canggan_map = {
            "子": "癸", "丑": "己癸辛", "寅": "甲丙戊", "卯": "乙",
            "辰": "戊乙癸", "巳": "丙庚戊", "午": "丁己", "未": "己丁乙",
            "申": "庚壬戊", "酉": "辛", "戌": "戊辛丁", "亥": "壬甲",
        }
        canggan = [canggan_map.get(d, "") for d in dizhi]

        rows = ["四柱", "天干", "地支", "十神", "藏干", "纳音"]
        cell_text = [
            pillars,
            tiangan,
            dizhi,
            shishen,
            canggan,
            nayin,
        ]

        tbl = ax_table.table(
            cellText=cell_text,
            rowLabels=None,
            colLabels=None,
            loc="center",
            cellLoc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(12)
        tbl.scale(1, 2.5)

        # Style header column (row label)
        for i, rlabel in enumerate(rows):
            # Add row label as text on the left
            ax_table.text(
                0.02, 0.85 - i * 0.155, rlabel,
                fontsize=11, fontweight="bold", va="center",
                fontproperties=fm.FontProperties(family=_CHINESE_FONT),
            )

        # Color the cell backgrounds
        header_color = "#FFF3E0"
        even_color = "#FAFAFA"
        odd_color = "#F5F5F5"

        for i in range(len(rows)):
            for j in range(4):
                cell = tbl[i, j]
                if j == 2:  # 日柱 column - highlight
                    cell.set_facecolor("#E3F2FD")
                elif i == 3:  # 十神 row - highlight
                    cell.set_facecolor("#E8F5E9")
                else:
                    cell.set_facecolor(odd_color if (i + j) % 2 == 0 else even_color)

                # Bold for 日主
                if i == 3 and j == 2 and cell_text[i][j] == "日主":
                    cell.set_text_props(weight="bold", color="#1B5E20")

        # ── Wuxing bar chart ─────────────────────────────────────────
        ax_wx = fig.add_axes([0.05, 0.30, 0.40, 0.18])
        wuxing = getattr(result, "wuxing", {})
        wx_order = ["木", "火", "土", "金", "水"]
        wx_values = [wuxing.get(w, 0) for w in wx_order]
        wx_colors = {
            "木": "#4CAF50", "火": "#FF5722", "土": "#FFC107",
            "金": "#9E9E9E", "水": "#2196F3",
        }
        bar_colors = [wx_colors.get(w, "#999999") for w in wx_order]

        bars = ax_wx.bar(wx_order, wx_values, color=bar_colors, edgecolor="white", linewidth=1.2)
        ax_wx.set_title("五行分布", fontsize=13, fontweight="bold",
                        fontproperties=fm.FontProperties(family=_CHINESE_FONT))
        ax_wx.set_ylabel("数量", fontsize=10,
                         fontproperties=fm.FontProperties(family=_CHINESE_FONT))
        ax_wx.set_ylim(0, max(6, max(wx_values) + 2))

        for bar, val in zip(bars, wx_values):
            ax_wx.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                       str(val), ha="center", va="bottom", fontsize=11, fontweight="bold")

        ax_wx.spines["top"].set_visible(False)
        ax_wx.spines["right"].set_visible(False)

        # ── Shensha text ─────────────────────────────────────────────
        ax_ss = fig.add_axes([0.50, 0.30, 0.45, 0.18])
        ax_ss.axis("off")
        shensha = getattr(result, "shensha", [])
        shensha_text = "、".join(shensha) if shensha else "无"
        dayun_raw = getattr(result, "dayun", [])
        dayun_text = "  ".join([f"{a}岁:{g}" for a, g in dayun_raw[:6]])

        info_lines = [
            f"神煞: {shensha_text}",
            f"日主: {getattr(result, 'day_master', '')}",
            f"大运(前6): {dayun_text}",
        ]
        for idx, line in enumerate(info_lines):
            ax_ss.text(
                0.05, 0.75 - idx * 0.25, line,
                fontsize=10, va="top",
                fontproperties=fm.FontProperties(family=_CHINESE_FONT),
                wrap=True,
            )

        # ── Dayun timeline ───────────────────────────────────────────
        ax_dy = fig.add_axes([0.05, 0.05, 0.90, 0.20])
        ax_dy.axis("off")
        ax_dy.set_xlim(0, 1)
        ax_dy.set_ylim(0, 1)

        ax_dy.text(0.5, 0.90, "大运时间线", fontsize=13, fontweight="bold",
                   ha="center", va="top",
                   fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        dy_data = getattr(result, "dayun", [])
        if dy_data:
            total_years = 120
            max_age = min(dy_data[-1][0] + 10, total_years)
            # Normalize positions
            positions = []
            labels = []
            for age, ganzhi in dy_data:
                x = age / max_age if max_age > 0 else 0
                positions.append(x)
                labels.append(f"{ganzhi}\n{age}岁")

            # Draw timeline bar
            bar_y = 0.30
            bar_height = 0.08
            rect = mpatches.FancyBboxPatch(
                (0, bar_y), 1, bar_height,
                boxstyle="round,pad=0.02",
                facecolor="#E0E0E0", edgecolor="#BDBDBD",
            )
            ax_dy.add_patch(rect)

            # Dot and label for each dayun
            for x, label in zip(positions, labels):
                ax_dy.plot(x, bar_y + bar_height / 2, "o", color="#1565C0", markersize=10, zorder=5)
                ax_dy.text(x, bar_y - 0.10, label, ha="center", va="top", fontsize=8,
                           fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        return _save_or_return(fig, output_path)
