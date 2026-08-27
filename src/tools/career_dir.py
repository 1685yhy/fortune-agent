"""择业/方位匹配工具规则层（批次 2 E4，cap_id: career_dir）：
参数解析 + 喜用神五行 → 行业五行映射 + 吉利方位 + 禁忌行业 + 结果卡片。

规则依据（知识性内容全部标注来源，不无据编造）：
- 喜用神：复用既有排盘引擎 BaziEngine.calculate 的 yongshen 字段——形态
  "X为用神（喜X、Y）"（引擎 _calc_yongshen 调候优先 + 扶抑辅助口径），解析
  函数 parse_yongshen 与 src/engines/ming.py 同口径（"X为用神" → 用神五行、
  "喜（...）" → 喜用五行列表）。E2 起名工具已按此口径使用，本工具同口径。
- 行业五行映射表：命理通识行业五行归类（通行做法，多源一致，见下）：
  · 金：金融财务（银行/证券/保险/期货/投资）、金属制造（钢铁/冶金/五金/
    机械/汽车）、珠宝钟表、司法军警、科技硬件（电子元件/计算机硬件/通讯）、
    精密医疗（外科/牙医）
  · 木：文化教育（出版/印刷/图书/教师）、木竹家具、纺织服装、农林园艺
    （林业/种植/花卉/茶叶/中药材）、中医医疗
  · 水：运输物流（交通运输/物流/航运/贸易）、水产饮品（水产/渔业/冷饮/酒水）、
    旅游出行、清洁家政、水务水利
  · 火：餐饮食品、能源化工（电力/石油/化工/燃料）、电子科技（电脑/电器/电子/
    通信/互联网）、光电演艺（照明/摄影/影视/演艺/娱乐）、美容化妆
  · 土：地产建筑（房地产/建筑/建材/装修）、农业养殖、矿业石材、仓储物业
  来源标注：白如雪《八字的五行與行業的關係》（bairuxue.hk/bazi-ji-8.htm）、
  龙隐说易《命理对应五行行业与高考专业》（longyinshuoyi.com/archiver/tid-1323.html）、
  百家号《五行与所属行业分类》（baijiahao.baidu.com/s?id=1795396618548705449）、
  百家号《五行对应的不同行业》（baijiahao.baidu.com/s?id=1811327326449249456）、
  百度文库《行业的五行属性》（wenku.baidu.com/view/3389f988e63a580216fc700abb68a98271feaccf.html）、
  Destiny Garden《八字命理的职业五行如何分类》（destinygarden.com/big5/mdiary10.htm）。
  歧义行业（美容美发、电子 vs 电子元件等）按通行分类取一；词表注册先到先得，
  表序（金木水火土，与引擎 WUXING_ORDER 同口径）即优先级，测试锁定。
- 方位：五行方位配属（后天八卦方位，命理通识——《周易》后天八卦：震东属木、
  离南属火、兑西属金、坎北属水、中宫属土；《尚书·洪范》五行五方配属）：
  木→东方、火→南方、土→中央（本地）、金→西方、水→北方。
- 禁忌行业：命局忌神五行对应的行业。忌神按日主强弱与扶抑理论判定
  （《子平真诠》扶抑法 + 《滴天髓》太过不及论）：日主强弱取引擎
  wuxing_energy.strength（得令+得地+得势五档：旺/偏旺/中和/偏弱/弱）——
  · 旺/偏旺（身旺）：忌生扶日主之五行（比劫、印星）
  · 弱/偏弱（身弱）：忌克泄耗日主之五行（官杀、食伤、财）
  · 中和：忌命局最旺五行（太过为忌）
  忌神与喜用重叠时剔除（避免同一五行既推荐又禁忌的自相矛盾）。

调用方（handler._tool_career_dir）：出生文本 → parse_career_params 解析 →
_extract_bazi_info 解析 → 既有 BaziEngine.calculate → 本模块出卡片。
"""
import re
from typing import Dict, List, Optional, Tuple

from src.engines.bazi import WUXING_ORDER, WUXING_TG
from src.engines.ming import parse_yongshen

# ============================================================
# 行业五行映射表（命理通识，来源见模块 docstring；表序即词→五行优先级）
# 类别结构：(类别名, 代表行业词顿号串)——代表词同时是行业关键词匹配词
# ============================================================
INDUSTRY_WUXING: Dict[str, List[Tuple[str, str]]] = {
    "金": [
        ("金融财务", "银行、证券、保险、期货、投资、财会、会计、税务、金融"),
        ("金属制造", "钢铁、冶金、五金、机械制造、模具、汽车、航空航天、金属制品"),
        ("珠宝钟表", "金银珠宝、钟表、刀具、金属工艺品"),
        ("司法军警", "法官、律师、军警、公务员、公检法、鉴定"),
        ("科技硬件", "电子元件、计算机硬件、通讯、通讯器材、半导体"),
        ("精密医疗", "外科医生、牙医、精密医疗器械"),
    ],
    "木": [
        ("文化教育", "教师、出版、印刷、图书、文具、新闻、教育、文化"),
        ("木竹家具", "木材、家具、竹木制品、木雕、装修木工"),
        ("纺织服装", "服装、纺织、布匹、鞋帽"),
        ("农林园艺", "林业、种植、园艺、花卉、茶叶、中药材"),
        ("中医医疗", "中医、中药、理疗养生"),
    ],
    "水": [
        ("运输物流", "交通运输、交通、物流、航运、港口、快递、贸易、进出口"),
        ("水产饮品", "水产、渔业、冷饮、酒水、饮料、纯净水"),
        ("旅游出行", "旅游、导游、酒店、航空"),
        ("清洁家政", "清洁、洗涤、家政、洗车"),
        ("水务水利", "水利、给排水、水务"),
    ],
    "火": [
        ("餐饮食品", "餐饮、厨师、食品、外卖、烧烤"),
        ("能源化工", "电力、能源、石油、天然气、化工、燃料、烟花爆竹"),
        ("电子科技", "电脑、电器、电子、通信、互联网、电子商务、电商、软件、网络、程序员"),
        ("光电演艺", "光学、照明、灯饰、摄影、影视、演艺、娱乐、直播"),
        ("美容化妆", "美容、美发、美甲、理发、化妆品"),
    ],
    "土": [
        ("地产建筑", "房地产、房产、建筑、土木工程、建材、水泥、砖瓦、装修"),
        ("农业养殖", "农业、农作物、畜牧业、养殖、粮食"),
        ("矿业石材", "矿业、煤炭、石材、陶瓷、古董"),
        ("仓储物业", "仓储、仓库、物业、土地买卖、殡葬"),
    ],
}

# 行业词 → 五行 词典（类别名 + 代表词全量注册；表序先到先得，词序表序即优先级）
INDUSTRY_WORDS: Dict[str, str] = {}
for _wx in WUXING_ORDER:
    for _label, _examples in INDUSTRY_WUXING[_wx]:
        for _w in (_label, *re.split(r"[、，,]", _examples)):
            _w = _w.strip()
            if _w and _w not in INDUSTRY_WORDS:
                INDUSTRY_WORDS[_w] = _wx
# 文本标签兜底匹配用：词长降序（同位置先取长词，防 "互联网" 被 "网络" 抢先）
_WORDS_BY_LEN = sorted(INDUSTRY_WORDS, key=len, reverse=True)

# 五行方位配属（后天八卦方位，命理通识——见模块 docstring）
ELEMENT_DIRECTION = {
    "木": "东方", "火": "南方", "土": "中央（本地）", "金": "西方", "水": "北方",
}

# 结构化键形态：birth:/industry:（半/全角冒号与等号均收）
_CAREER_KEY_RE = re.compile(r'^(birth|industry)\s*[:：=＝]\s*(.*)$', re.I)
_BIRTH_YEAR_RE = re.compile(r'[12]\d{3}\s*年')
# 文本标签兜底：行业词剔除后，出生文本尾部可能的行业语境残留（做/想/考虑…）
_TRAILING_VERB_RE = re.compile(
    r'(?:现在|目前|最近|想|打算|考虑|准备|正在|从事|做|干|搞|工作|行业)+$')


# ============================================================
# 参数解析（结构化键优先，文本标签兜底——与 hehun/naming/fortune_cycle 同型）
# ============================================================

def parse_career_params(text) -> Optional[dict]:
    """把工具参数文本解析为 {birth, industry?}；解析不出 → None。

    支持三种形态：
    1. 结构化多键（JSON 工单 serialize 产物，主路径）：
       "birth: 1990年5月20日 午时 北京 男\nindustry: 金融"
       （键前缀识别，冒号半/全角、等号均可；键序无关；industry 可省）
    2. 文本标签兜底（legacy <tool_call>）：
       "我考虑做金融，1990年5月20日 午时 北京 男" / "1990年5月20日 午时 北京 男 想做金融"
       —— 行业关键词（INDUSTRY_WORDS 全词表）取最早位置、同位置最长者，
       从原串剔除（词序变化不再影响匹配）；剩余文本从首个出生年起截取出生信息
       （尾部行业语境残留 做/想/考虑/工作… 一并剥净）
    3. 拆不出出生信息 → None（调用方转 needs_info 追问）
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    info: dict = {}

    # 形态 1：结构化键（LLM 走 schema 必填键 birth，industry 可选）
    for line in raw.splitlines():
        m = _CAREER_KEY_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip()
        if val:
            info[key] = val
    if info:
        return info

    # 形态 2：文本标签
    hits = [(pos, word) for word in _WORDS_BY_LEN
            if (pos := raw.find(word)) != -1]
    rest = raw
    if hits:
        pos, word = min(hits, key=lambda t: (t[0], -len(t[1])))
        info["industry"] = word
        rest = raw[:pos] + raw[pos + len(word):]
    m = _BIRTH_YEAR_RE.search(rest)
    if not m:
        return None
    birth = rest[m.start():]
    birth = _TRAILING_VERB_RE.sub("", birth).strip(" ，,。、")
    if not birth:
        return None
    info["birth"] = birth
    return info


def industry_element(word) -> Optional[str]:
    """行业词 → 五行：剥后缀（行业/公司/工作/领域/类）后查 INDUSTRY_WORDS。

    未收录时两级子串兜底：输入含登记词 → 取最长登记词（如 电子商务 含 电商 时
    按登记词归位）；登记词含输入 → 取最短登记词（如 房产 ⊂ 房地产 → 土）。
    均不中 → None（调用方文案提示未收录，不臆断五行）。
    """
    w = str(word or "").strip()
    if not w:
        return None
    for suf in ("行业", "公司", "工作", "领域", "类"):
        if w.endswith(suf) and len(w) > len(suf):
            w = w[:-len(suf)]
            break
    el = INDUSTRY_WORDS.get(w)
    if el:
        return el
    sub = [ew for ew in INDUSTRY_WORDS if ew in w]
    if sub:
        return INDUSTRY_WORDS[max(sub, key=len)]
    super_ = [ew for ew in INDUSTRY_WORDS if w in ew]
    if super_:
        return INDUSTRY_WORDS[min(super_, key=len)]
    return None


# ============================================================
# 喜用神 / 忌神（引擎口径 + 扶抑理论）
# ============================================================

def helpful_elements(result) -> List[str]:
    """喜用五行：引擎 yongshen 串 parse_yongshen（ming.py 同口径，
    "X为用神（喜X、Y）" → 喜用五行列表）。"""
    _use, helpful = parse_yongshen(result.yongshen)
    return helpful


def day_master_strength_of(result) -> str:
    """日主强弱五档（引擎 wuxing_energy.strength：得令+得地+得势加权口径）；
    字段缺失 → "中和" 兜底（按最旺五行判忌，不至于空结果）。"""
    return (result.wuxing_energy or {}).get("strength", "") or "中和"


def forbidden_elements(result) -> List[str]:
    """忌神五行（扶抑理论 + 引擎日主强弱五档，依据见模块 docstring）。

    旺/偏旺 → 忌生扶（比劫 day_wx、印星生我者）；弱/偏弱 → 忌克泄耗
    （官杀克我者、食伤我生者、财我克者）；中和 → 忌命局最旺五行（太过为忌）。
    与喜用重叠者剔除（同一五行不既荐又忌）。确定性：同排盘同输出。
    """
    strength = day_master_strength_of(result)
    day_wx = WUXING_TG.get(result.bazi[2][0], "")
    sheng_cycle = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}  # 我生
    ke_cycle = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}    # 我克
    ke_me = {v: k for k, v in ke_cycle.items()}      # 克我（官杀）
    sheng_me = {v: k for k, v in sheng_cycle.items()}  # 生我（印）
    if strength in ("旺", "偏旺"):
        forbidden = [day_wx, sheng_me.get(day_wx, "")]
    elif strength in ("弱", "偏弱"):
        forbidden = [ke_me.get(day_wx, ""), sheng_cycle.get(day_wx, ""),
                     ke_cycle.get(day_wx, "")]
    else:  # 中和：忌命局最旺（太过为忌）
        counts = result.wuxing or {}
        mx = max((counts.get(w, 0) for w in WUXING_ORDER), default=0)
        forbidden = [w for w in WUXING_ORDER if counts.get(w, 0) == mx]
    helpful = helpful_elements(result)
    return [w for w in forbidden if w and w not in helpful]


def forbidden_reason(strength: str) -> str:
    """忌神判定依据一句（扶抑理论文案，供卡片禁忌行）。"""
    if strength in ("旺", "偏旺"):
        return "日主偏旺，忌生扶之五行（比劫、印星）"
    if strength in ("弱", "偏弱"):
        return "日主偏弱，忌克泄耗之五行（官杀、食伤、财）"
    return "命局中和，忌过旺之五行（太过为忌）"


# ============================================================
# 结果卡片
# ============================================================

def _industry_line(wx: str) -> str:
    """单五行行业行：类别（代表词 4 个内）顿号连接。"""
    parts = []
    for label, examples in INDUSTRY_WUXING.get(wx, []):
        words = [x.strip() for x in re.split(r"[、，,]", examples) if x.strip()]
        parts.append(f"{label}（{'/'.join(words[:4])}）")
    return "、".join(parts)


def format_career_card(result, industry: Optional[str] = None) -> str:
    """择业/方位卡片（紧凑排版，控制每次工具调用注入的 token）。

    覆盖需求四要素：喜用神结论（引擎 yongshen 原文 + 喜用五行）、吉利方位
    （喜用五行方位）、适合行业（喜用五行对应行业清单）、禁忌行业（忌神五行
    对应行业 + 判定依据）；考虑行业（可选）给命中判定（喜用/禁忌/中性/未收录）。
    """
    helpful = helpful_elements(result)
    forbidden = forbidden_elements(result)
    strength = day_master_strength_of(result)
    lines = [
        f"【择业方位】日主：{result.day_master}｜四柱：{' '.join(result.bazi)}｜"
        f"日主强弱：{strength}",
        f"喜用神结论：{result.yongshen}",
    ]
    if helpful:
        lines.append("喜用五行：" + "、".join(helpful) + "｜吉利方位：" + "、".join(
            f"{ELEMENT_DIRECTION.get(w, '')}（{w}）"
            for w in helpful if w in ELEMENT_DIRECTION))
        for w in helpful:
            lines.append(f"适合行业（{w}·吉）：{_industry_line(w)}")
    if forbidden:
        lines.append(f"禁忌行业（{forbidden_reason(strength)}）：")
        for w in forbidden:
            lines.append(f"  忌{w}：{'、'.join(label for label, _ in INDUSTRY_WUXING[w])}")
    else:
        lines.append("禁忌行业：无明显禁忌五行（喜用五行外皆中性，按兴趣选择即可）")
    if industry:
        el = industry_element(industry)
        if el is None:
            lines.append(f"考虑行业「{industry}」暂无法归入行业五行表，"
                         "以下按喜用神五行全量推荐。")
        elif el in helpful:
            lines.append(f"考虑行业「{industry}」属{el}：命中喜用五行，非常适合。")
        elif el in forbidden:
            lines.append(f"考虑行业「{industry}」属{el}：命中禁忌五行，建议避开。")
        else:
            lines.append(f"考虑行业「{industry}」属{el}：非喜用亦非禁忌，可行但非最优。")
    return "\n".join(lines)
