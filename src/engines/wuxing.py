"""五行能量引擎（L2-3）：五行统计 / 月令旺衰 / 十二长生 / 日主强弱。

数据资产：data/wuxing_tables.json（2026-08-16 落盘）。文件缺失/损坏时回退内嵌表
（口径与 JSON 一致，回退契约与 bazi.py 的 jieqi_qz 表相同：logger.warning 一行后继续）。

五行统计口径（与问真 getwxnl 的差异，重要）：
- 问真"五行能量"含藏干加权（天干 + 地支藏干中气/余气），getwxnl 为 VIP 接口拿不到；
- 本引擎采用简化口径：天干直接计 1 + 地支仅计本气（藏干主气）计 1
  —— 与 BaziResult.wuxing 字段完全一致（该字段为问真排盘页"五行"同款口径）。

表结构（以 data/wuxing_tables.json 为准，见 _load_tables 校验）：
- changsheng：第 0 行 = 12 状态名；第 1~10 行 = 甲、乙、丙、丁、戊、己、庚、辛、壬、癸
  （行序即天干序）各 12 支，阳干顺行 / 阴干逆行（长生→养）。例：甲长生在亥
  （亥子丑寅卯辰巳午未申酉戌）；乙长生在午（午巳辰卯寅丑子亥戌酉申未）；
  戊、己分别同丙、丁（阳土同阳火、阴土同阴火）。
- wuxing_wangshuai：第 0 行 = 列头（目标五行 [金木水火土]）；第 1~5 行 = 月令五行
  金月/木月/水月/火月/土月（行序即月令五行序 [金木水火土]），每行 5 列对应目标五行
  [金木水火土] 的 旺/相/休/囚/死。例：火月行 [死,休,囚,旺,相] → 火旺、土相、木休、金死、水囚。
"""
import json as _json
import logging
import os as _os

logger = logging.getLogger(__name__)

# 五行序（与 wuxing_tables.json 表头/行序一致：金木水火土）
WUXING_ORDER = ["金", "木", "水", "火", "土"]

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 天干五行
GAN_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
              "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}

# 地支本气（藏干主气）→ 天干；五行 = GAN_WUXING[本气]（与 bazi.WUXING_DZ 同口径）
ZHI_BENQI = {"子": "癸", "丑": "己", "寅": "甲", "卯": "乙", "辰": "戊",
             "巳": "丙", "午": "丁", "未": "己", "申": "庚", "酉": "辛",
             "戌": "戊", "亥": "壬"}
ZHI_WUXING = {z: GAN_WUXING[g] for z, g in ZHI_BENQI.items()}

# 月支 → 月令五行（辰戌丑未为土月）
MONTH_WUXING = {"寅": "木", "卯": "木", "巳": "火", "午": "火",
                "申": "金", "酉": "金", "亥": "水", "子": "水",
                "辰": "土", "戌": "土", "丑": "土", "未": "土"}

# 五行相生 / 相克循环（与 bazi.py 同源口径）
SHENG_CYCLE = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
KE_CYCLE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
SHENG_ME = {v: k for k, v in SHENG_CYCLE.items()}  # 生我（印）
KE_ME = {v: k for k, v in KE_CYCLE.items()}        # 克我（官杀）

# ── 十二长生内嵌回退表（与 data/wuxing_tables.json changsheng 同内容）──
_CHANGSHENG_NAMES = ["长生", "沐浴", "冠带", "临官", "帝旺", "衰", "病", "死", "墓", "绝", "胎", "养"]
_CHANGSHENG_BY_GAN = {
    "甲": ["亥", "子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌"],
    "乙": ["午", "巳", "辰", "卯", "寅", "丑", "子", "亥", "戌", "酉", "申", "未"],
    "丙": ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"],
    "丁": ["酉", "申", "未", "午", "巳", "辰", "卯", "寅", "丑", "子", "亥", "戌"],
    "戊": ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"],
    "己": ["酉", "申", "未", "午", "巳", "辰", "卯", "寅", "丑", "子", "亥", "戌"],
    "庚": ["巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑", "寅", "卯", "辰"],
    "辛": ["子", "亥", "戌", "酉", "申", "未", "午", "巳", "辰", "卯", "寅", "丑"],
    "壬": ["申", "酉", "戌", "亥", "子", "丑", "寅", "卯", "辰", "巳", "午", "未"],
    "癸": ["卯", "寅", "丑", "子", "亥", "戌", "酉", "申", "未", "午", "巳", "辰"],
}

# ── 旺相休囚死内嵌回退表（与 data/wuxing_tables.json wuxing_wangshuai 同内容）──
_WANGSHUAI_BY_MONTH = {
    "金": ["旺", "死", "相", "囚", "休"],
    "木": ["囚", "旺", "休", "相", "死"],
    "水": ["休", "相", "旺", "死", "囚"],
    "火": ["死", "休", "囚", "旺", "相"],
    "土": ["相", "囚", "死", "休", "旺"],
}

_TABLES = None


def _load_tables():
    """加载五行表（懒加载缓存）：(状态名[12], {天干→12支序}, {月令五行→5状态[金木水火土]}）。

    回退契约（不抛异常）：文件缺失 / JSON 损坏 / 结构不合法（行数、行序、列数不符）→
    logger.warning 一行后回退内嵌表（内容与 JSON 一致，测试对 JSON 与引擎双端校验）。
    """
    global _TABLES
    if _TABLES is not None:
        return _TABLES
    _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "..", "..", "data", "wuxing_tables.json")
    try:
        with open(_path, encoding="utf-8") as _f:
            raw = _json.load(_f)
        cs, ws = raw["changsheng"], raw["wuxing_wangshuai"]
        # changsheng：11 行（行0 状态名 + 行1~10 甲乙丙丁戊己庚辛壬癸），每行 12 支
        assert len(cs) == 11 and len(cs[0]) == 12, "changsheng 表结构"
        assert all(len(r) == 12 for r in cs[1:]), "changsheng 支序列行宽"
        assert all(r[0] in DIZHI for r in cs[1:]), "changsheng 行序（首列为支）"
        # wuxing_wangshuai：6 行（行0 列头 [金木水火土] + 行1~5 金/木/水/火/土月），每行 5 列
        assert len(ws) == 6 and ws[0] == WUXING_ORDER, "wangshuai 表头"
        assert all(len(r) == 5 for r in ws[1:]), "wangshuai 行宽"
        cs_by_gan = {TIANGAN[i]: cs[i + 1] for i in range(10)}
        ws_by_month = {WUXING_ORDER[i]: ws[i + 1] for i in range(5)}
        _TABLES = (cs[0], cs_by_gan, ws_by_month)
    except Exception as _e:
        # 表文件缺失/损坏：不静默，告警一行后回退内嵌表（与 bazi.py jieqi_qz 回退契约一致）
        logger.warning("wuxing_tables.json 加载失败，回退内嵌表: %s", _e)
        _TABLES = (_CHANGSHENG_NAMES, _CHANGSHENG_BY_GAN, _WANGSHUAI_BY_MONTH)
    return _TABLES


def wuxing_counts(gan_zhi_list) -> dict:
    """五行统计（简化口径：天干直接计 + 地支本气[藏干主气]计，各计 1）。

    ⚠️ 口径注释：问真 getwxnl（五行能量）为 VIP 接口拿不到，本引擎未含藏干
    中气/余气加权；此口径与 BaziResult.wuxing 完全一致（问真排盘页"五行"同款）。
    返回 {金木水火土: 数量}，五键恒在（缺失五行 = 0）。
    例：wuxing_counts(["己卯","己巳","乙丑","壬午"]) → {"金":0,"木":2,"水":1,"火":2,"土":3}
    """
    counts = {w: 0 for w in WUXING_ORDER}
    for p in gan_zhi_list:
        counts[GAN_WUXING[p[0]]] += 1
        counts[ZHI_WUXING[p[1]]] += 1
    return counts


def month_wangshuai(month_zhi: str, wuxing: str) -> str:
    """月令旺相休囚死（查 wuxing_wangshuai：行 = 月令五行[金木水火土]，列 = 目标五行[金木水火土]）。

    例：month_wangshuai("巳", "木") → "休"（巳月火旺，木休）；month_wangshuai("巳", "火") → "旺"。
    非法输入抛 ValueError（调用方输入来自排盘内部，属编程错误）。
    """
    month_wx = MONTH_WUXING.get(month_zhi)
    if month_wx is None:
        raise ValueError("非法月支: %r" % month_zhi)
    if wuxing not in WUXING_ORDER:
        raise ValueError("非法五行: %r" % wuxing)
    _, _, ws_by_month = _load_tables()
    return ws_by_month[month_wx][WUXING_ORDER.index(wuxing)]


def changsheng_state(day_gan: str, zhi: str) -> str:
    """十二长生状态（查 changsheng 表，按日干定位行序列后查支位）。

    表行序 = 天干序 甲乙丙丁戊己庚辛壬癸（阳干顺行 / 阴干逆行，戊己同丙丁）。
    未知天干 / 未知地支 → "?"（与 bazi_formatter.get_changsheng 行为一致）。
    例：changsheng_state("乙", "丑") → "衰"（乙长生在午逆行）；changsheng_state("乙", "卯") → "临官"。
    """
    names, cs_by_gan, _ = _load_tables()
    seq = cs_by_gan.get(day_gan)
    if not seq or zhi not in seq:
        return "?"
    return names[seq.index(zhi)]


# 得令分：日主五行在月令的旺相休囚死
_LING_SCORE = {"旺": 2, "相": 1, "休": 0, "囚": -1, "死": -2}
# 得地分：日干十二长生强根状态（沐浴/衰/病/死/墓/绝/胎/养 不计根——简化口径）
_DI_STRONG = ("长生", "冠带", "临官", "帝旺")


def _shi_score(diff: float) -> int:
    """得势分（扶 − 克泄耗 差值 → 档位）：
    ≥ +2 → +2；( +0.5, +2 ) → +1；[ -0.5, +0.5 ] → 0；( -2, -0.5 ) → -1；≤ -2 → -2。"""
    if diff >= 2:
        return 2
    if diff > 0.5:
        return 1
    if diff >= -0.5:
        return 0
    if diff > -2:
        return -1
    return -2


def day_master_strength(bazi: list, counts: dict) -> str:
    """日主强弱五档判定（得令 + 得地 + 得势 加权求和，规则注释）：

    1) 得令分：日主五行在月令的旺相休囚死 → 旺+2 / 相+1 / 休0 / 囚-1 / 死-2
    2) 得地分：四地支中日干十二长生为 长生/冠带/临官/帝旺 者各 +1
       （沐浴/衰/病/死/墓/绝/胎/养 为弱气不计根——简化口径）
    3) 得势分：扶 = 比劫(同类)×1 + 印(生我)×0.5；克泄耗 = 官杀(克我)×1 + 食伤(我生)×1 + 财(我克)×1；
       差值按 _shi_score 归为 ±2/±1/0
    总分五档：≥5 旺；≥3 偏旺；≥1.5 中和；≥-1.5 偏弱；< -1.5 弱。

    例：己卯 己巳 乙丑 壬午（乙木日主，巳月木"休"不得令；卯临官+午长生两强根
    得地；双己财透+巳午火泄，扶2.5 vs 克泄耗5）→ 得令0 + 得地2 + 得势-2 = 0 → 偏弱。
    """
    if len(bazi) < 4:
        raise ValueError("bazi 需为四柱干支列表，如 ['己卯','己巳','乙丑','壬午']")
    day_gan, month_zhi = bazi[2][0], bazi[1][1]
    day_wx = GAN_WUXING[day_gan]

    # 得令：日主五行在月令的旺相休囚死
    ling = _LING_SCORE[month_wangshuai(month_zhi, day_wx)]

    # 得地：四地支中日干强长生状态的个数
    di = sum(1 for p in bazi if changsheng_state(day_gan, p[1]) in _DI_STRONG)

    # 得势：扶（比劫+印） vs 克泄耗（官杀+食伤+财）
    fu = counts[day_wx] + 0.5 * counts[SHENG_ME[day_wx]]
    xie = counts[KE_ME[day_wx]] + counts[SHENG_CYCLE[day_wx]] + counts[KE_CYCLE[day_wx]]
    shi = _shi_score(fu - xie)

    total = ling + di + shi
    if total >= 5:
        return "旺"
    if total >= 3:
        return "偏旺"
    if total >= 1.5:
        return "中和"
    if total >= -1.5:
        return "偏弱"
    return "弱"
