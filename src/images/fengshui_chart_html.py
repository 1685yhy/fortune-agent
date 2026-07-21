"""HTML-based Fengshui chart — dark theme, gold accents, Playwright rendering."""
import os, sys
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

CHARTS_DIR = Path(os.environ.get('CHARTS_DIR', '/opt/fortune-data/charts'))

# 九宫八卦顺序 (3x3 grid, top-left to bottom-right)
PALACE_ORDER_3x3 = ["巽", "离", "坤", "震", "中", "兑", "艮", "坎", "乾"]
DIRECTION_LABELS = {
    "巽": "东南", "离": "南", "坤": "西南",
    "震": "东", "中": "中宫", "兑": "西",
    "艮": "东北", "坎": "北", "乾": "西北",
}

# 五行生克 helpers for auspiciousness evaluation
_WUXING_CYCLE = {1: "水", 2: "土", 3: "木", 4: "木", 5: "土",
                 6: "金", 7: "金", 8: "土", 9: "火"}
_WUXING_SHENG = {"水": "木", "木": "火", "火": "土", "土": "金", "金": "水"}
_WUXING_KE = {"水": "火", "火": "金", "金": "木", "木": "土", "土": "水"}
_CURRENT_PERIOD = 9


def _eval_auspiciousness(yun, shan, xiang):
    """Return 'auspicious', 'inauspicious', or 'neutral'."""
    try:
        yun, shan, xiang = int(yun), int(shan), int(xiang)
    except (ValueError, TypeError):
        return "neutral"
    yw = _WUXING_CYCLE.get(yun, "")
    sw = _WUXING_CYCLE.get(shan, "")
    xw = _WUXING_CYCLE.get(xiang, "")
    if not yw or not sw or not xw:
        return "neutral"
    if yun == _CURRENT_PERIOD:
        return "auspicious"
    if _WUXING_SHENG.get(sw) == yw or _WUXING_SHENG.get(xw) == yw:
        return "auspicious"
    if _WUXING_KE.get(yw) == sw or _WUXING_KE.get(yw) == xw:
        return "inauspicious"
    return "neutral"


# 八宅吉凶 colors
AUSPICIOUS_COLORS = {"生气": "#26de81", "天医": "#26de81", "延年": "#26de81", "伏位": "#48dbfb"}
INAUSPICIOUS_COLORS = {"绝命": "#ff6b6b", "五鬼": "#ee5a24", "六煞": "#ff9f43", "祸害": "#feca57"}

HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><style>
body{font-family:"PingFang SC","Microsoft YaHei","Noto Serif SC","WenQuanYi Zen Hei",serif;background:#faf8f5;color:#1a1a1a;width:800px;padding:40px 36px}
.h{text-align:center;margin-bottom:28px}
.h .emblem{font-size:48px;margin-bottom:6px;letter-spacing:20px;color:#c9a96e;font-weight:600}
.h .line{width:100%;height:1px;background:linear-gradient(90deg,transparent,rgba(201,169,110,.35) 20%,rgba(201,169,110,.35) 80%,transparent);margin:16px 0}
.h .sub{font-size:13px;color:#8b8680;letter-spacing:3px}
.h .meta{display:flex;justify-content:center;gap:18px;margin-top:12px;font-size:11px;color:#b5b0a8}
.h .meta span{padding:3px 14px;border:1px solid #edeae4;border-radius:4px;background:#ffffff}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;margin-bottom:24px}
.palace{background:#ffffff;padding:16px 12px;text-align:center;position:relative;min-height:90px;border:1px solid #e0ddd6}
.palace.auspicious{background:#f6faf6;border:1px solid #c8e0c8}
.palace.inauspicious{background:#faf6f5;border:1px solid #e0c8c5}
.palace .ph{font-size:11px;color:#c9a96e;letter-spacing:2px;margin-bottom:8px;border-bottom:1px solid #edeae4;padding-bottom:4px}
.palace .dir{font-size:9px;color:#b5b0a8;margin-left:4px}
.palace .nums{display:flex;justify-content:center;gap:12px;margin-top:8px}
.palace .num{text-align:center}
.palace .num .v{font-size:22px;font-weight:700;line-height:1}
.palace .num .l{font-size:9px;color:#b5b0a8;margin-top:2px}
.palace .num.yun .v{color:#6a9ec4}
.palace .num.shan .v{color:#7ab87a}
.palace .num.xiang .v{color:#c96a5e}
.palace.center .ph{color:#c9a96e;font-size:14px;font-weight:600;border-bottom:none;margin-bottom:4px}
.palace.center .v{font-size:26px;color:#c9a96e;font-weight:700}
.section{margin-bottom:24px}
.section .tt{font-size:12px;color:#c9a96e;letter-spacing:5px;margin-bottom:14px;display:flex;align-items:center;gap:12px}
.section .tt::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(201,169,110,.2) 20%,rgba(201,169,110,.2) 80%,transparent)}
.eight-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.eight-item{background:#ffffff;padding:12px;text-align:center;border-radius:8px;border:1px solid #e0ddd6}
.eight-item .en{font-size:13px;font-weight:600;margin-bottom:4px}
.eight-item .ed{font-size:11px;color:#8b8680}
.eight-item.good .en{color:#7ab87a}
.eight-item.bad .en{color:#c96a5e}
.ft{text-align:center;margin-top:28px;padding-top:16px;border-top:1px solid #edeae4;font-size:10px;color:#b5b0a8;letter-spacing:3px}
.legend{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:8px 0;font-size:10px;color:#8b8680}
.legend .swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle;border:1px solid rgba(0,0,0,.06)}
.info-row{display:flex;justify-content:center;gap:20px;margin-top:10px}
</style></head><body>
<div class="h"><div class="emblem">风 水 分 析</div><div class="line"></div>
<div class="sub">{{house_gua}}  ·  {{period}}运</div>
<div class="meta">
<span>{{date}}</span>
{% if person_gua %}<span>命卦：{{person_gua}}</span>{% endif %}
</div></div>

<div class="legend">
<span><span class="swatch" style="background:#5eadf2"></span>运星</span>
<span><span class="swatch" style="background:#52d98a"></span>山星</span>
<span><span class="swatch" style="background:#f4645c"></span>向星</span>
<span><span class="swatch" style="background:#0d2618"></span>吉利</span>
<span><span class="swatch" style="background:#261111"></span>凶险</span>
</div>

<div class="grid3">
{% for p in palaces %}
<div class="palace{% if p.css %} {{p.css}}{% endif %}{% if p.center %} center{% endif %}">
<div class="ph">{{p.name}}{% if p.direction %}<span class="dir">({{p.direction}})</span>{% endif %}</div>
{% if p.center %}
<div class="v">{{p.period_num}}</div>
{% else %}
<div class="nums">
<div class="num yun"><div class="v">{{p.yun}}</div><div class="l">运</div></div>
<div class="num shan"><div class="v">{{p.shan}}</div><div class="l">山</div></div>
<div class="num xiang"><div class="v">{{p.xiang}}</div><div class="l">向</div></div>
</div>
{% endif %}
</div>
{% endfor %}
</div>

<div class="section"><div class="tt">八宅吉凶方位</div></div>
<div class="eight-grid">
{% for e in eight_mansions %}
<div class="eight-item {% if e.auspicious %}good{% else %}bad{% endif %}">
<div class="en">{{e.name}}</div>
<div class="ed">{{e.direction}}</div>
</div>
{% endfor %}
</div>

{% if analysis_info %}
<div class="section"><div class="tt">分析说明</div></div>
<div class="info-row"><span style="font-size:11px;color:#5c626d">{{analysis_info}}</span></div>
{% endif %}

<div class="ft">易理明灯 · 以古籍为灯 照命理之路</div>
</body></html>"""


class FengshuiChartHTML:
    def generate(self, result, output_path: Optional[str] = None) -> str:
        from jinja2 import Template
        now = datetime.now()

        flying_stars = getattr(result, "flying_stars", {})
        house_gua = getattr(result, "house_gua", "")
        period = getattr(result, "period", "")
        eight_mansions_raw = getattr(result, "eight_mansions", {})
        person_gua = getattr(result, "person_gua", "")

        # Build 3x3 palace grid
        palaces = []
        for trigram in PALACE_ORDER_3x3:
            info = flying_stars.get(trigram, {})
            yun = info.get("运星", "")
            shan = info.get("山星", "")
            xiang = info.get("向星", "")
            status = _eval_auspiciousness(yun, shan, xiang) if yun and shan and xiang else "neutral"

            palaces.append({
                "name": trigram,
                "direction": DIRECTION_LABELS.get(trigram, ""),
                "yun": str(yun) if yun else "",
                "shan": str(shan) if shan else "",
                "xiang": str(xiang) if xiang else "",
                "period_num": str(yun) if yun else "",
                "center": trigram == "中",
                "css": status,
            })

        # Eight mansions
        eight_mansions = []
        auspicious_set = {"生气", "天医", "延年", "伏位"}
        for name, direction in eight_mansions_raw.items():
            eight_mansions.append({
                "name": name,
                "direction": direction,
                "auspicious": name in auspicious_set,
            })

        html = Template(HTML).render(
            house_gua=house_gua,
            period=period,
            person_gua=person_gua,
            palaces=palaces,
            eight_mansions=eight_mansions,
            date=now.strftime("%Y.%m.%d"),
            analysis_info=f"宅卦{house_gua}，{period}运，玄空飞星九宫飞布",
        )

        out = output_path or str(CHARTS_DIR / f"fengshui_{now.strftime('%Y%m%d_%H%M%S')}.png")
        hp = out.replace(".png", ".html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)

        import os as _os
        from concurrent.futures import ThreadPoolExecutor

        def _render_sync():
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                p = b.new_page(viewport={"width": 800, "height": 900})
                p.goto("file://" + hp, wait_until="networkidle")
                p.screenshot(path=out, full_page=True)
                b.close()

        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(_render_sync).result(timeout=30)

        _os.remove(hp)
        return out
