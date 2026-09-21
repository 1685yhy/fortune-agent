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
    # k11-C（事实纪律）：显示端全量（折行）——喂给 LLM 的神煞集合 = 用户实际可见集合，
    # 杜绝"卡内引用看不到的神煞"观感编造（2026-09-06 事故：排盘卡 [:12]/[:6] 截断而
    # advisor 喂全集，孤辰寡宿/童子煞/金神等引用用户看不到）。全集通常 14-20 项，
    # 微信自动折行一行为限；不再做任何截断（若未来超长需截断必须同步改 prompt 侧集合）。
    shensha = getattr(result, 'shensha', []) or []
    if shensha:
        lines.append("⭐ **神煞**")
        lines.append("  " + "、".join(str(s) for s in shensha))
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

    # 起运实岁串（k39-S2/T015：G5 问真口径「X岁X个月起运」）——引擎既有字段
    # qiyun_sui_desc 的纯确定性渲染，0 LLM、不新造文案、不改起运算法；
    # 标签沿用既有口径（见 src/tools/fortune_cycle.py 起运：{...}）。
    # getattr + 真值判断 = 字段缺失/为空时优雅降级（不上空壳行）。
    qy = getattr(result, "qiyun_sui_desc", "") or ""
    if qy:
        lines.append(f"⏳ 起运：{qy}")

    # 神煞 —— k11-C：显示端全量（与详细卡/LLM 事实包同一集合，禁截断；
    # 截断裂缝=观感编造事故根因，见 format_detailed_chart 同款注释）
    ss = getattr(result, 'shensha', []) or []
    if ss:
        lines.append(f"⭐ 神煞：{'、'.join(str(s) for s in ss)}")

    lines.append("")
    lines.append("💡 回复「详细排盘」查看完整命盘")

    return "\n".join(lines)


# ============================================================
# k11-A 事实包（LLM prompt 注入块，两链共用单一事实源）
# ============================================================

_LUNAR_MONTH_CN = ("正月", "二月", "三月", "四月", "五月", "六月",
                   "七月", "八月", "九月", "十月", "十一月", "腊月")


def format_fact_pack_block(result) -> str:
    """确定性事实包文本（k11-A）——主链 _format_chart 与 advisor prompt 共同注入。

    内容只读 BaziResult：current_stage（引擎 calculate 内 current_stage_facts 计算，
    口径见 bazi.py 该函数文档）+ liunian_rel/liunian_full 兜底。prompt 侧纪律：
    年龄/大运/换运年份/神煞只许引用本包与命盘数据行，禁止自行推算或编造。
    任何字段缺失 → 该行缺席或改为"勿推算"提示，绝不抛错、绝不编数值。
    """
    try:
        cs = getattr(result, "current_stage", None) or {}
        lines = []
        parts = []
        # 1) 当前日期 + 当前流年（立春界定，引擎 liunian_rel 同源）
        _yr = (cs.get("year") if cs else None) or (
            (getattr(result, "liunian_rel", None) or {}).get("year"))
        _ln_gz = (cs.get("liunian_ganzhi") if cs else None) or (
            (getattr(result, "liunian_rel", None) or {}).get("ganzhi"))
        _date = cs.get("date_iso") if cs else None
        seg = []
        if _date:
            seg.append(f"当前日期：{_date}")
        if _yr:
            seg.append(f"当前流年：{_yr} 年 {_ln_gz or '?'}（干支年，以立春为界）")
        # 2) 出生档案（公历=引擎排盘输入；农历=归一化后农历，与四柱自洽）
        _bs = cs.get("birth_solar") if cs else None
        _city = (cs.get("birth_city") if cs else None) or ""
        _bl = (cs.get("birth_lunar") if cs else None)
        if isinstance(_bs, (tuple, list)) and len(_bs) >= 5:
            _y, _m, _d, _h, _mi = _bs[:5]
            # 排盘口径时刻注记：真太阳时修正/晚子时归日与用户提供时刻不同才显示
            _hhmm = cs.get("chart_hhmm") if cs else None
            _note = ""
            try:
                if _hhmm and _hhmm != "%02d:%02d" % (_h, _mi):
                    _note = f"（排盘口径 {_hhmm}）"
            except Exception:
                pass
            seg.append("出生档案（公历）：%d年%d月%d日 %02d:%02d%s%s"
                       % (_y, _m, _d, _h, _mi,
                          f" {_city}" if _city else "", _note))
        if isinstance(_bl, (tuple, list)) and len(_bl) >= 3 and _bl[0]:
            _lm, _ld = _bl[1], _bl[2]
            _lm_txt = (_LUNAR_MONTH_CN[_lm - 1]
                       if isinstance(_lm, int) and 1 <= _lm <= 12 else str(_lm))
            _yp = getattr(result, "bazi", None)
            _yg = _yp[0] + "年" if _yp and _yp[0] else ""
            # review r1-5：晚子时归日/真太阳时跨日（23:xx 出生）时农历取自排盘口径
            # 的次日/修正日——与「出生档案（公历）」行并排时显式注记口径，防歧义
            _shift_note = ("（按排盘口径日期）" if (cs or {}).get("lunar_date_shifted")
                           else "")
            seg.append(f"出生（农历）：{_yg}{_lm_txt}{_ld}{_shift_note}")
        # 3) 当前年龄/当前大运段（核心修复：杜绝把换运岁数当当前年龄）
        if cs and cs.get("age_zhousui") is not None:
            seg.append("命主当前年龄：周岁 %s 岁（虚岁 %s）"
                       % (cs["age_zhousui"], cs["age_xusui"]))
        if cs and cs.get("dayun_ganzhi"):
            seg.append(
                "当前大运：%s（虚岁 %s-%s，约 %s-%s 年）%s"
                % (cs["dayun_ganzhi"], cs.get("dayun_sui_start"),
                   cs.get("dayun_sui_end"), cs.get("dayun_year_start"),
                   cs.get("dayun_year_end"),
                   ("；下一步：%s（虚岁 %s 起，约 %s 年起）"
                    % (cs.get("next_ganzhi"), cs.get("next_sui"),
                       cs.get("next_year"))
                    if cs.get("next_ganzhi") else "")))
        # 4) 神煞白名单纪律（名单本身在命盘数据段，两链均已注入）
        _ss = getattr(result, "shensha", None) or []
        if seg:
            lines.append("【确定性事实包（以下为排盘引擎按当前日期确定性算出的数值，"
                         "只许引用，禁止自行推算或编造）】")
            for s in seg:
                lines.append(f"- {s}")
            if _ss:
                lines.append("- 神煞纪律：本盘神煞全集见上方命盘数据（共 %d 个）；"
                             "只许引用名单内神煞，禁止自造或引用名单外的任何神煞名"
                             % len(_ss))
        # 5) 无事实包字段时的防编造提示（老对象/手工 mock/降级对象兜底）
        else:
            _hint = "【提示】本次未提供当前年龄/大运/换运年份等确定性数值，" \
                    "回答涉及年龄与大运时只引用上方命盘数据已有内容，" \
                    "不得自行推算编造年龄或换运年份。"
            if _ss:
                _hint += "（本盘神煞 %d 个：名单见上方命盘数据，只许引用名单内神煞）" \
                         % len(_ss)
            lines.append(_hint)
        return "\n".join(lines)
    except Exception:
        # 事实包是 prompt 增强：任何异常返回最小防编造提示，不阻塞主流程
        return "【提示】本次未提供当前年龄/大运等确定性数值，回答时不得自行推算编造。"


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

# ── k76：`compare_with_wenzhen()` 已**删除**（控制方按「接口安全红线」裁定）──
# 被删函数把「出生日期 + 性别」拼进 URL 发给**第三方问真八字**排盘接口，属
# **未披露的对外发送**；且全仓**零调用**（死代码）——死代码 + 未披露外发 = 双重
# 问题，故整段移除，避免将来被误启用。
#
# 删除前已核**零调用**（含动态形态）：
#   - 全树（排除 .git）仅本文件出现该名字，且只有 def 行；
#   - getattr / eval / __import__ / importlib / 字符串拼接 构造调用 → 0 命中；
#   - 从本模块 import 的 4 处（src/tools/fortune_cycle.py、src/engines/bazi.py、
#     tests/bulk_compare_wenzhen.py、scripts/calibrate_qz_250.py 等）的导入清单里
#     **都不含**该名字。
#
# 离线标定脚本（tests/bulk_compare_wenzhen.py、scripts/calibrate_qz_250.py、
# scripts/scrape_wenzhen.py）**不受影响**：它们各自独立构造请求，不走本函数——
# 那些是人工离线跑的对标工具，不是服务的运行时代码路径。
