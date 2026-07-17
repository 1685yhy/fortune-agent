"""HTML-based Ziwei Doushu chart — dark theme, gold accents, Playwright rendering."""
import os, sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

CHARTS_DIR = Path(os.environ.get('CHARTS_DIR', '/mnt/d/fortune-data/charts'))

# 紫微系 / 天府系 star sets
ZIWEI_XI_STARS = {"紫微", "天机", "太阳", "武曲", "天同", "廉贞"}
TIANFU_XI_STARS = {"天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"}

# 四化 color keys
SIHUA_COLORS = {"化禄": "#f4645c", "化权": "#f2c94c", "化科": "#52d98a", "化忌": "#888888"}

# Traditional 4x3 grid: dizhi -> (row, col)
GRID_LAYOUT = {
    "巳": (0, 0), "午": (0, 1), "未": (0, 2), "申": (0, 3),
    "辰": (1, 0),                                    "酉": (1, 3),
    "卯": (2, 0),                                    "戌": (2, 3),
    "寅": (3, 0), "丑": (3, 1), "子": (3, 2), "亥": (3, 3),
}

PALACE_DIR_NAMES = {
    "巳": "巳", "午": "午", "未": "未", "申": "申",
    "辰": "辰", "酉": "酉",
    "卯": "卯", "戌": "戌",
    "寅": "寅", "丑": "丑", "子": "子", "亥": "亥",
}

HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><style>
body{font-family:"PingFang SC","Microsoft YaHei","Noto Serif SC","WenQuanYi Zen Hei",serif;background:#faf8f5;color:#1a1a1a;width:800px;padding:40px 36px}
.h{text-align:center;margin-bottom:28px}
.h .emblem{font-size:48px;margin-bottom:6px;letter-spacing:20px;color:#c9a96e;font-weight:600}
.h .line{width:100%;height:1px;background:linear-gradient(90deg,transparent,rgba(201,169,110,.35) 20%,rgba(201,169,110,.35) 80%,transparent);margin:16px 0}
.h .sub{font-size:13px;color:#8b8680;letter-spacing:3px}
.h .meta{display:flex;justify-content:center;gap:18px;margin-top:12px;font-size:11px;color:#b5b0a8}
.h .meta span{padding:3px 14px;border:1px solid #edeae4;border-radius:4px;background:#ffffff}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;margin-bottom:30px}
.palace{background:#ffffff;padding:12px 10px 10px;position:relative;min-height:110px;border:1px solid #e0ddd6}
.palace.ming{z-index:2;border-color:#c9a96e;background:#ffffff;box-shadow:0 1px 6px rgba(201,169,110,.15)}
.palace .ph{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;border-bottom:1px solid #edeae4;padding-bottom:4px}
.palace .pn{font-size:11px;color:#c9a96e;letter-spacing:2px;font-weight:600}
.palace .dz{font-size:10px;color:#b5b0a8}
.palace .stars{font-size:12px;line-height:1.7}
.palace .star{display:inline-block;margin-right:2px;font-weight:500}
.palace .star.zw{color:#c9a96e}
.palace .star.tf{color:#6a9ec4}
.palace .star .sh{font-size:9px;color:#c96a5e;margin-left:2px}
.palace .aux{font-size:10px;color:#8b8680;line-height:1.6;margin-top:3px;border-top:1px solid #edeae4;padding-top:3px}
.palace .aux .a{display:inline-block;margin-right:4px}
.section{margin-bottom:28px}
.section .tt{font-size:12px;color:#c9a96e;letter-spacing:5px;margin-bottom:14px;display:flex;align-items:center;gap:12px}
.section .tt::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(201,169,110,.2) 20%,rgba(201,169,110,.2) 80%,transparent)}
.dayun{display:flex;align-items:center;gap:0;padding:8px 4px;overflow-x:auto}
.dy-item{flex:1;text-align:center;min-width:60px;position:relative;padding:6px 2px}
.dy-dot{width:6px;height:6px;border-radius:50%;background:#c9a96e;margin:0 auto 6px;position:relative;z-index:1}
.dy-line{position:absolute;top:3px;left:50%;right:-50%;height:1px;background:#edeae4}
.dy-gz{font-size:12px;color:#1a1a1a;font-weight:500}
.dy-p{font-size:9px;color:#8b8680;margin-top:1px}
.dy-age{font-size:9px;color:#b5b0a8;margin-top:1px}
.ft{text-align:center;margin-top:30px;padding-top:16px;border-top:1px solid #edeae4;font-size:10px;color:#b5b0a8;letter-spacing:3px}
.legend{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin-top:10px;font-size:10px;color:#8b8680}
.legend .swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle;border:1px solid rgba(0,0,0,.06)}
.sihua{text-align:center;margin:12px 0;display:flex;justify-content:center;gap:16px}
.sihua .si{font-size:11px;color:#8b8680}
.sihua .si .sl{color:#c9a96e}
</style></head><body>
<div class="h"><div class="emblem">紫 微 斗 数 命 盘</div><div class="line"></div>
<div class="sub">命宫{{ming_gong}}  ·  身宫{{shen_gong}}  ·  {{wuxing_ju}}</div>
<div class="meta"><span>{{date}}</span></div>
<div class="sihua">
{% for k,v in sihua_list %}<span class="si">{{k}}：<span class="sl">{{v}}</span></span>{% endfor %}
</div></div>

<div class="legend">
<span><span class="swatch" style="background:#d4a853"></span>紫微系</span>
<span><span class="swatch" style="background:#5eadf2"></span>天府系</span>
<span><span class="swatch" style="background:#f4645c"></span>四化</span>
<span><span class="swatch" style="background:#6c727d"></span>辅星</span>
</div>

<div class="grid4">
{% for p in palaces %}
<div class="palace{% if p.ming %} ming{% endif %}">
<div class="ph"><span class="pn">{{p.name}}</span><span class="dz">{{p.dizhi}}</span></div>
<div class="stars">{% for s in p.main_stars %}<span class="star {{s.css}}">{{s.name}}{% if s.sihua %}<span class="sh">[{{s.sihua}}]</span>{% endif %}</span> {% endfor %}</div>
{% if p.aux_stars %}<div class="aux">{% for a in p.aux_stars %}<span class="a">{{a}}</span>{% endfor %}</div>{% endif %}
</div>
{% endfor %}
</div>

{% if dayun %}
<div class="section"><div class="tt">大限</div><div class="dayun">
{% for d in dayun %}
<div class="dy-item"><div class="dy-dot"></div>{% if not loop.last %}<div class="dy-line"></div>{% endif %}<div class="dy-gz">{{d.gz}}</div><div class="dy-p">{{d.palace}}</div><div class="dy-age">{{d.age}}岁</div></div>
{% endfor %}
</div></div>
{% endif %}

<div class="ft">易理明灯 · 以古籍为灯 照命理之路</div>
</body></html>"""


class ZiweiChartHTML:
    def generate(self, result, output_path: Optional[str] = None) -> str:
        from jinja2 import Template
        now = datetime.now()

        palaces_data = []
        palaces = getattr(result, "palaces", {})
        sihua = getattr(result, "sihua", {})
        sihua_rev = {v: k for k, v in sihua.items() if v}

        # Build grid: 12 cells ordered by grid position (top-left to bottom-right)
        grid_cells = [
            ("巳", 0, 0), ("午", 0, 1), ("未", 0, 2), ("申", 0, 3),
            ("辰", 1, 0),                                    ("酉", 1, 3),
            ("卯", 2, 0),                                    ("戌", 2, 3),
            ("寅", 3, 0), ("丑", 3, 1), ("子", 3, 2), ("亥", 3, 3),
        ]

        # Build dizhi -> palace_name mapping
        dz_to_name = {info.dizhi: pname for pname, info in palaces.items()}

        for dz, row, col in grid_cells:
            pname = dz_to_name.get(dz, "")
            pinfo = palaces.get(pname) if pname else None

            main_stars = []
            if pinfo and pinfo.stars:
                for s in pinfo.stars:
                    css = "zw" if s in ZIWEI_XI_STARS else "tf"
                    sh = sihua_rev.get(s, "")
                    main_stars.append({"name": s, "css": css, "sihua": sh})

            aux_stars = pinfo.aux_stars if pinfo and pinfo.aux_stars else []

            palaces_data.append({
                "name": pname if pname else "",
                "dizhi": dz,
                "main_stars": main_stars,
                "aux_stars": aux_stars,
                "ming": pname == "命宫",
            })

        # 四化 list for header
        sihua_list = [(k, v) for k, v in sihua.items() if v]

        # 大限
        dayun_raw = getattr(result, "dayun", [])
        dayun = []
        for item in dayun_raw[:8]:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                age, palace_name, dz = item[0], item[1], item[2]
                dayun.append({"age": age, "gz": f"{palace_name}({dz})", "palace": palace_name})
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                age, palace_name = item[0], item[1]
                dayun.append({"age": age, "gz": palace_name, "palace": palace_name})

        ming_gong = getattr(result, "ming_gong", "")
        shen_gong = getattr(result, "shen_gong", "")
        wuxing_ju = getattr(result, "wuxing_ju", "")

        html = Template(HTML).render(
            ming_gong=ming_gong,
            shen_gong=shen_gong,
            wuxing_ju=wuxing_ju,
            sihua_list=sihua_list,
            palaces=palaces_data,
            dayun=dayun,
            date=now.strftime("%Y.%m.%d"),
        )

        out = output_path or str(CHARTS_DIR / f"ziwei_{now.strftime('%Y%m%d_%H%M%S')}.png")
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
