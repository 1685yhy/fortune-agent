"""风水罗盘图表生成 - Fengshui chart generator with matplotlib."""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Rectangle, FancyBboxPatch

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

# Auspicious/inauspicious combinations
# (运星, 向星, 山星) - simplified heuristics
# In Flying Stars, 生入(山/向生运) and 克入(运克山/向) are auspicious
# 生出(运生山/向) and 克入(山/向克运), 反吟伏吟 are inauspicious
_WUXING_CYCLE = {1: "水", 2: "土", 3: "木", 4: "木", 5: "土",
                  6: "金", 7: "金", 8: "土", 9: "火"}
_WUXING_SHENG = {"水": "木", "木": "火", "火": "土", "土": "金", "金": "水"}
_WUXING_KE = {"水": "火", "火": "金", "金": "木", "木": "土", "土": "水"}

# 当运星
_CURRENT_PERIOD = 9


def _auspiciousness(yun: int, shan: int, xiang: int) -> str:
    """Evaluate if the star combination is auspicious or inauspicious.

    Returns: 'auspicious', 'inauspicious', or 'neutral'
    """
    yun_wx = _WUXING_CYCLE.get(yun, "")
    shan_wx = _WUXING_CYCLE.get(shan, "")
    xiang_wx = _WUXING_CYCLE.get(xiang, "")

    if not yun_wx or not shan_wx or not xiang_wx:
        return "neutral"

    # 当旺之星 (运星 == current period)
    if yun in (_CURRENT_PERIOD,):
        return "auspicious"

    # 山星生运星 (生入) = auspicious
    if _WUXING_SHENG.get(shan_wx) == yun_wx:
        return "auspicious"
    # 向星生运星 (生入) = auspicious
    if _WUXING_SHENG.get(xiang_wx) == yun_wx:
        return "auspicious"

    # 运星克山/向星 (克出) = neutral
    # 山/向星克运星 (克入) = inauspicious
    if _WUXING_KE.get(shan_wx) == yun_wx:
        return "neutral"
    if _WUXING_KE.get(xiang_wx) == yun_wx:
        return "neutral"
    if _WUXING_KE.get(yun_wx) == shan_wx or _WUXING_KE.get(yun_wx) == xiang_wx:
        return "inauspicious"

    # 反吟伏吟 - same numbers
    if yun == shan == xiang:
        return "inauspicious"

    return "neutral"


def _save_or_return(fig: plt.Figure, output_path: Optional[str] = None,
                    prefix: str = "fengshui") -> str:
    if output_path is None:
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(CHARTS_DIR / f"{prefix}_{timestamp}.png")
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return os.path.abspath(output_path)


# 九宫八卦名 in display order (3x3 grid, top-left to bottom-right)
PALACE_ORDER_3x3 = ["巽", "离", "坤", "震", "中", "兑", "艮", "坎", "乾"]
DIRECTION_LABELS = {
    "巽": "东南", "离": "南", "坤": "西南",
    "震": "东", "中": "中宫", "兑": "西",
    "艮": "东北", "坎": "北", "乾": "西北",
}

# 后天八卦 number
TRIGRAM_NUM = {"坎": 1, "坤": 2, "震": 3, "巽": 4, "中": 5, "乾": 6, "兑": 7, "艮": 8, "离": 9}


class FengshuiChartGenerator:
    """风水九宫格图表生成器"""

    def generate(self, result, output_path: Optional[str] = None) -> str:
        """生成风水九宫飞星PNG图表

        Args:
            result: FengshuiResult 对象
            output_path: 输出文件路径，None时自动生成

        Returns:
            str: PNG文件路径
        """
        fig = plt.figure(figsize=(12, 10))
        fig.patch.set_facecolor("white")

        # Title
        house_gua = getattr(result, "house_gua", "")
        period = getattr(result, "period", "")
        person_gua = getattr(result, "person_gua", "")
        title_parts = [f"玄空飞星九宫图"]
        if house_gua:
            title_parts.append(house_gua)
        if person_gua:
            title_parts.append(f"命卦{person_gua}")
        if period:
            title_parts.append(f"{period}运")

        fig.suptitle("  |  ".join(title_parts), fontsize=16, fontweight="bold", y=0.97,
                     fontproperties=fm.FontProperties(family=_CHINESE_FONT))

        # ── 3x3 Grid: 九宫格 ────────────────────────────────────────
        flying_stars = getattr(result, "flying_stars", {})

        ax = fig.add_axes([0.08, 0.08, 0.55, 0.80])
        ax.set_xlim(0, 3)
        ax.set_ylim(0, 3)
        ax.set_aspect("equal")
        ax.axis("off")

        cell_size = 1.0

        for idx, palace in enumerate(PALACE_ORDER_3x3):
            row = idx // 3  # 0=top, 1=middle, 2=bottom
            col = idx % 3

            # Display: row 0 = top = y=2, row 1 = y=1, row 2 = y=0
            y = 2 - row
            x = col

            star_info = flying_stars.get(palace, {})
            yun = star_info.get("运星", "")
            shan = star_info.get("山星", "")
            xiang = star_info.get("向星", "")

            # Evaluate auspiciousness
            status = "neutral"
            if yun and shan and xiang:
                try:
                    status = _auspiciousness(int(yun), int(shan), int(xiang))
                except (ValueError, TypeError):
                    status = "neutral"

            if status == "auspicious":
                bg_color = "#E8F5E9"  # Green tint
                border_color = "#4CAF50"
            elif status == "inauspicious":
                bg_color = "#FFEBEE"  # Red tint
                border_color = "#F44336"
            else:
                bg_color = "#FAFAFA"
                border_color = "#9E9E9E"

            # Draw cell background
            rect = Rectangle(
                (x, y), cell_size, cell_size,
                facecolor=bg_color, edgecolor=border_color,
                linewidth=2 if status != "neutral" else 1,
            )
            ax.add_patch(rect)

            # Palace name and direction in top-left
            fp = fm.FontProperties(family=_CHINESE_FONT, size=9, weight="bold")
            ax.text(x + 0.03, y + cell_size - 0.05,
                    f"{palace} ({DIRECTION_LABELS.get(palace, '')})",
                    fontproperties=fp, color="#333333", va="top", ha="left")

            # Star numbers (运/山/向)
            fp_num = fm.FontProperties(family=_CHINESE_FONT, size=16, weight="bold")
            fp_label = fm.FontProperties(family=_CHINESE_FONT, size=8)

            if palace == "中":
                # Center - show numbers differently
                center_y = y + cell_size / 2
                if yun:
                    ax.text(x + cell_size / 2, center_y + 0.15,
                            str(yun),
                            fontproperties=fp_num, ha="center", va="center",
                            color="#D32F2F")
                    ax.text(x + cell_size / 2, center_y - 0.05, "运星",
                            fontproperties=fp_label, ha="center", va="center",
                            color="#888888")
            else:
                # 运星 - top center of cell
                ax.text(x + cell_size / 2, y + cell_size * 0.72,
                        str(yun),
                        fontproperties=fp_num, ha="center", va="center",
                        color="#1565C0")

                # 山星 - bottom-left
                ax.text(x + cell_size * 0.25, y + cell_size * 0.35,
                        str(shan),
                        fontproperties=fp_num, ha="center", va="center",
                        color="#388E3C")

                # 向星 - bottom-right
                ax.text(x + cell_size * 0.75, y + cell_size * 0.35,
                        str(xiang),
                        fontproperties=fp_num, ha="center", va="center",
                        color="#D32F2F")

                # Small labels
                ax.text(x + cell_size / 2, y + cell_size * 0.88,
                        "运", fontproperties=fp_label, ha="center",
                        va="center", color="#666666")
                ax.text(x + cell_size * 0.25, y + cell_size * 0.18,
                        "山", fontproperties=fp_label, ha="center",
                        va="center", color="#666666")
                ax.text(x + cell_size * 0.75, y + cell_size * 0.18,
                        "向", fontproperties=fp_label, ha="center",
                        va="center", color="#666666")

        # ── Legend ───────────────────────────────────────────────────
        legend_x = 3.5
        legend_y = 2.5

        fp_title = fm.FontProperties(family=_CHINESE_FONT, size=12, weight="bold")
        fp_item = fm.FontProperties(family=_CHINESE_FONT, size=10)

        ax.text(legend_x, legend_y, "图例", fontproperties=fp_title, color="#333")
        legend_items = [
            ("吉利 (生入/当旺)", "#E8F5E9", "#4CAF50"),
            ("凶险 (克入/反吟)", "#FFEBEE", "#F44336"),
            ("平", "#FAFAFA", "#9E9E9E"),
            ("", "", ""),
            ("运星 (当运之气)", "#1565C0", ""),
            ("山星 (坐山旺衰)", "#388E3C", ""),
            ("向星 (向首吉凶)", "#D32F2F", ""),
        ]
        for i, (label, bg, border) in enumerate(legend_items):
            if not label:
                continue
            ly = legend_y - 0.20 * (i + 1)
            if bg:
                rect = Rectangle((legend_x, ly - 0.06), 0.25, 0.15,
                                 facecolor=bg, edgecolor=border if border else "none",
                                 linewidth=2)
                ax.add_patch(rect)
            ax.text(legend_x + 0.35, ly, label, fontproperties=fp_item, va="center", color="#333")

        # ── Eight Mansions info ──────────────────────────────────────
        eight_mansions = getattr(result, "eight_mansions", {})
        if eight_mansions:
            em_y = legend_y - 0.20 * (len(legend_items) + 2)
            ax.text(legend_x, em_y, "八宅吉凶方位",
                    fontproperties=fp_title, color="#333")

            auspicious_types = {"生气": "#4CAF50", "天医": "#4CAF50",
                                "延年": "#4CAF50", "伏位": "#8BC34A"}
            inauspicious_types = {"绝命": "#F44336", "五鬼": "#FF5722",
                                  "六煞": "#FF9800", "祸害": "#FFC107"}

            em_row = 0
            for etype, edir in eight_mansions.items():
                if etype in auspicious_types:
                    color = auspicious_types[etype]
                elif etype in inauspicious_types:
                    color = inauspicious_types[etype]
                else:
                    color = "#888888"

                ey = em_y - 0.18 * (em_row + 1)
                ax.text(legend_x + 0.02, ey,
                        f"{etype}: {edir}",
                        fontproperties=fp_item, va="center", color=color)
                em_row += 1

        # Turn off default axes
        ax.axis("off")

        return _save_or_return(fig, output_path, prefix="fengshui")
