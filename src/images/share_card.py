"""Social share card — 1200x630 magazine-style OG image for 易理明灯.

Design language: Luxury editorial / Monocle cover aesthetic.
- Warm white background, gold accent bar at top
- Title "易理明灯" in Noto Serif SC
- Bazi four pillars across the middle
- Day master as the hero element
- Pattern and key data below
- QR code placeholder (bottom-right)

Uses Jinja2 + Playwright for rendering, matching the chart pipeline.
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

CHARTS_DIR = Path(os.environ.get("CHARTS_DIR", "/mnt/d/fortune-data/charts"))

# ── Wuxing colour mapping (same as bazi_chart_html) ──
WX_COLORS = {
    "金": "#dedad1",
    "木": "#7ab87a",
    "水": "#6a9ec4",
    "火": "#c96a5e",
    "土": "#c9a96e",
}
WX_TG = {"甲": "木", "乙": "木", "丙": "火", "丁": "火",
         "戊": "土", "己": "土", "庚": "金", "辛": "金",
         "壬": "水", "癸": "水"}
WX_DZ = {"子": "水", "丑": "土", "寅": "木", "卯": "木",
         "辰": "土", "巳": "火", "午": "火", "未": "土",
         "申": "金", "酉": "金", "戌": "土", "亥": "水"}
ELEMENT_CN = {"金": "金", "木": "木", "水": "水", "火": "火", "土": "土"}

# ── Pillar label order ──
PILLAR_LABELS = ["年柱", "月柱", "日柱", "时柱"]

HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  width:1200px;height:630px;overflow:hidden;
  background:#ffffff;
  font-family:"PingFang SC","Microsoft YaHei","Noto Serif SC","WenQuanYi Zen Hei",serif;
  color:#1a1a1a;
  position:relative
}

/* ===== TOP GOLD ACCENT BAR ===== */
.top-bar{
  position:absolute;top:0;left:0;right:0;height:4px;
  background:#c9a96e
}

/* ===== BRAND HEADER ===== */
.brand{
  text-align:center;padding-top:36px;margin-bottom:20px
}
.brand .title{
  font-family:"Noto Serif SC","Source Han Serif SC",serif;
  font-size:24px;font-weight:700;color:#c9a96e;
  letter-spacing:12px
}
.brand .tagline{
  font-size:11px;color:#b5b0a8;letter-spacing:4px;
  margin-top:4px
}

/* ===== PILLARS ROW ===== */
.pillars{
  display:flex;justify-content:center;gap:20px;
  margin:0 60px 24px
}
.pillar{
  text-align:center;width:140px;
  padding:14px 8px 12px;
  border:1px solid #edeae4;
  border-radius:8px;
  background:#faf8f5
}
.pillar.day{
  border-color:#c9a96e;
  background:#ffffff;
  box-shadow:0 1px 6px rgba(201,169,110,.15)
}
.pillar .label{
  font-size:10px;color:#b5b0a8;letter-spacing:3px;margin-bottom:6px
}
.pillar .gan{
  font-size:36px;font-weight:700;line-height:1.1
}
.pillar .zhi{
  font-size:24px;line-height:1.2;color:#8b8680;margin-top:2px
}
.pillar .meta{
  font-size:10px;color:#b5b0a8;margin-top:6px;letter-spacing:1px
}

/* ===== DAY MASTER HERO ===== */
.hero-section{
  text-align:center;margin-bottom:16px
}
.hero-section .dm-label{
  font-size:10px;color:#b5b0a8;letter-spacing:4px;margin-bottom:4px
}
.hero-section .dm-gan{
  font-size:56px;font-weight:700;line-height:1
}
.hero-section .dm-element{
  font-size:14px;color:#8b8680;margin-top:2px
}
.hero-section .dm-shishen{
  font-size:12px;color:#8b8680;margin-top:2px;letter-spacing:2px
}

/* ===== PATTERN + YONGSHEN BADGES ===== */
.badges{
  text-align:center;margin-bottom:20px
}
.badge{
  display:inline-block;padding:4px 18px;margin:0 6px;
  border-radius:4px;font-size:12px;letter-spacing:2px
}
.badge.geju{
  background:#f5f2ed;color:#8b8680;border:1px solid #edeae4
}
.badge.yongshen{
  background:#faf8f5;color:#c9a96e;border:1px solid rgba(201,169,110,.3)
}

/* ===== DIVIDER ===== */
.divider{
  margin:0 60px 16px;
  height:1px;
  background:linear-gradient(90deg,transparent,rgba(201,169,110,.25) 20%,rgba(201,169,110,.25) 80%,transparent)
}

/* ===== FIVE ELEMENTS MINI BARS ===== */
.elements{
  display:flex;justify-content:center;gap:10px;
  margin:0 100px 16px
}
.el{
  display:flex;align-items:center;gap:6px
}
.el .dot{
  width:8px;height:8px;border-radius:50%
}
.el .label{
  font-size:10px;color:#8b8680
}
.el .value{
  font-size:10px;color:#b5b0a8;font-variant-numeric:tabular-nums
}

/* ===== FOOTER LINE ===== */
.footer{
  position:absolute;bottom:28px;left:60px;right:60px;
  display:flex;justify-content:space-between;align-items:center
}
.footer .brand-name{
  font-size:11px;color:#b5b0a8;letter-spacing:4px
}
.footer .qr-placeholder{
  width:56px;height:56px;
  border:2px dashed rgba(201,169,110,.35);
  border-radius:6px;
  display:flex;align-items:center;justify-content:center;
  font-size:7px;color:#b5b0a8;letter-spacing:2px;
  text-align:center;
  line-height:1.2
}
.footer .date{
  font-size:9px;color:#b5b0a8;letter-spacing:2px
}
</style></head><body>
<div class="top-bar"></div>

<div class="brand">
  <div class="title">易 理 明 灯</div>
  <div class="tagline">以 古 籍 为 灯 · 照 命 理 之 路</div>
</div>

<div class="pillars">
{% for p in pillars %}
<div class="pillar{% if p.is_day %} day{% endif %}">
  <div class="label">{{p.label}}</div>
  <div class="gan" style="color:{{p.gan_color}}">{{p.gan}}</div>
  <div class="zhi">{{p.zhi}}</div>
  <div class="meta">{{p.shishen}}</div>
</div>
{% endfor %}
</div>

<div class="hero-section">
  <div class="dm-label">日 主 · DAY MASTER</div>
  <div class="dm-gan" style="color:{{dm_color}}">{{day_master}}</div>
  <div class="dm-element">{{dm_element}}</div>
  <div class="dm-shishen">{{dm_shishen}}</div>
</div>

<div class="badges">
  <span class="badge geju">{{geju}}</span>
  {% if yongshen %}
  <span class="badge yongshen">{{yongshen}}</span>
  {% endif %}
</div>

<div class="divider"></div>

{% if wx_bars %}
<div class="elements">
{% for b in wx_bars %}
  <div class="el">
    <div class="dot" style="background:{{b.color}}"></div>
    <span class="label">{{b.name}}</span>
    <span class="value">{{b.value}}</span>
  </div>
{% endfor %}
</div>
{% endif %}

<div class="footer">
  <span class="brand-name">{{user_name}}</span>
  <span class="date">{{date}}</span>
  <div class="qr-placeholder">扫 码<br>详 解</div>
</div>
</body></html>"""


class ShareCardGenerator:
    """1200x630 social share card for 易理明灯 bazi results."""

    def generate(
        self,
        result,
        user_name: str = "",
        date_str: str = "",
        output_path: Optional[str] = None,
    ) -> str:
        """Generate a 1200x630 share card PNG.

        Args:
            result: BaziResult object with .bazi, .day_master, .geju, .yongshen,
                    .wuxing, .shishen, .nayin attributes.
            user_name: Optional display name shown in footer.
            date_str: Optional custom date string. Defaults to today.
            output_path: Optional PNG output path.

        Returns:
            str: absolute path to the generated PNG.
        """
        from jinja2 import Template

        now = datetime.now()
        date = date_str or now.strftime("%Y年%m月%d日")
        name = user_name or "命理分析"

        # ── Build pillar data ──
        pillars = []
        bazi = getattr(result, "bazi", []) or []
        shishen = getattr(result, "shishen", []) or []
        for i, label in enumerate(PILLAR_LABELS):
            if i < len(bazi):
                g, z = bazi[i][0], bazi[i][1]
            else:
                g, z = "", ""
            eg = WX_TG.get(g, "")
            ss = shishen[i] if i < len(shishen) else ""
            pillars.append({
                "label": label,
                "gan": g,
                "zhi": z,
                "gan_color": WX_COLORS.get(eg, "#1a1a1a"),
                "shishen": ss,
                "is_day": i == 2,
            })

        # ── Day master info ──
        day_master = getattr(result, "day_master", "")
        dm_wx = WX_TG.get(day_master, "")
        dm_color = WX_COLORS.get(dm_wx, "#1a1a1a")
        dm_shishen = shishen[2] if len(shishen) > 2 else ""

        # ── Pattern ──
        geju = getattr(result, "geju", "普通格")
        yongshen_raw = getattr(result, "yongshen", "")
        yongshen = yongshen_raw.split("（")[0] if yongshen_raw else ""

        # ── Five elements mini bars ──
        wuxing = getattr(result, "wuxing", {})
        wx_order = ["金", "木", "水", "火", "土"]
        wx_bars = []
        for wx in wx_order:
            val = wuxing.get(wx, 0)
            if val > 0:
                wx_bars.append({"name": wx, "color": WX_COLORS.get(wx, "#ccc"),
                                "value": val})

        # ── Render ──
        html = Template(HTML).render(
            pillars=pillars,
            day_master=day_master,
            dm_color=dm_color,
            dm_element=ELEMENT_CN.get(dm_wx, ""),
            dm_shishen=dm_shishen,
            geju=geju,
            yongshen=yongshen,
            wx_bars=wx_bars,
            user_name=name,
            date=date,
        )

        out = output_path or str(CHARTS_DIR / f"share_{now.strftime('%Y%m%d_%H%M%S')}.png")
        hp = out.replace(".png", ".html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)

        import os as _os
        from concurrent.futures import ThreadPoolExecutor

        def _render_sync():
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                p = b.new_page(viewport={"width": 1200, "height": 630})
                p.goto("file://" + hp, wait_until="networkidle")
                p.screenshot(path=out, full_page=True)
                b.close()

        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(_render_sync).result(timeout=30)

        _os.remove(hp)
        return out
