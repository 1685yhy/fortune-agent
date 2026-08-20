"""专项论断引擎（L2-5，问真 VIP 同款）：论财 / 论事业 / 论健康。

数据基础：BaziResult（bazi 四柱 / shishen 十神 / wuxing 五行统计 /
wuxing_energy{counts, wangshuai, changsheng, strength, yongshen} /
dayun 大运 / dayun_rel 大运干支关系）。

设计原则：
- 纯规则生成，确定性（同一命盘恒得同一论断），不调 LLM；
  LLM 润色为可选项（本期不做）。
- 每项输出结构化 {summary, points: [{title, text}], luck_phase?}：
  - luck_phase：论财 = 财星大运阶段；论事业 = 官杀/印星大运阶段；
    论健康不输出运程阶段（以原局五行失衡为主）。
- 论财口径（问真同款）：
  - 财星 = 日主所克五行（我克）；透干看十神正偏财，得地看地支本气，
    得令看月令旺相休囚死；
  - 格局判定顺序：劫财夺财（须身弱，身强劫财为用不夺财）→ 财星不显 →
    财多身弱 → 财旺身弱 → 身强财旺 → 身强财弱 → 身财相当
    （先断凶后断吉，防误判）。
- 论事业口径：官杀（克我）、印星（生我）、食伤（我生）三组十神状态
  优先于格局名；官印相生 / 官杀攻身 / 食伤吐秀等为事业格局。
- 论健康口径：五行过旺（≥4 或达全局最高且 ≥3）/ 过弱（=0）→ 中医
  脏腑对应（木肝/火心/土脾胃/金肺/水肾）+ 体质倾向（日主强弱 + 冬夏月令）。
"""
import logging

from src.engines.wuxing import (
    GAN_WUXING, ZHI_WUXING, SHENG_CYCLE, KE_CYCLE, SHENG_ME, KE_ME,
)

logger = logging.getLogger(__name__)

STRONG = ("旺", "偏旺")
WEAK = ("弱", "偏弱")

# 五行 → 脏腑（中医五行对应，问真健康论断同款口径）
ZANGFU = {
    "木": ("肝胆", "筋骨、眼睛、神经"),
    "火": ("心、小肠", "血液循环、睡眠、精神"),
    "土": ("脾胃", "消化系统、肌肉"),
    "金": ("肺、大肠", "呼吸系统、皮肤、咽喉"),
    "水": ("肾、膀胱", "泌尿系统、生殖、内分泌"),
}

# 五行过旺 / 过弱阈值
WANG_THRESHOLD = 4      # 单五行 ≥ 4 视为过旺（天干+地支本气，共 8 个位置）
WANG_TOP_THRESHOLD = 3  # 唯一最高且 ≥ 3 亦视为偏旺（防四柱分散型命盘漏判）
WEAK_THRESHOLD = 0      # 单五行 = 0 视为不足（缺行）


# ── 通用辅助 ─────────────────────────────────────────────────────

def _strength(result) -> str:
    return result.wuxing_energy.get("strength", "中和")


def _month_zhi(result) -> str:
    return result.bazi[1][1]


def _dizhi_counts(result) -> dict:
    """地支本气五行统计（与 BaziResult.wuxing 同口径中的地支部分，用于得地判断）。"""
    out = {w: 0 for w in KE_CYCLE}
    for p in result.bazi:
        out[ZHI_WUXING[p[1]]] += 1
    return out


def _dayun_phases(result, want_wx: set, tag: str) -> list:
    """大运阶段：干支五行命中 want_wx 的运步 → [{sui, ganzhi, desc}]。

    天干与地支分别判（任一命中即算该运），desc 注明透/坐与作用标签。
    例：日主乙木（财土），大运 戊辰 → 天干透土、地支辰土 → 财星大运。
    """
    out = []
    for (sui, gz), _rel in zip(result.dayun or [], result.dayun_rel or []):
        if not gz or len(gz) < 2:
            continue
        hit = []
        swx, zwx = GAN_WUXING.get(gz[0]), ZHI_WUXING.get(gz[1])
        if swx in want_wx:
            hit.append("天干透%s" % swx)
        if zwx in want_wx:
            hit.append("地支藏%s" % zwx)
        if hit:
            out.append({
                "sui": sui,
                "ganzhi": gz,
                "desc": "%d 岁起运 %s：%s，%s" % (sui, gz, "、".join(hit), tag),
            })
    return out


def _phase_points(phases: list, title: str, fallback: str) -> list:
    """luck_phase → 断语要点（有阶段出阶段，无阶段出兜底文案）。"""
    if phases:
        names = "、".join("「%s岁 %s」" % (p["sui"], p["ganzhi"]) for p in phases[:6])
        return [{"title": title, "text": "行至%s，%s" % (names, phases[0]["desc"].split("，", 1)[-1])}]
    return [{"title": title, "text": fallback}]


# ── 论财 ─────────────────────────────────────────────────────────

CAI_GEJU_TEXT = {
    "财多身弱": "财星过于旺盛而日主偏弱，属「富屋贫人」之象——财机会多但身弱难担，财来财去、聚财不易。",
    "财旺身弱": "财星偏旺而日主不够强，身弱不胜财，求财较辛苦，财多反为累。",
    "身强财旺": "日主强旺足以任财，财星亦有力量，属能挣能守之局，财运亨通、求财得力。",
    "身强财弱": "日主强旺而财星不足，先立身再求财，大运流年见财方发。",
    "财星不显": "原局财星不透不藏，财运多赖后天赋能累积，宜稳扎稳打、以专业立财。",
    "劫财夺财": "劫财透干夺财，钱财易因合伙、借贷、兄弟朋友而损耗，防破财漏财。",
    "身财相当": "日主与财星力量相当，财来财去皆有定数，平稳理财可积少成多。",
}

CAI_ADVICE = {
    "财多身弱": "宜先强身（补印比劫帮扶）后求财；不宜负债经营，理财求稳，以固定资产与储蓄为重。",
    "财旺身弱": "量入为出，避免高风险投资；多借助贵人（印星）之力，稳字当头。",
    "身强财旺": "财旺可任，宜积极进取，把握财星大运阶段投资置业；唯需防财星过旺之时乐极生悲。",
    "身强财弱": "主业精进为先，待财星大运（财星流年）再行投资，逢财年果断出手。",
    "财星不显": "以专长谋财，细水长流；大运流年遇财星透出之年，即财运转折点。",
    "劫财夺财": "合伙须立字据，钱不外借，防损友；大运见官杀制劫之运，财运转佳。",
    "身财相当": "理财以均衡为要，收支同步规划；逢财星流年适当进取，逢劫财流年守成为上。",
}


def lun_cai(result) -> dict:
    """论财（结构化论断）。

    规则：
    1. 财星 = 日主所克五行；透干数 = 十神正偏财个数；得地 = 地支本气
       财五行个数；得令 = 财星月令旺/相。
    2. 财星力量分 score = 透干×2 + 得地×1 + 得令+2。
    3. 格局判定（先断凶后断吉，见模块注释）；大运财运 = 财星大运阶段。
    """
    day_gan = result.bazi[2][0]
    day_wx = GAN_WUXING[day_gan]
    cai_wx = KE_CYCLE[day_wx]  # 我克 → 财
    shishen = result.shishen or []
    zheng, pian = shishen.count("正财"), shishen.count("偏财")
    tougan = zheng + pian
    dizhi_map = _dizhi_counts(result)
    dizhi = dizhi_map.get(cai_wx, 0)
    ws = (result.wuxing_energy.get("wangshuai") or {}).get(cai_wx, "")
    deling = ws in ("旺", "相")
    strength = _strength(result)

    score = tougan * 2 + dizhi + (2 if deling else 0)
    jiecai = shishen.count("劫财")

    # 格局判定（顺序即优先级：凶局/显性局优先，再按身财强弱）
    # 劫财夺财须身弱方为凶：身强时劫财为用（帮身任财），不夺财——
    # 身强劫财盘应落入身强财旺/身财相当等局（终审 Fix，2026-08-20）
    if jiecai >= 2 and dizhi >= 2 and strength in WEAK:
        geju = "劫财夺财"
    elif tougan == 0 and dizhi == 0:
        geju = "财星不显"
    elif strength in WEAK and score >= 7:
        geju = "财多身弱"
    elif strength in WEAK and score >= 5:
        geju = "财旺身弱"
    elif strength in STRONG and score >= 6:
        geju = "身强财旺"
    elif strength in STRONG and score <= 4:
        geju = "身强财弱"
    else:
        geju = "身财相当"

    phases = _dayun_phases(result, {cai_wx}, "财星大运，求财机会增加，宜把握投资机遇")
    points = [
        {"title": "财星透干",
         "text": ("正财透 %d、偏财透 %d，" % (zheng, pian))
                + ("财星透干有力，财来路明、求财主动。" if tougan >= 2
                   else "财星单透，财气偏轻，需大运流年引动。" if tougan == 1
                   else "四柱不见财星透干，财不露白、宜守宜藏。")},
        {"title": "财星得地",
         "text": "地支财星 %d 位，财星在月令为「%s」。" % (dizhi, ws or "—")
                + ("财得月令之助，根基稳固。" if deling
                   else "财星失令，财气须待岁运扶助。")},
        {"title": "财运格局", "text": CAI_GEJU_TEXT[geju]},
        {"title": "聚财建议", "text": CAI_ADVICE[geju]},
    ]
    points += _phase_points(
        phases, "大运财运",
        "原局无财星大运之助，财运随流年起伏，宜以稳为主、逢财年（流年见%s）再进取。" % cai_wx)
    summary = ("日主%s，%s。财星（%s）透干 %d 位、得地 %d 位、%s令，"
               "财运格局：%s。") % (result.day_master, strength, cai_wx,
                                    tougan, dizhi, "得" if deling else "失", geju)
    return {"summary": summary, "points": points, "luck_phase": phases}


# ── 论事业 ───────────────────────────────────────────────────────

SHIYE_GEJU_TEXT = {
    "官印相生": "官星得印星化泄——官不伤身、印能护官，事业上有贵人与平台加持，宜走体制内或大平台路线，职务晋升运佳。",
    "身强官旺": "日主强旺足以担当官杀，有管理才能与魄力，宜任管理职务或执掌一方。",
    "官杀攻身": "官杀旺而日主弱，工作压力大、竞争激烈；宜以印化杀——多学习、多结贵人，或走专业技术路线化解压力。",
    "官星有气": "官星透干有力，事业目标明确、循规蹈矩可得认可，宜在稳定岗位深耕。",
    "食伤吐秀": "食伤旺而聪慧外露、无官束缚，宜走创意、技术、专业路线或自由职业，以才华立身。",
    "印星护身": "印星护身而无官压，宜以学识、证书、平台背书立身，走学术、专业、辅助型角色更能发挥。",
    "无官无印": "原局无官无印，不受体制约束，宜自主创业或自由职业，靠自己实力闯荡，一技傍身为安身之本。",
    "事业平稳": "事业运平稳，按部就班亦有小成，宜深耕主业、静待大运引动。",
}

SHIYE_ADVICE = {
    "官印相生": "顺势走体制/大平台路线，多向贵人学习；大运见官杀印星时主动争取晋升机会。",
    "身强官旺": "宜争取管理岗位、项目负责权；注意以印星制衡官杀，避免刚愎自用。",
    "官杀攻身": "以专业技能立身，把压力化为动力；多结贵人、多考证进修，借印星之力缓释压力。",
    "官星有气": "专注主业、按制度办事，稳步积累资历；大运见印星时是升职窗口。",
    "食伤吐秀": "发挥创意与技术优势，宜自主输出成果（作品/专利/项目），避免受限体制。",
    "印星护身": "深耕学识与专业背书，宜走专家路线；必要时借平台之力向上发展。",
    "无官无印": "靠实力与口碑立身，宜轻装上阵自主决策；注意补强专业技能这一安身之本。",
    "事业平稳": "按部就班、积攒口碑，待官杀/印星大运引动时事业格局再上台阶。",
}


def lun_shiye(result) -> dict:
    """论事业（结构化论断）。

    规则：
    1. 官杀（克我）、印星（生我）、食伤（我生）三组十神透干状态；
    2. 事业格局判定顺序：官印相生 → 官杀攻身 → 身强官旺 → 官星有气 →
       食伤吐秀 → 印星护身 → 无官无印 → 事业平稳；
    3. 事业阶段 = 官杀/印星大运（官运与贵人运并重）。
    """
    day_gan = result.bazi[2][0]
    day_wx = GAN_WUXING[day_gan]
    guan_wx = KE_ME[day_wx]    # 克我 → 官杀
    yin_wx = SHENG_ME[day_wx]  # 生我 → 印
    shishen = result.shishen or []
    guan = shishen.count("正官") + shishen.count("七杀")
    yin = shishen.count("正印") + shishen.count("偏印")
    shi_shang = shishen.count("食神") + shishen.count("伤官")
    strength = _strength(result)
    geju_name = result.geju or ""

    # 事业格局判定（顺序即优先级）
    if guan >= 1 and yin >= 1:
        geju = "官印相生"
    elif guan >= 2 and strength in WEAK:
        geju = "官杀攻身"
    elif guan >= 1 and strength in STRONG:
        geju = "身强官旺"
    elif guan >= 1:
        geju = "官星有气"
    elif shi_shang >= 2:
        geju = "食伤吐秀"
    elif yin >= 2:
        geju = "印星护身"
    elif guan == 0 and yin == 0:
        geju = "无官无印"
    else:
        geju = "事业平稳"

    guan_text = ("官杀透干 %d 位（正官 %d、七杀 %d）。" % (guan,
                 shishen.count("正官"), shishen.count("七杀")) +
                 ("官星有力，事业有方向。" if guan >= 2
                  else "官星单透，事业线单一而清晰。" if guan == 1
                  else "四柱无官杀透干，不受体制约束，自主性强。"))
    yin_text = ("印星透干 %d 位（正印 %d、偏印 %d）。" % (yin,
                shishen.count("正印"), shishen.count("偏印")) +
                ("印星有力，贵人学养皆备。" if yin >= 2
                 else "印星单现，贵人运时有助益。" if yin == 1
                 else "四柱不见印星，凡事多靠自身摸索。"))

    phases = _dayun_phases(result, {guan_wx, yin_wx}, "官印大运，事业与贵人运进入关键期，宜进取晋升")
    points = [
        {"title": "官杀状态", "text": guan_text},
        {"title": "印星状态", "text": yin_text},
        {"title": "事业格局", "text": SHIYE_GEJU_TEXT[geju]},
        {"title": "事业建议", "text": SHIYE_ADVICE[geju]},
    ]
    if shi_shang:
        points.insert(2, {"title": "才华倾向",
                          "text": "食神伤官透干 %d 位，才华外露，宜将专业特长融入事业路径。" % shi_shang})
    points += _phase_points(
        phases, "事业阶段",
        "原局官印大运不明显，事业以稳健积累为主，逢官杀/印星流年再把握升迁机遇。")
    summary = ("日主%s，%s。官杀透干 %d 位、印星透干 %d 位，"
               "事业格局：%s。") % (result.day_master, strength, guan, yin, geju)
    return {"summary": summary, "points": points, "luck_phase": phases}


# ── 论健康 ───────────────────────────────────────────────────────

def _health_imbalance(result) -> dict:
    """五行失衡：过旺 / 过弱（按阈值，见模块常量）。"""
    counts = result.wuxing or {}
    if not counts:
        return {"wang": {}, "ruo": {}}
    mx = max(counts.values())
    # 过旺：≥4（绝对过旺），或达全局最高且 ≥3（最高并列一并计入，防四柱分散型漏判）
    wang = {w: c for w, c in counts.items()
            if c >= WANG_THRESHOLD or (c == mx and c >= WANG_TOP_THRESHOLD)}
    ruo = {w: c for w, c in counts.items() if c <= WEAK_THRESHOLD}
    return {"wang": wang, "ruo": ruo}


def _constitution_text(result) -> str:
    """体质倾向：日主强弱 + 冬夏月令寒热。"""
    strength = _strength(result)
    month_zhi = _month_zhi(result)
    parts = []
    if strength in WEAK:
        parts.append("日主偏弱，先天体质与精力底子偏薄，易疲劳、抵抗力一般，需规律作息")
    elif strength in STRONG:
        parts.append("日主强旺，体质底子较好、恢复力强；但五行过旺之处易亢进生疾，需注意节制")
    else:
        parts.append("日主中和，体质较为平衡")
    if month_zhi in ("亥", "子", "丑"):
        parts.append("生于冬月，命局偏寒，体寒怕冷，宜温补、注意保暖与关节养护")
    elif month_zhi in ("巳", "午", "未"):
        parts.append("生于夏月，命局偏燥热，易上火口干，宜清热润燥、多饮水")
    return "；".join(parts) + "。"


def lun_jiankang(result) -> dict:
    """论健康（结构化论断，无运程阶段）。

    规则：
    1. 五行失衡：过旺（≥4 或唯一最高且 ≥3）/ 过弱（=0）→ 中医脏腑对应
       （木肝/火心/土脾胃/金肺/水肾）+ 易发方向；
    2. 体质倾向：日主强弱（底子）+ 冬夏月令（寒热偏性）；
    3. 养生建议：补不足、泄有余。
    """
    counts = result.wuxing or {}
    im = _health_imbalance(result)
    wang, ruo = im["wang"], im["ruo"]

    # 脏腑预警
    alerts = []
    for wx, c in sorted(wang.items(), key=lambda kv: -kv[1]):
        organs, systems = ZANGFU.get(wx, ("", ""))
        alerts.append("%s（%d 位）过旺 → %s系统易亢进，注意%s" % (wx, c, organs, systems))
    for wx, c in sorted(ruo.items(), key=lambda kv: -kv[1]):
        organs, systems = ZANGFU.get(wx, ("", ""))
        alerts.append("%s（%d 位）不足 → %s先天偏弱，注意%s" % (wx, c, organs, systems))

    points = [
        {"title": "五行失衡",
         "text": ("五行分布：%s。" % "、".join("%s %d" % (w, c) for w, c in counts.items()))
                + ("其中过旺：%s；不足：%s。" % (
                    "、".join(wang) if wang else "无",
                    "、".join(ruo) if ruo else "无"))},
    ]
    if alerts:
        points.append({"title": "脏腑预警", "text": "；".join(alerts) + "。"})
    else:
        points.append({"title": "脏腑预警", "text": "五行分布均衡，无明显脏腑偏亢或不足之象。"})
    points.append({"title": "体质倾向", "text": _constitution_text(result)})

    # 养生建议
    tips = []
    for wx in sorted(ruo):
        tips.append("补%s：宜食%s类食物，作息规律以养%s" % (
            wx, {"木": "绿色蔬果", "火": "红色谷物", "土": "五谷杂粮",
                 "金": "白色润肺", "水": "黑色益肾"}.get(wx, ""),
            ZANGFU.get(wx, ("", ""))[0]))
    for wx in sorted(wang):
        tips.append("泄%s：忌熬夜过劳、情绪紧张，疏泄%s气以防淤积生疾" % (wx, wx))
    points.append({"title": "养生建议",
                   "text": "；".join(tips) if tips
                   else "五行平和，保持均衡饮食、规律运动与良好作息即可。"})

    summary = ("五行分布：%s。失衡点：%s。%s") % (
        "、".join("%s %d" % (w, c) for w, c in counts.items()),
        ("%s过旺、%s不足" % ("、".join(wang) if wang else "无", "、".join(ruo) if ruo else "无")),
        _constitution_text(result))
    return {"summary": summary, "points": points}
