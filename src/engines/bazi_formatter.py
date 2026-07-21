"""Detailed bazi chart formatter — multi-level display like 问真八字.

Outputs a comprehensive chart with:
Level 1: 基本信息 (四柱, 日主, 五行)
Level 2: 基本命盘 (十神, 纳音, 藏干, 空亡, 神煞)
Level 3: 专业细盘 (大运, 流年, 格局, 用神)
"""
from typing import List, Dict, Optional


# 地支藏干 (hidden stems within earthly branches)
# Standard from 《子平真诠》
BRANCH_HIDDEN = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "庚", "戊"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}

# 天干十神 mapping based on day master
def _get_shishen(day_gan: str, target_gan: str, for_hidden: bool = False) -> str:
    """Calculate 十神 relationship between day stem and target stem.

    Args:
        day_gan: Day master stem
        target_gan: Target stem to analyze
        for_hidden: True if computing for hidden stems (same stem → 比肩, not 日主)
    """
    gan = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
    wx =  ["木", "木", "火", "火", "土", "土", "金", "金", "水", "水"]
    yinyang = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]  # 1=阳, 0=阴

    di = gan.index(day_gan)
    ti = gan.index(target_gan)
    diff = (ti - di) % 10

    same_yin = (yinyang[di] == yinyang[ti])
    same_wx = (wx[di] == wx[ti])

    if diff == 0:
        return "比肩" if for_hidden else "日主"
    # Same element, different yin-yang → 劫财; same → 比肩
    if same_wx:
        return "比肩" if same_yin else "劫财"
    # Day generates target → 食神/伤官
    gen_map = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    if gen_map.get(wx[di]) == wx[ti]:
        return "食神" if same_yin else "伤官"
    # Target generates day → 偏印/正印
    if gen_map.get(wx[ti]) == wx[di]:
        return "偏印" if same_yin else "正印"
    # Target controls day → 七杀/正官
    ctrl_map = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
    if ctrl_map.get(wx[ti]) == wx[di]:
        return "七杀" if same_yin else "正官"
    # Day controls target → 偏财/正财
    if ctrl_map.get(wx[di]) == wx[ti]:
        return "偏财" if same_yin else "正财"
    return "?"


# 六十甲子纳音表 (abbreviated — common entries)
NAYIN_TABLE = {
    "甲子": "海中金", "乙丑": "海中金", "丙寅": "炉中火", "丁卯": "炉中火",
    "戊辰": "大林木", "己巳": "大林木", "庚午": "路旁土", "辛未": "路旁土",
    "壬申": "剑锋金", "癸酉": "剑锋金", "甲戌": "山头火", "乙亥": "山头火",
    "丙子": "涧下水", "丁丑": "涧下水", "戊寅": "城头土", "己卯": "城头土",
    "庚辰": "白蜡金", "辛巳": "白蜡金", "壬午": "杨柳木", "癸未": "杨柳木",
    "甲申": "泉中水", "乙酉": "泉中水", "丙戌": "屋上土", "丁亥": "屋上土",
    "戊子": "霹雳火", "己丑": "霹雳火", "庚寅": "松柏木", "辛卯": "松柏木",
    "壬辰": "长流水", "癸巳": "长流水", "甲午": "沙中金", "乙未": "沙中金",
    "丙申": "山下火", "丁酉": "山下火", "戊戌": "平地木", "己亥": "平地木",
    "庚子": "壁上土", "辛丑": "壁上土", "壬寅": "金箔金", "癸卯": "金箔金",
    "甲辰": "覆灯火", "乙巳": "覆灯火", "丙午": "天河水", "丁未": "天河水",
    "戊申": "大驿土", "己酉": "大驿土", "庚戌": "钗钏金", "辛亥": "钗钏金",
    "壬子": "桑柘木", "癸丑": "桑柘木", "甲寅": "大溪水", "乙卯": "大溪水",
    "丙辰": "沙中土", "丁巳": "沙中土", "戊午": "天上火", "己未": "天上火",
    "庚申": "石榴木", "辛酉": "石榴木", "壬戌": "大海水", "癸亥": "大海水",
}


def format_detailed_chart(result, birth_info: dict = None) -> str:
    """Generate a detailed multi-level bazi chart like 问真八字.

    Args:
        result: BaziResult from BaziEngine.calculate()
        birth_info: Optional dict with {year, month, day, hour, minute, city, gender}

    Returns:
        Markdown-formatted detailed chart string
    """
    bazi = list(result.bazi)
    day_gan = bazi[2][0] if len(bazi) >= 3 else "?"
    pillars = ["年柱", "月柱", "日柱", "时柱"]

    # ===== Level 1: 基本信息 =====
    lines = []
    if birth_info:
        lines.append(f"📋 **八字命盘**")
        lines.append(f"出生：{birth_info.get('year','?')}年{birth_info.get('month','?')}月{birth_info.get('day','?')}日 "
                     f"{birth_info.get('hour','?')}:{birth_info.get('minute','00')} "
                     f"{birth_info.get('city','?')} {birth_info.get('gender','?')}")
    lines.append(f"日主：**{result.day_master}** | 格局：**{result.geju}** | 用神：**{result.yongshen}**")
    lines.append("")

    # ===== Level 2: 基本命盘 Table =====
    lines.append("```")
    lines.append(f"{'':6} {'年柱':^10} {'月柱':^10} {'日柱':^10} {'时柱':^10}")
    lines.append(f"{'':6} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    # Row 1: 天干 + 十神
    gan_row = "天干  "
    for i, p in enumerate(bazi):
        g = p[0]
        ss = _get_shishen(day_gan, g)
        gan_row += f" {g:^4}({ss}) "
    lines.append(gan_row)

    # Row 2: 地支
    zhi_row = "地支  "
    for p in bazi:
        zhi_row += f" {p[1]:^8} "
    lines.append(zhi_row)

    # Row 3: 藏干
    cg_row = "藏干  "
    for p in bazi:
        hidden = BRANCH_HIDDEN.get(p[1], ["?"])
        cg_row += f" {','.join(hidden):^8} "
    lines.append(cg_row)

    # Row 4: 纳音
    nayin = getattr(result, 'nayin', ['']*4) or ['']*4
    ny_row = "纳音  "
    for i, p in enumerate(bazi):
        n = NAYIN_TABLE.get(p, nayin[i] if i < len(nayin) else "?")
        ny_row += f" {n:^8} "
    lines.append(ny_row)

    # Row 5: 十神 (full)
    ss_row = "十神  "
    # shishen from engine
    ss_list = getattr(result, 'shishen', []) or []
    for i, p in enumerate(bazi):
        if i < len(ss_list) and ss_list[i]:
            s = ss_list[i]
        else:
            s = _get_shishen(day_gan, p[0])
        ss_row += f" {s:^8} "
    lines.append(ss_row)

    lines.append("```")
    lines.append("")

    # ===== Level 3: 五行能量 =====
    wuxing = getattr(result, 'wuxing', {}) or {}
    if wuxing:
        wx_order = ["金", "木", "水", "火", "土"]
        total = sum(wuxing.values()) or 1
        lines.append("📊 **五行能量**")
        bar = "█"
        for wx in wx_order:
            val = wuxing.get(wx, 0)
            pct = val / total * 100
            barlen = int(pct / 5)
            lines.append(f"  {wx}: {'▮'*barlen}{'▯'*(20-barlen)} {val} ({pct:.0f}%)")
        lines.append("")

    # ===== Level 4: 大运流年 =====
    dayun = getattr(result, 'dayun', []) or []
    if dayun:
        lines.append("📅 **大运**")
        dy_str = ""
        for age, gz in dayun[:8]:
            dy_str += f" {age}岁→{gz} |"
        lines.append(dy_str.rstrip("|"))
        lines.append("")

    # ===== Level 5: 神煞 =====
    shensha = getattr(result, 'shensha', []) or []
    if shensha:
        lines.append("⭐ **神煞**")
        lines.append("  " + "、".join(str(s) for s in shensha[:12]))
        lines.append("")

    # ===== Level 6: 流年 =====
    liunian = getattr(result, 'liunian', {}) or {}
    if liunian:
        lines.append("🔮 **近期流年**")
        recent = list(liunian.items())[:6]
        for year, gz in recent:
            lines.append(f"  {year}年 → {gz}")
        lines.append("")

    return "\n".join(lines)


def format_compact_card(result, birth_info: dict = None) -> str:
    """Generate a compact WeChat-friendly version of the chart."""
    bazi = list(result.bazi)
    day_gan = bazi[2][0] if len(bazi) >= 3 else "?"

    lines = ["🧧 **八字命盘**", ""]

    if birth_info:
        bi = birth_info
        lines.append(f"📅 {bi.get('year','?')}.{bi.get('month','?')}.{bi.get('day','?')} "
                     f"{bi.get('hour','?')}:{bi.get('minute','00')} "
                     f"{bi.get('city','?')} {bi.get('gender','?')}")
        lines.append("")

    # Compact table
    lines.append("```")
    lines.append(f"    年柱   月柱   日柱   时柱")
    lines.append(f"天干 {bazi[0][0]:^4}  {bazi[1][0]:^4}  {bazi[2][0]:^4}  {bazi[3][0]:^4}")
    lines.append(f"地支 {bazi[0][1]:^4}  {bazi[1][1]:^4}  {bazi[2][1]:^4}  {bazi[3][1]:^4}")
    # 藏干
    cgs = [",".join(BRANCH_HIDDEN.get(p[1], ["?"])) for p in bazi]
    lines.append(f"藏干 {cgs[0]:^4}  {cgs[1]:^4}  {cgs[2]:^4}  {cgs[3]:^4}")
    # 纳音
    nys = [NAYIN_TABLE.get(p, "?") for p in bazi]
    lines.append(f"纳音 {nys[0][:2]:^4}  {nys[1][:2]:^4}  {nys[2][:2]:^4}  {nys[3][:2]:^4}")
    lines.append("```")
    lines.append("")

    lines.append(f"☀️ 日主：{result.day_master} | 🏷️ 格局：{result.geju}")
    lines.append(f"🔧 用神：{result.yongshen}")

    # 五行 bars
    wx = getattr(result, 'wuxing', {}) or {}
    if wx:
        total = sum(wx.values()) or 1
        wx_str = " ".join(f"{k}{v}({v/total*100:.0f}%)" for k, v in wx.items())
        lines.append(f"📊 五行：{wx_str}")

    # 大运
    dayun = getattr(result, 'dayun', []) or []
    if dayun:
        dy_str = " → ".join(f"{a}岁{g}" for a, g in dayun[:4])
        lines.append(f"📅 大运：{dy_str}")

    # 神煞
    ss = getattr(result, 'shensha', []) or []
    if ss:
        lines.append(f"⭐ 神煞：{'、'.join(str(s) for s in ss[:6])}")

    lines.append("")
    lines.append("💡 回复「详细排盘」查看完整命盘")

    return "\n".join(lines)

# ============================================================
# 补全：空亡、星运、藏干十神、自坐、对比函数
# ============================================================

XUN_KONG = {
    "甲子": "戌亥","乙丑": "戌亥","丙寅": "戌亥","丁卯": "戌亥",
    "戊辰": "戌亥","己巳": "戌亥","庚午": "戌亥","辛未": "戌亥",
    "壬申": "戌亥","癸酉": "戌亥",
    "甲戌": "申酉","乙亥": "申酉","丙子": "申酉","丁丑": "申酉",
    "戊寅": "申酉","己卯": "申酉","庚辰": "申酉","辛巳": "申酉",
    "壬午": "申酉","癸未": "申酉",
    "甲申": "午未","乙酉": "午未","丙戌": "午未","丁亥": "午未",
    "戊子": "午未","己丑": "午未","庚寅": "午未","辛卯": "午未",
    "壬辰": "午未","癸巳": "午未",
    "甲午": "辰巳","乙未": "辰巳","丙申": "辰巳","丁酉": "辰巳",
    "戊戌": "辰巳","己亥": "辰巳","庚子": "辰巳","辛丑": "辰巳",
    "壬寅": "辰巳","癸卯": "辰巳",
    "甲辰": "寅卯","乙巳": "寅卯","丙午": "寅卯","丁未": "寅卯",
    "戊申": "寅卯","己酉": "寅卯","庚戌": "寅卯","辛亥": "寅卯",
    "壬子": "寅卯","癸丑": "寅卯",
    "甲寅": "子丑","乙卯": "子丑","丙辰": "子丑","丁巳": "子丑",
    "戊午": "子丑","己未": "子丑","庚申": "子丑","辛酉": "子丑",
    "壬戌": "子丑","癸亥": "子丑",
}

CHANG_SHENG = {
    "甲": ["亥","子","丑","寅","卯","辰","巳","午","未","申","酉","戌"],
    "乙": ["午","巳","辰","卯","寅","丑","子","亥","戌","酉","申","未"],
    "丙": ["寅","卯","辰","巳","午","未","申","酉","戌","亥","子","丑"],
    "丁": ["酉","申","未","午","巳","辰","卯","寅","丑","子","亥","戌"],
    "戊": ["寅","卯","辰","巳","午","未","申","酉","戌","亥","子","丑"],
    "己": ["酉","申","未","午","巳","辰","卯","寅","丑","子","亥","戌"],
    "庚": ["巳","午","未","申","酉","戌","亥","子","丑","寅","卯","辰"],
    "辛": ["子","亥","戌","酉","申","未","午","巳","辰","卯","寅","丑"],
    "壬": ["申","酉","戌","亥","子","丑","寅","卯","辰","巳","午","未"],
    "癸": ["卯","寅","丑","子","亥","戌","酉","申","未","午","巳","辰"],
}
CS_NAMES = ["长生","沐浴","冠带","临官","帝旺","衰","病","死","墓","绝","胎","养"]

def get_changsheng(day_gan, branch):
    if day_gan not in CHANG_SHENG: return "?"
    order = CHANG_SHENG[day_gan]
    return CS_NAMES[order.index(branch)] if branch in order else "?"

def get_kongwang(day_pillar):
    return XUN_KONG.get(day_pillar, "?")  

def compute_cg_shishen(day_gan, hidden_stems):
    return [_get_shishen(day_gan, s, for_hidden=True) for s in hidden_stems]

def compare_with_wenzhen(y, m, d, h, mi, gender_code, city="北京"):
    import urllib.request, json
    from src.engines.bazi import BaziEngine as BE
    gender = "女" if gender_code == 0 else "男"
    r = BE().calculate(y,m,d,h,mi,city,gender)
    ob = list(r.bazi)
    day_gan = ob[2][0]
    d_str = f'{y:04d}-{m:02d}-{d:02d}%20{h:02d}:{mi:02d}'
    url = f'https://bzapi3.iwzbz.com/getbasebz8.php?d={d_str}&s={gender_code}&today={d_str}&vip=0&yzs=0&pqf=0'
    wz = json.loads(urllib.request.urlopen(url,timeout=10).read())
    wz_pillars = [wz['bz'][str(i)]+wz['bz'][str(i+1)] for i in range(0,8,2)]
    items = [
        ("四柱"," ".join(ob)," ".join(wz_pillars)),
        ("十神",[r.shishen[i] if i<len(r.shishen) else _get_shishen(day_gan,ob[i][0]) for i in range(4)],wz.get('ss',[])),
        ("藏干",[BRANCH_HIDDEN.get(p[1],["?"]) for p in ob],wz.get('cg',[])),
        ("藏干十神",[compute_cg_shishen(day_gan,BRANCH_HIDDEN.get(p[1],["?"])) for p in ob],wz.get('cgss',[])),
        ("纳音",[NAYIN_TABLE.get(p,"?") for p in ob],wz.get('ny',[])),
        ("空亡",[get_kongwang(p) for p in ob],wz.get('kw',[])),
        ("星运",[get_changsheng(day_gan,p[1]) for p in ob],wz.get('xy',[])),
        ("自坐",[get_changsheng(day_gan,p[1]) for p in ob],wz.get('zz',[])),
    ]
    ok = sum(1 for _,o,t in items if o==t)
    for name,o,t in items:
        mark = "OK" if o==t else "DIFF"
        print(f"[{mark}] {name}")
        if o!=t:
            print(f"    Ours: {o}")
            print(f"    WZ:   {t}")
    our_dy = [g for _,g in r.dayun[:8]]
    wz_dy = wz.get('dayun',[])[:8]
    dy_ok = our_dy == wz_dy
    print(f"[{'OK' if dy_ok else 'DIFF'}] 大运(8步)")
    if not dy_ok:
        print(f"    Ours (with age): {[(a,g) for a,g in r.dayun[:4]]}")
        print(f"    WZ: {wz_dy[:4]}")
    print(f"Score: {ok+dy_ok}/{len(items)+1}")
