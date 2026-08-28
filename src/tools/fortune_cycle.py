"""流月流年运势工具规则层（批次 2 E3，cap_id: fortune_cycle）：
参数解析 + 流年/流月干支推演 + 十神映射解读 + 吉凶月提示 + 结果卡片。

推演口径（全部复用既有引擎资产，不引入新依赖、不新起引擎）：
- 流年干支：引擎 liunian_ganzhi（年柱 + 岁差，六十甲子循环）——出生干支年取
  BaziResult.liunian_full[0]["year"]（立春界定，与年柱同口径，2026-08-20 修复）；
  目标年可为任意公历年（负岁差 mod 60 亦成立），超出 30 年流年表窗口时
  公式直接成立（2027 年对 1990 生人即属此例，已验证与引擎表同源）。
- 流月干支：五虎遁（WUHU_DUN 年干定寅月首干）+ 地支按公历月近似——
  立春（约 2 月 4 日）起寅月为正月，公历 2 月 ≈ 寅月、1 月 ≈ 丑月（腊月）、
  12 月 ≈ 子月（冬月）；月干 = 寅月首干 + (支序 − 2)。年内 12 月序列与引擎
  liuyue(year_ganzhi)（农历正月起寅月）完全一致，仅月序锚点不同（公历月 vs
  农历月），±1 日级边界差异（问真按节切月）在报告注明。
- 十神：D2 派生护栏口径——bazi_formatter._get_shishen(day_gan, gan,
  for_hidden=True)（同干 → 比肩），天干与地支藏干（BRANCH_HIDDEN，子平真诠表）
  同口径；藏干取全（本气/中气/余气），吉凶月评分取本气。
- 十神吉凶（四吉四凶两中，命理通识）：吉 = 正官/正印/正财/食神；
  需留意 = 七杀/偏印/伤官/劫财；中性 = 比肩/偏财。
- 关注维度 → 十神映射（通识，映射依据详见报告）：
  事业 = 官杀（职位职权/挑战压力）+ 印星（名誉贵人）+ 比劫（同侪竞争协作）；
  财运 = 正偏财（财星本体）+ 食伤（财源，食伤生财）；
  感情 = 男命财为妻星（正财=妻、偏财=缘）+ 女命官为夫星（正官=夫、七杀=偏缘），
  双口径并取（正财/偏财/正官/七杀）。
- 大运上下文：result.dayun 虚岁定位（虚岁 = 目标年 − 出生干支年 + 1，与引擎
  liunian_full age 同口径，每步 10 年 s~s+9）；起运/交运文案直接引用引擎
  qiyun_desc / jiaoyun（问真口径，未改动）。
- 默认值：目标年份/月份缺省 → 今年/本月（标准库 datetime，now 可注入
  以便确定性测试）；流年干支按公历年整年一柱（与引擎流年表 liunian_full
  同口径），立春换年的标签语境差异（立春前正月仍属上一年流年）在整年
  视角下不影响逐年干支，报告注明。

调用方（handler._tool_fortune_cycle）：出生文本 → parse_cycle_params 解析 →
_extract_bazi_info 解析 → 既有 BaziEngine.calculate → 本模块出卡片。
"""
import re
from datetime import datetime
from typing import List, Optional, Tuple

from src.engines.bazi import DIZHI, NAYIN, TIANGAN, WUHU_DUN, liunian_ganzhi
from src.engines.bazi_formatter import BRANCH_HIDDEN, _get_shishen

# 关注维度 → 相关十神（通识映射，报告标注依据）
FOCUS_TEN_SHEN = {
    "事业": ["正官", "七杀", "正印", "偏印", "比肩", "劫财"],
    "财运": ["正财", "偏财", "食神", "伤官"],
    "感情": ["正财", "偏财", "正官", "七杀"],
}
FOCUS_NAMES = ("事业", "财运", "感情")

# 十神吉凶（四吉四凶两中，命理通识）——吉凶月评分与单月吉凶判定共用
TEN_SHEN_LUCK = {
    "吉": ("正官", "正印", "正财", "食神"),
    "中": ("比肩", "偏财"),
    "需留意": ("七杀", "偏印", "伤官", "劫财"),
}
_LUCK_SCORE = {s: 1 for s in TEN_SHEN_LUCK["吉"]}
_LUCK_SCORE.update({s: 0 for s in TEN_SHEN_LUCK["中"]})
_LUCK_SCORE.update({s: -1 for s in TEN_SHEN_LUCK["需留意"]})

# 单十神一句点评（通识化解读文案，用于关注维度命中摘要）
_TEN_SHEN_NOTE = {
    "正官": "官星当值，循正途有升迁名位之机",
    "七杀": "七杀透显，挑战压力并存，宜实干破局",
    "正印": "印星照命，贵人提携、学业名誉有利",
    "偏印": "偏印当道，思路独到却易多思多虑",
    "正财": "正财入局，正职财源稳定，宜守成积累",
    "偏财": "偏财引动，投资机遇增多而波动亦大",
    "食神": "食神泄秀，才艺表达顺畅，谋事多得助力",
    "伤官": "伤官透出，防口舌是非，宜低调行事",
    "比肩": "比肩助力，合伙协作可成，亦防竞争分利",
    "劫财": "劫财临位，防破财与人情借贷，理财宜紧",
}

# 结构化键形态：birth:/year:/month:/focus:（半/全角冒号与等号均收）
_CYCLE_KEY_RE = re.compile(r'^(birth|year|month|focus)\s*[:：=＝]\s*(.*)$', re.I)
# 文本标签兜底：目标年份（公历 4 位）、目标月份、关注维度（从后向前匹配，
# 出生描述本身含 出生年/月 日期，取最后一个命中避免误吞出生日期）
_YEAR_RE = re.compile(r'([12]\d{3})\s*年')
_MONTH_RE = re.compile(r'(\d{1,2})\s*月')
_FOCUS_RE = re.compile(r'(事业|财运|感情)')


# ============================================================
# 参数解析（结构化键优先，文本标签兜底——与 hehun/naming 同型）
# ============================================================

def parse_cycle_params(text) -> Optional[dict]:
    """把工具参数文本解析为 {birth, year?, month?, focus?}；解析不出 → None。

    支持三种形态：
    1. 结构化多键（JSON 工单 serialize 产物，主路径）：
       "birth: 1990年5月20日 午时 北京 男\nyear: 2027\nmonth: 6\nfocus: 事业,财运"
       （键前缀识别，冒号半/全角、等号均可；键序无关；year/month/focus 可省）
    2. 文本标签兜底（legacy <tool_call>）：
       "1990年5月20日 午时 北京 男，2027年6月，看财运"
       —— 年份/月份/维度各取最后一个命中（出生日期内含 1990年/5月 不被误吞），
       剔除后剩余文本（含出生年）作为出生信息
    3. 拆不出出生信息 → None（调用方转 needs_info 追问）
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    info: dict = {}

    # 形态 1：结构化键（LLM 走 schema 必填键 birth）
    for line in raw.splitlines():
        m = _CYCLE_KEY_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip()
        if val:
            info[key] = val
    if info:
        return info

    # 形态 2：文本标签（日期感知：完整日期 "YYYY年M月D日" 内的年/月是出生日期
    # 的一部分，剔除后才是目标年/月；目标年取最后一个非日期年份匹配，
    # 目标月取最后一个非日期月份匹配——"2027年看运势 1990年5月20日 午时 北京 男"
    # 与 "1990年5月20日 午时 北京 男 2027年6月" 两种语序都能正确拆出）
    year_matches = [m for m in _YEAR_RE.finditer(raw) if not _is_date_year(m, raw)]
    month_matches = [m for m in _MONTH_RE.finditer(raw) if not _is_date_month(m, raw)]
    focuses = _FOCUS_RE.findall(raw)
    # 一次成型：目标年/月区间均基于原始串坐标（先后剔除会让后续坐标错位，
    # 2026-08-27 实测 "…男 2027年6月 看财运" 残留 6月/看财 的修复）
    removals = []
    if year_matches:
        info["year"] = year_matches[-1].group(1)
        removals.append(year_matches[-1].span())
    if month_matches:
        info["month"] = month_matches[-1].group(1)
        removals.append(month_matches[-1].span())
    rest = raw
    if removals:
        parts, prev = [], 0
        for s, e in sorted(removals):
            parts.append(raw[prev:s])
            prev = e
        parts.append(raw[prev:])
        rest = "".join(parts)
    if focuses:
        info["focus"] = "，".join(dict.fromkeys(focuses))
        # 连带剥"看/看看财运"这类前置动词，避免残留"看财"污染出生文本
        for f in dict.fromkeys(focuses):
            rest = re.sub(r'(?:看|看看)?%s' % f, "", rest)
    # 剔除目标年/月/维度后，剩余须含出生年（\d{4}年）才算出生信息；
    # 出生信息从首个出生年起始截取（"2027年看运势，1990年…" 的前缀杂语剥离，
    # 两种语序得到同一份干净的出生文本）
    m = re.search(r'[12]\d{3}\s*年', rest)
    if not m:
        return None
    info["birth"] = rest[m.start():].strip(" ，,。、")
    return info


def _is_date_year(match, text: str) -> bool:
    """年份匹配后紧跟 "M月D日" → 完整日期（出生日期的一部分，非目标年份）。"""
    return bool(re.match(r'\d{1,2}\s*月\d{1,2}\s*日', text[match.end():]))


def _is_date_month(match, text: str) -> bool:
    """月份匹配后紧跟 "D日" → 完整日期（出生日期的一部分，非目标月份）。"""
    return bool(re.match(r'\d{1,2}\s*日', text[match.end():]))


# ============================================================
# 默认值（目标年份/月份缺省 → 今年/本月，标准库 datetime，now 可注入）
# ============================================================

def default_targets(now: Optional[datetime] = None) -> Tuple[int, int]:
    """缺省目标年份/月份：今年/本月（标准库 datetime；now 供测试注入）。"""
    now = now or datetime.now()
    return now.year, now.month


def parse_target_year(raw) -> Optional[int]:
    """目标年份合法化：1900-2300 的整数 → int；其余 → None（调用方点名 year）。"""
    try:
        y = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return y if 1900 <= y <= 2300 else None


def parse_target_month(raw) -> Optional[int]:
    """目标月份合法化：1-12 的整数 → int；其余 → None（调用方点名 month）。"""
    try:
        m = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return m if 1 <= m <= 12 else None


def parse_focus(raw) -> List[str]:
    """关注维度规范化：事业/财运/感情（可逗号/顿号/、分隔多选），未知词忽略。"""
    if not raw:
        return []
    parts = re.split(r'[，,、\s]+', str(raw))
    return list(dict.fromkeys(p for p in parts if p in FOCUS_NAMES))


# ============================================================
# 流年/流月干支推演（引擎口径复用）
# ============================================================

def birth_pillar_year(result) -> int:
    """出生干支年（立春界定，与年柱同口径）：引擎流年表首项 year。"""
    if result.liunian_full:
        return result.liunian_full[0]["year"]
    # 兜底：农历年（晚子时已归一，与 _calc_liunian 同源近似）
    return int(result.lunar.get("year", 0))


def flow_year_gz(result, target_year: int) -> str:
    """流年干支（引擎 liunian_ganzhi 同口径：年柱 + 岁差 mod 60，六十甲子循环）。

    目标年可为任意公历年（含早于出生年，负岁差 mod 60 成立）；与引擎流年表
    liunian_full 同源，超出 30 年窗口的年份公式直接成立（不另起表）。
    """
    return liunian_ganzhi(birth_pillar_year(result), result.bazi[0], target_year)


def flow_month_gz(year_gz: str, month: int) -> str:
    """流月干支（五虎遁 + 公历月近似：立春≈2月4日，公历2月≈寅月正月）。

    支 = DIZHI[month % 12]（子=0 序：2月→寅、1月→丑、12月→子）；
    干 = WUHU_DUN[年干] 定寅月首干 + ((月 − 2) mod 12)（正月前差 11 个月补
    整循环，公历 1 月=丑月与引擎 12 月癸丑同干）。年内 12 月序列与引擎
    liuyue(year_gz) 完全一致（仅月序锚点从农历正月换成公历 2 月）。
    """
    gan = TIANGAN[(WUHU_DUN.get(year_gz[0], 2) + (month - 2) % 12) % 10]
    zhi = DIZHI[month % 12]
    return gan + zhi


# ============================================================
# 十神（D2 派生护栏口径：for_hidden=True，同干 → 比肩）
# ============================================================

def shishen_of(day_gan: str, gan: str) -> str:
    """任意天干（流年/流月干、藏干）对日主的十神（for_hidden=True 口径）。"""
    return _get_shishen(day_gan, gan, for_hidden=True)


def hidden_stems_of(zhi: str) -> List[str]:
    """地支藏干（子平真诠表，本气/中气/余气序）。"""
    return BRANCH_HIDDEN.get(zhi, [zhi])


def year_ten_shen_set(day_gan: str, year_gz: str) -> set:
    """流年十神集合：天干十神 + 地支全藏干十神（去重）。"""
    return {shishen_of(day_gan, year_gz[0])} | {
        shishen_of(day_gan, g) for g in hidden_stems_of(year_gz[1])}


def luck_score(day_gan: str, month_gz: str) -> int:
    """单月吉凶评分：月干十神 + 月支本气十神 按 四吉四凶两中 计分（-2..+2）。"""
    gan_s = shishen_of(day_gan, month_gz[0])
    zhi_s = shishen_of(day_gan, hidden_stems_of(month_gz[1])[0])
    return _LUCK_SCORE.get(gan_s, 0) + _LUCK_SCORE.get(zhi_s, 0)


def luck_word(score: int) -> str:
    """评分 → 吉/平/需留意（≥1 吉；=0 平；≤-1 需留意）。"""
    if score >= 1:
        return "吉"
    if score <= -1:
        return "需留意"
    return "平"


def best_worst_months(day_gan: str, year_gz: str) -> Tuple[int, int]:
    """全年 12 流月吉凶评分 → (最吉月, 最需留意月)（1-12 公历月；同分时最吉月
    取最早、最需留意月取最晚——best=max key=(scores[i],-i) 取最早，worst=min
    key=(scores[i],-i) 取最晚，M4 文档修正对齐实现）。"""
    scores = [luck_score(day_gan, flow_month_gz(year_gz, m)) for m in range(1, 13)]
    best = max(range(12), key=lambda i: (scores[i], -i))
    worst = min(range(12), key=lambda i: (scores[i], -i))
    return best + 1, worst + 1


def dayun_step_for(result, target_year: int) -> Optional[Tuple[int, str]]:
    """目标年份所在大运（虚岁定位，与引擎 liunian_full age 同口径，s~s+9 每步 10 年）。"""
    if not result.dayun or not result.liunian_full:
        return None
    age = target_year - birth_pillar_year(result) + 1
    for sui, gz in result.dayun:
        if sui <= age <= sui + 9:
            return sui, gz
    return None


# ============================================================
# 关注维度要点（十神命中摘要）
# ============================================================

def focus_summary(day_gan: str, year_gz: str, focus_list: List[str]) -> List[str]:
    """关注维度 → 流年十神命中点评（未命中 → 平稳之年文案）。"""
    present = year_ten_shen_set(day_gan, year_gz)
    out = []
    for f in focus_list:
        hit = [s for s in FOCUS_TEN_SHEN[f] if s in present]
        if hit:
            out.append(f"{f}：{'；'.join(_TEN_SHEN_NOTE[s] for s in hit)}。")
        else:
            out.append(f"{f}：本年度该维度相关十神不显，平稳之年，按部就班即可。")
    return out


# ============================================================
# 结果卡片
# ============================================================

def format_cycle_card(result, target_year: int, target_month: int,
                      focus_list: List[str]) -> str:
    """流月流年运势卡片（紧凑排版，控制每次工具调用注入的 token）。

    覆盖需求四要素：流年干支+十神（天干+地支藏干）、流月干支+十神、
    关注维度运势要点（focus）、吉凶月份提示；附所在大运/起运上下文
    （复用引擎 qiyun_desc/jiaoyun 问真口径，未改动）。
    """
    day_gan = result.bazi[2][0]
    year_gz = flow_year_gz(result, target_year)
    nayin = NAYIN.get(year_gz, "")
    gan_s = shishen_of(day_gan, year_gz[0])
    hidden_ss = [(g, shishen_of(day_gan, g)) for g in hidden_stems_of(year_gz[1])]
    step = dayun_step_for(result, target_year)

    lines = [
        f"【流月流年】日主：{result.day_master}｜四柱：{' '.join(result.bazi)}",
        f"{target_year}年流年：{year_gz}（{nayin}）｜流年十神：{gan_s}；"
        f"{year_gz[1]}藏{'、'.join(f'{g} {s}' for g, s in hidden_ss)}",
    ]
    if step is not None:
        qy = result.qiyun_desc or ""
        lines.append(f"所在大运：{step[1]}（{step[0]}岁起，十年一换）｜{qy}")
    elif result.qiyun_desc:
        lines.append(f"起运：{result.qiyun_desc}")

    if focus_list:
        lines.append("关注维度：" + " ".join(focus_summary(day_gan, year_gz, focus_list)))

    # 流月：单月 → 该月一行 + 全年吉凶提示；缺省 → 全年 12 月一览
    months_lines = [f"{m}月 {flow_month_gz(year_gz, m)}"
                    f"（{shishen_of(day_gan, flow_month_gz(year_gz, m)[0])}"
                    f"·{luck_word(luck_score(day_gan, flow_month_gz(year_gz, m)))}）"
                    for m in range(1, 13)]
    if target_month:
        mgz = flow_month_gz(year_gz, target_month)
        ms = shishen_of(day_gan, mgz[0])
        mz = shishen_of(day_gan, hidden_stems_of(mgz[1])[0])
        lines.append(f"{target_month}月单月：{mgz}（{ms}/{mz}，"
                     f"{luck_word(luck_score(day_gan, mgz))}）")
    else:
        lines.append(f"{target_year}年流月：{' '.join(months_lines)}")

    best, worst = best_worst_months(day_gan, year_gz)
    b_gz = flow_month_gz(year_gz, best)
    w_gz = flow_month_gz(year_gz, worst)
    lines.append(f"吉凶月提示：最吉 {best}月（{b_gz}，"
                 f"{luck_word(luck_score(day_gan, b_gz))}）；"
                 f"最需留意 {worst}月（{w_gz}，"
                 f"{luck_word(luck_score(day_gan, w_gz))}）")
    return "\n".join(lines)
