"""Magazine-quality bazi chart — warm paper background, gold accents, premium editorial feel.

Design language: The Economist / Monocle magazine pull-out.
- WHO (日柱 hero dial, 2x visual weight) -> WHAT (五行 bars) -> PATH (大运 timeline)
- 850px wide, auto height, Jinja2 template, Playwright rendering.
"""

import os, sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

CHARTS_DIR = Path(os.environ.get("CHARTS_DIR", "/mnt/d/fortune-data/charts"))

# --- Refined luxury colour palette ---
# 天干/地支 五行 colours (rich, toned — not neon)
WX_COLORS = {
    "金": "#dedad1",  # platinum
    "木": "#7ab87a",  # jade
    "水": "#6a9ec4",  # sapphire
    "火": "#c96a5e",  # terracotta
    "土": "#c9a96e",  # gold
}
WX_TG = {"甲":"木","乙":"木","丙":"火","丁":"火","戊":"土","己":"土","庚":"金","辛":"金","壬":"水","癸":"水"}
WX_DZ = {"子":"水","丑":"土","寅":"木","卯":"木","辰":"土","巳":"火","午":"火","未":"土","申":"金","酉":"金","戌":"土","亥":"水"}
ELEMENT_CN = {"金":"金","木":"木","水":"水","火":"火","土":"土"}

HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><style>
/* ===== RESET & BASE ===== */
*{margin:0;padding:0;box-sizing:border-box}
body{
  background:#faf8f5;
  color:#1a1a1a;
  font-family:"PingFang SC","Microsoft YaHei","Noto Serif SC","WenQuanYi Zen Hei",serif;
  width:850px;padding:40px 48px 32px
}

/* ===== HEADER — gold line · title · gold line ===== */
.hd{display:flex;align-items:center;gap:16px;margin-bottom:6px}
.hd-t{font-size:14px;color:#c9a96e;letter-spacing:8px;white-space:nowrap}
.hd-l{flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(201,169,110,.25) 20%,rgba(201,169,110,.25) 80%,transparent)}
.hd-s{font-size:11px;color:#8b8680;text-align:center;letter-spacing:4px;margin-bottom:6px}
.hd-d{text-align:center;font-size:10px;color:#b5b0a8;letter-spacing:3px;margin-bottom:24px}

/* ===== HERO CARD — watch-dial centrepiece ===== */
.hero{
  background:#ffffff;
  border:1px solid #e0ddd6;
  border-radius:12px;padding:28px 32px 24px;text-align:center;
  position:relative;isolation:isolate;
  box-shadow:0 1px 3px rgba(0,0,0,0.04),0 4px 12px rgba(0,0,0,0.06);
  margin-bottom:20px
}
.hero::before{
  content:'';position:absolute;top:0;left:35%;right:35%;height:1px;
  background:#c9a96e;border-radius:1px
}
.hero-lb{font-size:10px;color:#b5b0a8;letter-spacing:6px;margin-bottom:16px}

/* circular dial — the crown jewel */
.hero-d{
  width:156px;height:156px;border-radius:50%;
  border:1px solid #e0ddd6;
  margin:0 auto 18px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:#faf8f5;
  box-shadow:inset 0 1px 4px rgba(0,0,0,0.04)
}
.hero-g{font-size:48px;font-weight:700;line-height:1;margin-bottom:4px}
.hero-e{font-size:14px;font-weight:400;line-height:1;margin-bottom:4px;color:#8b8680}
.hero-z{font-size:28px;font-weight:400;line-height:1;color:#1a1a1a}

.hero-m{display:flex;justify-content:center;gap:16px;font-size:11px;color:#8b8680;letter-spacing:2px;margin-bottom:10px}
.hero-s{font-size:10px;color:#b5b0a8;letter-spacing:2px}
.hero-s b{color:#c9a96e;font-weight:500}

/* ===== THREE PILLAR CARDS — gold top-accent line ===== */
.cr{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px}
.cr-c{
  background:#ffffff;border-radius:8px;padding:16px 12px;text-align:center;
  border:1px solid #e0ddd6;
  box-shadow:0 1px 3px rgba(0,0,0,0.04);position:relative;isolation:isolate
}
.cr-c::before{
  content:'';position:absolute;top:0;left:20%;right:20%;height:1px;
  background:#c9a96e;border-radius:1px
}
.cr-l{font-size:10px;color:#b5b0a8;letter-spacing:4px;margin-bottom:10px}
.cr-g{font-size:32px;font-weight:700;line-height:1;margin-bottom:2px}
.cr-z{font-size:22px;font-weight:400;line-height:1;margin-bottom:8px;color:#8b8680}
.cr-m{font-size:11px;color:#8b8680;letter-spacing:1px}

/* ===== SECTION HEADER ===== */
.sec{margin-bottom:24px}
.st{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.st-t{font-size:11px;color:#8b8680;letter-spacing:5px;white-space:nowrap}
.st-l{flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(201,169,110,.2) 20%,rgba(201,169,110,.2) 80%,transparent)}

/* ===== 五行 BARS ===== */
.bg{display:grid;gap:8px}
.br{display:flex;align-items:center;gap:14px}
.br .l{width:20px;font-size:14px;font-weight:700;text-align:center}
.br .t{flex:1;height:8px;background:#edeae4;border-radius:4px;overflow:hidden}
.br .f{height:100%;border-radius:4px;min-width:2px;transition:width 1.2s cubic-bezier(.22,1,.36,1)}
.br .n{width:24px;font-size:11px;color:#8b8680;text-align:right;font-variant-numeric:tabular-nums}

/* ===== 大运 TIMELINE ===== */
.tl{display:flex;padding:12px 0 4px}
.ti{flex:1;text-align:center;position:relative}
.td{width:6px;height:6px;border-radius:50%;background:#e0ddd6;margin:0 auto;position:relative;z-index:2}
.tn{position:absolute;top:3px;left:50%;right:-50%;height:1px;z-index:1}
.ti:last-child .tn{display:none}
.ti .tg{font-size:12px;font-weight:600;margin-top:12px;letter-spacing:1px}
.ti .ta{font-size:9px;margin-top:2px;letter-spacing:1px}
/* past — muted */
.ti.p{opacity:.4}
.ti.p .tn{background:#edeae4}
.ti.p .tg{color:#b5b0a8}
.ti.p .ta{color:#b5b0a8}
/* future — dim */
.ti.f{opacity:.55}
.ti.f .tn{background:#edeae4}
.ti.f .tg{color:#8b8680}
.ti.f .ta{color:#b5b0a8}
/* current — glowing gold */
.ti.c{opacity:1}
.ti.c .td{width:10px;height:10px;background:#c9a96e;box-shadow:0 0 8px rgba(201,169,110,.4);top:-2px;position:relative}
.ti.c .tn{background:linear-gradient(90deg,rgba(201,169,110,.2),rgba(201,169,110,.08))}
.ti.c .tg{color:#c9a96e}
.ti.c .ta{color:#8b8680}

/* ===== TAGS ===== */
.tg{display:flex;gap:6px;flex-wrap:wrap}
.ts{padding:3px 12px;border-radius:4px;font-size:10px;background:#f5f2ed;color:#8b8680;border:1px solid #edeae4}

/* ===== FOOTER ===== */
.ft{text-align:center;margin-top:32px;padding-top:14px;border-top:1px solid #edeae4;font-size:9px;color:#b5b0a8;letter-spacing:4px}
</style></head><body>

<div class="hd"><div class="hd-l"></div><div class="hd-t">八字命盘</div><div class="hd-l"></div></div>
<div class="hd-s">{{day_master}} · {{geju}} · {{yongshen}}</div>
<div class="hd-d">{{date}}</div>

<div class="hero">
<div class="hero-lb">日主 · DAY MASTER</div>
<div class="hero-d">
<div class="hero-g" style="color:{{hero.gan_color}}">{{hero.gan}}</div>
<div class="hero-e" style="color:{{hero.gan_color}}">{{hero.element}}</div>
<div class="hero-z" style="color:{{hero.zhi_color}}">{{hero.zhi}}</div>
</div>
<div class="hero-m"><span>{{hero.shishen}}</span><span>{{hero.nayin}}</span></div>
{% if hero_strength_pct > 0 %}<div class="hero-s">日主强度 <b>{{hero_strength_pct}}%</b></div>{% endif %}
</div>

<div class="cr">
{% for p in regular_pillars %}
<div class="cr-c">
<div class="cr-l">{{p.label}}</div>
<div class="cr-g" style="color:{{p.gan_color}}">{{p.gan}}</div>
<div class="cr-z" style="color:{{p.zhi_color}}">{{p.zhi}}</div>
<div class="cr-m">{{p.shishen}} · {{p.nayin}}</div>
</div>
{% endfor %}
</div>

{% if bars %}
<div class="sec">
<div class="st"><div class="st-l"></div><div class="st-t">五行能量</div><div class="st-l"></div></div>
<div class="bg">
{% for b in bars %}
<div class="br"><span class="l" style="color:{{b.c}}">{{b.n}}</span><div class="t"><div class="f" style="width:{{b.p}}%;background:{{b.c}}"></div></div><span class="n">{{b.v}}</span></div>
{% endfor %}
</div>
</div>
{% endif %}

{% if dayun %}
<div class="sec">
<div class="st"><div class="st-l"></div><div class="st-t">大运流年</div><div class="st-l"></div></div>
<div class="tl">
{% for d in dayun %}
<div class="ti {{d.cls}}"><div class="td"></div>{% if not loop.last %}<div class="tn"></div>{% endif %}<div class="tg">{{d.gz}}</div><div class="ta">{{d.a}}岁</div></div>
{% endfor %}
</div>
</div>
{% endif %}

{% if tags %}
<div class="sec">
<div class="st"><div class="st-l"></div><div class="st-t">神煞</div><div class="st-l"></div></div>
<div class="tg">{% for t in tags %}<span class="ts">{{t}}</span>{% endfor %}</div>
</div>
{% endif %}

<div class="ft">易理明灯 · 以古籍为灯 照命理之路</div>
</body></html>"""


class BaziChartHTML:
    def generate(self, result, output_path=None):
        """Render a luxury-finance bazi chart to PNG via Jinja2 + Playwright.

        Args:
            result: Bazi calculation result with .bazi, .day_master, .wuxing,
                    .shishen, .nayin, .dayun, .shensha, .geju, .yongshen.
            output_path: Optional PNG output path.

        Returns:
            str: absolute path to the generated PNG.
        """
        from jinja2 import Template
        now = datetime.now()

        # --- 1. Build all four pillar dicts ---
        pillars = []
        labels = ["年柱", "月柱", "日柱", "时柱"]
        for i, p in enumerate(result.bazi[:4]):
            g, z = p[0], p[1]
            eg = WX_TG.get(g, "")
            ez = WX_DZ.get(z, "")
            pillars.append({
                "label": labels[i],
                "gan": g,
                "zhi": z,
                "gan_color": WX_COLORS.get(eg, "#c9c9c9"),
                "zhi_color": WX_COLORS.get(ez, "#7a7a7a"),
                "shishen": result.shishen[i] if i < len(result.shishen) else "",
                "nayin": result.nayin[i] if i < len(result.nayin) else "",
            })

        hero = dict(pillars[2])
        dm_element = WX_TG.get(hero["gan"], "")
        hero["element"] = ELEMENT_CN.get(dm_element, "")
        regular_pillars = [pillars[0], pillars[1], pillars[3]]

        # --- 2. Hero strength (day master element proportion) ---
        total_wx = sum(result.wuxing.values()) if result.wuxing else 0
        hero_strength_pct = 0
        if dm_element and total_wx > 0:
            hero_strength_pct = int(result.wuxing.get(dm_element, 0) / total_wx * 100)

        # --- 3. 五行 bars (canonical order, omit zero) ---
        mx = max(result.wuxing.values()) if result.wuxing else 1
        wx_order = ["金", "木", "水", "火", "土"]
        bars = []
        for n in wx_order:
            val = result.wuxing.get(n, 0)
            if val > 0:
                bars.append({"n": n, "c": WX_COLORS.get(n, "#c9c9c9"),
                             "v": val, "p": int(val / mx * 100)})

        # --- 4. 大运 timeline with current-period glow ---
        dayun_raw = list(result.dayun[:8]) if result.dayun else []
        current_age = 30
        for attr in ("birth_year", "birthday"):
            val = getattr(result, attr, None)
            if val is not None:
                try:
                    current_age = now.year - (val.year if hasattr(val, "year") else int(val))
                    break
                except (ValueError, TypeError):
                    continue

        dayun = []
        for age, gz in dayun_raw:
            dayun.append({"a": age, "gz": gz, "current": False, "past": False, "future": False})

        current_idx = len(dayun) - 1 if dayun else -1
        for i in range(len(dayun)):
            next_age = dayun[i + 1]["a"] if i + 1 < len(dayun) else 999
            if dayun[i]["a"] <= current_age < next_age:
                current_idx = i
                break

        for i, d in enumerate(dayun):
            if i == current_idx:
                d["current"] = True
            elif i < current_idx:
                d["past"] = True
            else:
                d["future"] = True
            d["cls"] = "c" if d["current"] else "p" if d["past"] else "f"

        # --- 5. 神煞 ---
        tags = list(result.shensha[:8]) if result.shensha else []

        # --- 6. Render ---
        geju = getattr(result, "geju", "")
        yongshen_raw = getattr(result, "yongshen", None)
        yongshen = yongshen_raw.split("（")[0] if yongshen_raw else ""

        html = Template(HTML).render(
            day_master=result.day_master,
            geju=geju,
            yongshen=yongshen,
            hero=hero,
            hero_strength_pct=hero_strength_pct,
            regular_pillars=regular_pillars,
            bars=bars,
            dayun=dayun,
            tags=tags,
            date=now.strftime("%Y.%m.%d"),
        )

        out = output_path or str(CHARTS_DIR / f"bazi_{now.strftime('%Y%m%d_%H%M%S')}.png")
        hp = out.replace(".png", ".html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)

        import os as _os
        from concurrent.futures import ThreadPoolExecutor

        def _render_sync():
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                p = b.new_page(viewport={"width": 850, "height": 1050})
                p.goto("file://" + hp, wait_until="networkidle")
                p.screenshot(path=out, full_page=True)
                b.close()

        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(_render_sync).result(timeout=30)
        _os.remove(hp)
        return out
