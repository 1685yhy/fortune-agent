"""紫微斗数命盘图表生成 - Ziwei chart generator with matplotlib."""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Rectangle

# ── Chinese font ────────────────────────────────────────────────────
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

CHARTS_DIR = Path("/opt/fortune-data/charts")


def _save_or_return(fig: plt.Figure, output_path: Optional[str] = None,
                    prefix: str = "ziwei") -> str:
    if output_path is None:
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(CHARTS_DIR / f"{prefix}_{timestamp}.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return os.path.abspath(output_path)


# Star classification
ZIWEI_XI = {"紫微", "天机", "太阳", "武曲", "天同", "廉贞"}
TIANFU_XI = {"天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"}
YANG_STARS = {"紫微", "太阳", "武曲", "天相", "七杀", "破军", "贪狼", "天府"}
YIN_STARS = {"天机", "天同", "太阴", "巨门", "天梁", "廉贞"}
ZIWEI_COLOR = "#D32F2F"      # Red for 紫微系
TIANFU_COLOR = "#1565C0"     # Blue for 天府系
AUX_COLOR = "#388E3C"        # Green for aux stars
SIHUA_COLORS = {
    "化禄": "#F44336",
    "化权": "#FF9800",
    "化科": "#4CAF50",
    "化忌": "#9E9E9E",
}


class ZiweiChartGenerator:
    """紫微斗数命盘图表生成器"""

    def generate(self, result, output_path: Optional[str] = None) -> str:
        """生成紫微斗数命盘PNG图表

        Args:
            result: ZiweiResult 对象
            output_path: 输出文件路径，None时自动生成

        Returns:
            str: PNG文件路径
        """
        fig = plt.figure(figsize=(16, 12))
        fig.patch.set_facecolor("white")

        # Title
        ming_gong = getattr(result, "ming_gong", "")
        shen_gong = getattr(result, "shen_gong", "")
        wuxing_ju = getattr(result, "wuxing_ju", "")
        fig.suptitle(
            f"紫微斗数命盘  |  命宫{ming_gong}  身宫{shen_gong}  五行局{wuxing_ju}",
            fontsize=16, fontweight="bold", y=0.98,
            fontproperties=fm.FontProperties(family=_CHINESE_FONT),
        )

        palaces = getattr(result, "palaces", {})
        sihua = getattr(result, "sihua", {})

        # ── 12-palace layout: 4 columns x 3 rows ────────────────────
        # Palace order in grid (top-left to bottom-right):
        # Row 0: [巳, 午, 未, 申]
        # Row 1: [辰,  -,  -, 酉]
        # Row 2: [卯,  -,  -, 戌]
        # Row 3: [寅, 丑, 子, 亥]
        # Center 4 cells are empty (above/below 中)
        # We place palaces by their earth branch

        # Build a lookup: palace_name -> (row, col)
        palace_grid = {}  # dizhi -> (row, col)
        grid_positions = [
            ("巳", 0, 0), ("午", 0, 1), ("未", 0, 2), ("申", 0, 3),
            ("辰", 1, 0),                      ("酉", 1, 3),
            ("卯", 2, 0),                      ("戌", 2, 3),
            ("寅", 3, 0), ("丑", 3, 1), ("子", 3, 2), ("亥", 3, 3),
        ]
        for dz, r, c in grid_positions:
            palace_grid[dz] = (r, c)

        # Map palace names to their dizhi
        # Create a mapping: palace_name -> (row, col)
        name_to_pos = {}
        for pname, pinfo in palaces.items():
            dz = pinfo.dizhi
            if dz in palace_grid:
                name_to_pos[pname] = palace_grid[dz]

        # Draw cells
        cell_w = 0.18
        cell_h = 0.18
        x_start = 0.08
        y_start = 0.08

        for pname, pinfo in palaces.items():
            dz = pinfo.dizhi
            if dz not in palace_grid:
                continue
            row, col = palace_grid[dz]
            x = x_start + col * cell_w
            y = y_start + (3 - row) * cell_h  # Flip Y so row 0 is top

            # Background color based on palace type
            if pname == "命宫":
                bg = "#FFF3E0"
            elif any(g in pname for g in ["财", "官", "田", "福"]):
                bg = "#E8F5E9"  # Auspicious palaces
            elif any(b in pname for b in ["兄", "子", "父"]):
                bg = "#E3F2FD"  # Family palaces
            else:
                bg = "#FAFAFA"

            # Draw palace rectangle
            rect = Rectangle(
                (x, y), cell_w, cell_h,
                facecolor=bg, edgecolor="#333333", linewidth=1.5,
            )
            fig.gca().add_patch(rect)

            # Palace name in top-left corner
            fp = fm.FontProperties(family=_CHINESE_FONT, size=8, weight="bold")
            fig.text(x + 0.005, y + cell_h - 0.025, pname,
                     fontproperties=fp, color="#333333", va="top", ha="left")

            # Earth branch in parentheses
            fp_small = fm.FontProperties(family=_CHINESE_FONT, size=6)
            fig.text(x + cell_w - 0.005, y + cell_h - 0.025, f"({dz})",
                     fontproperties=fp_small, color="#888888", va="top", ha="right")

            # Stars in this palace
            main_stars = pinfo.stars
            aux_stars = pinfo.aux_stars

            # Determine which stars have 四化 markers
            sihua_reverse = {v: k for k, v in sihua.items()}

            star_text_y = y + cell_h - 0.055
            for star in main_stars:
                if star in ZIWEI_XI:
                    star_color = ZIWEI_COLOR
                elif star in TIANFU_XI:
                    star_color = TIANFU_COLOR
                else:
                    star_color = "#333333"

                sihua_label = ""
                if star in sihua_reverse:
                    sihua_label = f" [{sihua_reverse[star]}]"

                fp_star = fm.FontProperties(family=_CHINESE_FONT, size=8)
                fig.text(x + 0.01, star_text_y,
                         f"{star}{sihua_label}",
                         fontproperties=fp_star, color=star_color,
                         va="top", ha="left")
                star_text_y -= 0.030

            # Aux stars (smaller)
            for aux in aux_stars:
                fp_aux = fm.FontProperties(family=_CHINESE_FONT, size=6)
                fig.text(x + 0.01, star_text_y, aux,
                         fontproperties=fp_aux, color=AUX_COLOR,
                         va="top", ha="left")
                star_text_y -= 0.020

        # Center label
        fig.text(x_start + 1.5 * cell_w, y_start + 1.5 * cell_h - 0.03,
                 "紫微斗数\n命盘",
                 ha="center", va="center",
                 fontproperties=fm.FontProperties(family=_CHINESE_FONT, size=14, weight="bold"),
                 color="#666666")

        # ── Legend ───────────────────────────────────────────────────
        legend_x = x_start + 4.2 * cell_w
        legend_y = y_start + 3 * cell_h

        fig.text(legend_x, legend_y, "图例", fontsize=12, fontweight="bold",
                 fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        legend_items = [
            ("紫微系主星", ZIWEI_COLOR),
            ("天府系主星", TIANFU_COLOR),
            ("辅星", AUX_COLOR),
            ("化禄", SIHUA_COLORS["化禄"]),
            ("化权", SIHUA_COLORS["化权"]),
            ("化科", SIHUA_COLORS["化科"]),
            ("化忌", SIHUA_COLORS["化忌"]),
        ]
        for li, (label, color) in enumerate(legend_items):
            ly = legend_y - 0.035 * (li + 1)
            fig.gca().add_patch(Rectangle(
                (legend_x, ly - 0.008), 0.025, 0.020,
                facecolor=color, edgecolor="none",
            ))
            fig.text(legend_x + 0.03, ly, label, fontsize=8,
                     va="center",
                     fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        # ── 四化 detail ─────────────────────────────────────────────
        detail_y = legend_y - 0.035 * (len(legend_items) + 2)
        fig.text(legend_x, detail_y, "四化详情", fontsize=10, fontweight="bold",
                 fontproperties=fm.FontProperties(family=_CHINESE_FONT))
        for si_idx, (sihua_name, star_name) in enumerate(sihua.items()):
            dy = detail_y - 0.030 * (si_idx + 1)
            fig.text(legend_x + 0.01, dy, f"{sihua_name}: {star_name}",
                     fontsize=8,
                     color=SIHUA_COLORS.get(sihua_name, "#333"),
                     fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        # ── 大限 info ────────────────────────────────────────────────
        dayun = getattr(result, "dayun", [])
        dayun_y = detail_y - 0.030 * (len(sihua) + 3)
        fig.text(legend_x, dayun_y, "大限", fontsize=10, fontweight="bold",
                 fontproperties=fm.FontProperties(family=_CHINESE_FONT))
        for dy_idx, (age, palace_name, dz) in enumerate(dayun[:8]):
            dy = dayun_y - 0.028 * (dy_idx + 1)
            fig.text(legend_x + 0.01, dy, f"{age}~{age+9}岁 {palace_name}({dz})",
                     fontsize=7,
                     fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        # Turn off the default axes
        ax = fig.gca()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

        # Remove unused axes
        for a in fig.axes:
            if a != ax:
                a.set_visible(False)

        return _save_or_return(fig, output_path, prefix="ziwei")
