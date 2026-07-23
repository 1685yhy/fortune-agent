"""八字学堂 — beginner-friendly fortune-telling education content.

Uses the existing RAG knowledge base to generate structured tutorials
for beginners. Covers basic concepts progressively.

Supports personalized lessons that incorporate the user's own bazi data
into examples and explanations.
"""

import re

# Structured curriculum — topics with search keywords for RAG
CURRICULUM = {
    "入门": {
        "什么是八字": ["八字 基础 入门", "四柱 天干地支"],
        "天干地支": ["十天干", "十二地支", "天干地支 含义"],
        "五行生克": ["五行 相生相克", "金木水火土"],
        "阴阳学说": ["阴阳", "阴阳五行"],
    },
    "进阶": {
        "十神分析": ["十神 详解", "正官 七杀 正印 偏印"],
        "格局判断": ["八字格局", "正格 变格 特殊格局"],
        "用神取法": ["用神 忌神", "扶抑 调候 通关"],
        "大运流年": ["大运 流年", "起运 排大运"],
    },
    "专题": {
        "财运分析": ["八字 财运", "财星 财库"],
        "感情婚姻": ["八字 婚姻", "配偶星 夫妻宫"],
        "事业官运": ["八字 事业", "官星 印星"],
        "健康养生": ["八字 健康", "五行 健康"],
    },
}

# Topic-specific personalization templates
# Each template receives bazi_data dict and returns a personalized paragraph
_PERSONALIZATION_TEMPLATES = {
    "什么是八字": (
        "你的日主是**{day_master}**。在八字中，日主代表你自己，"
        "是整个命盘的核心。你的四柱分别是：{bazi_str}。\n\n"
        "每一柱都代表你人生中的一个重要领域："
        "年柱代表祖上根基，月柱代表父母兄弟和成长环境，"
        "日柱代表你自己和配偶，时柱代表子女和晚年。"
    ),
    "天干地支": (
        "你的八字由以下天干地支组成：**{bazi_str}**。\n\n"
        "来看你的天干："
        "{tiangan_str}。\n"
        "每个天干都带有独特的五行属性和性格特征。\n\n"
        "你的地支：{dizhi_str}。\n"
        "地支中藏着不同的藏干，影响着你的运势走向。"
    ),
    "五行生克": (
        "你的日主是**{day_master}**，属{day_master_wuxing}。\n\n"
        "从你的八字来看，{wuxing_analysis}\n\n"
        "五行的平衡关系对你的性格和运势有重要影响。"
        "了解你的五行强弱，可以帮助你更好地把握人生方向和调整策略。"
    ),
    "阴阳学说": (
        "你的日主**{day_master}**为{'阳' if day_master and '甲丙戊庚壬'.find(day_master[0]) >= 0 else '阴'}干。\n\n"
        "在八字中，阴阳平衡至关重要。你的四柱阴阳分布是：{yinyang_analysis}\n\n"
        "阴阳的调和程度会影响你的性格倾向——"
        "{'你的阳性能量较强，性格可能偏向外向、主动、果断。' if day_master and '甲丙戊庚壬'.find(day_master[0]) >= 0 else '你的阴性能量较强，性格可能偏向内敛、细腻、含蓄。'}"
    ),
    "十神分析": (
        "你的日主是**{day_master}**，以日主为中心，其他天干地支与日主的关系就是十神。\n\n"
        "{shishen_analysis}\n\n"
        "十神的组合决定了你的性格特质、天赋才能以及人生中遇到的贵人、挑战等。"
    ),
    "格局判断": (
        "你的八字格局是**{geju}**（如果已排盘）。\n\n"
        "格局是八字命理中最高层次的判断，它决定了你命局的整体基调。\n\n"
        "{geju_analysis}\n\n"
        "不同的格局有不同的喜忌和运势特征，"
        "了解自己的格局有助于把握人生的关键节点。"
    ),
    "用神取法": (
        "你的八字用神是**{yongshen}**（如果已分析）。\n\n"
        "用神是八字中最有利于你的五行元素，是调整运势的关键。\n\n"
        "{yongshen_analysis}\n\n"
        "在日常生活中，可以通过颜色、方位、职业选择等方式来补益用神，"
        "从而达到趋吉避凶的效果。"
    ),
    "大运流年": (
        "你的大运走势对你的人生有至关重要的影响。\n"
        "大运每十年一换，不同的大运带来不同的机遇和挑战。\n\n"
        "{dayun_analysis}\n\n"
        "结合你的日主{day_master}，大运的变化会对你的人生产生深远影响。"
    ),
    "财运分析": (
        "以你的日主**{day_master}**来看，\n\n"
        "{wealth_analysis}\n\n"
        "了解你命中的财星特征，可以帮助你在投资、事业发展中做出更明智的选择。"
    ),
    "感情婚姻": (
        "以你的日主**{day_master}**来看，\n\n"
        "{love_analysis}\n\n"
        "感情婚姻是人生重要组成部分，了解自己的感情特点，"
        "有助于经营更和谐的两性关系。"
    ),
    "事业官运": (
        "以你的日主**{day_master}**来看，\n\n"
        "{career_analysis}\n\n"
        "事业运势和你的命局特点密切相关，"
        "选择适合自己五行属性的行业方向，往往能事半功倍。"
    ),
    "健康养生": (
        "以你的日主**{day_master}**来看，\n\n"
        "{health_analysis}\n\n"
        "根据你的五行特点来调理身体，"
        "可以达到更好的养生效果。"
    ),
}

# Fallback generic personalization when no topic-specific template exists
_GENERIC_PERSONALIZATION = (
    "你的日主是**{day_master}**，八字为：{bazi_str}。\n\n"
    "以上知识适用于你的命盘分析。你可以结合自己的八字特点来理解这些概念，"
    "看看哪些在你的命局中特别突出或需要注意。\n\n"
    "如果想深入了解，可以发送具体问题，我会结合你的八字给你更针对性的解答。"
)


def _get_topic_section(topic: str):
    """Find the section and keywords for a given topic.

    Returns:
        (section_name, keywords) or (None, None) if not found.
    """
    for sec, topics in CURRICULUM.items():
        if topic in topics:
            return sec, topics[topic]
    # Fuzzy match
    for sec, topics in CURRICULUM.items():
        for t, kws in topics.items():
            if topic in t or t in topic:
                return sec, kws
    return None, None


def get_lesson(topic: str, retriever=None) -> str:
    """Generate a beginner-friendly lesson on a given topic using RAG.

    Args:
        topic: Topic name from CURRICULUM keys
        retriever: RAG retriever for classical text lookup

    Returns:
        Formatted markdown lesson text
    """
    section, keywords = _get_topic_section(topic)

    if not keywords:
        return f"📚 抱歉，还未收录「{topic}」的教程。试试：{', '.join(list(CURRICULUM.keys())[:4])}"

    # RAG search for classical references
    refs = []
    if retriever:
        for kw in keywords[:2]:
            results = retriever.search(kw, top_k=3)
            refs.extend(results[:2])

    # Build lesson
    lines = [f"📚 八字学堂 · {section or '基础'}篇", f"", f"## {topic}", ""]

    if topic == "什么是八字":
        lines.extend([
            "**八字**又称四柱命理学，是中国传统命理学的核心。",
            "",
            "一个人的出生年、月、日、时，分别对应**四柱**：",
            "- **年柱**：出生年份的天干地支（以立春为界）",
            "- **月柱**：出生月份的天干地支（以节气为界）",
            "- **日柱**：出生日期的天干地支",
            "- **时柱**：出生时辰的天干地支",
            "",
            "每个柱由两个字组成（天干+地支），四柱共**八个字**，故称「八字」。",
            "",
            "💡 **试试**：发送「帮我看看八字 1990年5月20日 下午3点 北京 男」看看你的八字！",
        ])
    elif topic == "天干地支":
        lines.extend([
            "**十天干**：甲、乙、丙、丁、戊、己、庚、辛、壬、癸",
            "**十二地支**：子、丑、寅、卯、辰、巳、午、未、申、酉、戌、亥",
            "",
            "天干和地支组合成 60 个不同的「干支」，称为**六十甲子**。",
            "例如：甲子、乙丑、丙寅……癸亥。",
            "",
            "你的八字就是由 4 组这样的干支组成的！",
        ])
    elif topic == "五行生克":
        lines.extend([
            "**五行**：金、木、水、火、土",
            "",
            "**相生**（一个促进另一个）：",
            "木 → 火 → 土 → 金 → 水 → 木",
            "",
            "**相克**（一个制约另一个）：",
            "木 → 土 → 水 → 火 → 金 → 木",
            "",
            "每个天干和地支都有五行属性。分析八字时，",
            "五行平衡是判断命格好坏的重要依据。",
        ])
    else:
        lines.append("（以下内容由AI基于古籍知识库生成）")
        lines.append("")

    # Add classical references
    if refs:
        lines.append("---")
        lines.append("📖 **古籍参考**：")
        for r in refs[:2]:
            text = r.content if hasattr(r, 'content') else str(r)[:200]
            lines.append(f"> {text[:200]}")

    lines.append("")
    lines.append(f"💡 回复「学堂」查看更多教程 | 回复「{topic} 详解」深入探索")

    return "\n".join(lines)


def personalized_lesson(topic: str, bazi_data: dict = None, retriever=None) -> str:
    """Like get_lesson but personalizes examples using user's bazi.

    After getting the standard lesson content, a personalized section
    is appended with examples specific to the user's bazi.

    Args:
        topic: Topic name from CURRICULUM keys
        bazi_data: dict with keys like "day_master", "bazi", "geju", "yongshen"
        retriever: RAG retriever for classical text lookup

    Returns:
        Markdown lesson with personalized examples appended.
    """
    standardized = get_lesson(topic, retriever)
    if not bazi_data:
        return standardized

    day_master = bazi_data.get("day_master", "")
    bazi = bazi_data.get("bazi", [])
    geju = bazi_data.get("geju", "")
    yongshen = bazi_data.get("yongshen", "")

    if not day_master and not bazi:
        return standardized

    # Build bazi_str for display
    if isinstance(bazi, (list, tuple)):
        bazi_str = " ".join(str(p) for p in bazi[:4]) if len(bazi) >= 4 else " ".join(str(p) for p in bazi)
    elif isinstance(bazi, str):
        bazi_str = bazi
    else:
        bazi_str = ""

    # Prepare common analysis snippets
    section, _ = _get_topic_section(topic)

    # Build the personalized paragraph
    personalized = _build_personalized_paragraph(
        topic=topic,
        day_master=day_master,
        bazi_str=bazi_str,
        bazi=bazi,
        geju=geju,
        yongshen=yongshen,
    )

    # Append to standard lesson
    lines = [standardized, "", "---", "🌟 **以你的八字为例**：", "", personalized, ""]
    lines.append(f"💡 回复「学堂」查看更多教程 | 发送具体问题继续深入探索")

    return "\n".join(lines)


def _build_personalized_paragraph(topic, day_master, bazi_str, bazi, geju, yongshen):
    """Build a personalized paragraph for a given topic using bazi data."""

    # Determine day master's wuxing
    _WUXING_MAP = {
        "甲": "木", "乙": "木",
        "丙": "火", "丁": "火",
        "戊": "土", "己": "土",
        "庚": "金", "辛": "金",
        "壬": "水", "癸": "水",
    }
    dm_char = day_master[0] if day_master else ""
    day_master_wuxing = _WUXING_MAP.get(dm_char, "")

    # Get tiangan and dizhi
    tiangan_list = []
    dizhi_list = []
    if isinstance(bazi, (list, tuple)):
        for pillar in bazi[:4]:
            if len(pillar) >= 2:
                tiangan_list.append(pillar[0])
                dizhi_list.append(pillar[1])

    tiangan_str = "、".join(tiangan_list) if tiangan_list else "（待排盘）"
    dizhi_str = "、".join(dizhi_list) if dizhi_list else "（待排盘）"

    context = {
        "day_master": day_master or "?",
        "bazi_str": bazi_str or "（待排盘）",
        "tiangan_str": tiangan_str,
        "dizhi_str": dizhi_str,
        "day_master_wuxing": day_master_wuxing or "?",
        "geju": geju or "（待判断）",
        "yongshen": yongshen or "（待分析）",
        "wuxing_analysis": _build_wuxing_analysis(tiangan_list, dizhi_list, day_master_wuxing),
        "yinyang_analysis": _build_yinyang_analysis(tiangan_list, dizhi_list),
        "shishen_analysis": _build_shishen_analysis(day_master, bazi),
        "geju_analysis": _build_geju_analysis(geju, day_master),
        "yongshen_analysis": _build_yongshen_analysis(yongshen, day_master),
        "dayun_analysis": _build_dayun_analysis(day_master, geju, yongshen),
        "wealth_analysis": _build_wealth_analysis(day_master, bazi),
        "love_analysis": _build_love_analysis(day_master),
        "career_analysis": _build_career_analysis(day_master),
        "health_analysis": _build_health_analysis(day_master_wuxing),
    }

    # Look for topic-specific template
    if topic in _PERSONALIZATION_TEMPLATES:
        try:
            return _PERSONALIZATION_TEMPLATES[topic].format(**context)
        except (KeyError, ValueError):
            pass

    # Fallback to generic
    try:
        return _GENERIC_PERSONALIZATION.format(**context)
    except (KeyError, ValueError):
        return f"你的日主是{day_master}，八字为{bazi_str}。以上知识可结合你的命盘来理解。"


def _build_wuxing_analysis(tiangan_list, dizhi_list, day_master_wuxing):
    """Build a wuxing analysis paragraph."""
    if not tiangan_list or not day_master_wuxing:
        return "你的八字日主五行属性可用于分析五行平衡。"
    # Count wuxing occurrences in tiangan
    _T_WUXING = {"甲乙": "木", "丙丁": "火", "戊己": "土", "庚辛": "金", "壬癸": "水"}
    wuxing_count = {}
    for tg in tiangan_list:
        for chars, wx in _T_WUXING.items():
            if tg in chars:
                wuxing_count[wx] = wuxing_count.get(wx, 0) + 1
                break
    for dz in dizhi_list:
        for chars, wx in _T_WUXING.items():
            if dz in chars:
                wuxing_count[wx] = wuxing_count.get(wx, 0) + 1
                break
    if not wuxing_count:
        return "你的八字中五行分布可以通过排盘进一步了解。"
    strongest = max(wuxing_count, key=wuxing_count.get)
    weakest = min(wuxing_count, key=wuxing_count.get)
    return (
        f"你的日主为{day_master_wuxing}，八字中**{strongest}**元素较为旺盛，"
        f"而**{weakest}**相对偏弱。"
        f"在分析运势时，需要特别关注五行的平衡与调和。"
    )


def _build_yinyang_analysis(tiangan_list, dizhi_list):
    """Build a yinyang analysis paragraph."""
    if not tiangan_list:
        return "（需排盘数据）"
    yang = sum(1 for tg in tiangan_list if tg and tg in "甲丙戊庚壬")
    yin = len(tiangan_list) - yang
    return f"天干中阳干{yang}个、阴干{yin}个，整体偏向{'阳' if yang > yin else '阴'}。"


def _build_shishen_analysis(day_master, bazi):
    """Build a shishen analysis paragraph."""
    if not day_master or not bazi:
        return "需要完整的八字排盘数据来进行十神分析。"
    # For each pillar's tiangan, determine relationship with day master
    _SHI_SHEN_MAP = {
        ("甲", "甲"): "比肩", ("甲", "乙"): "劫财", ("甲", "丙"): "食神", ("甲", "丁"): "伤官",
        ("甲", "戊"): "偏财", ("甲", "己"): "正财", ("甲", "庚"): "七杀", ("甲", "辛"): "正官",
        ("甲", "壬"): "偏印", ("甲", "癸"): "正印",
        ("乙", "甲"): "劫财", ("乙", "乙"): "比肩", ("乙", "丙"): "伤官", ("乙", "丁"): "食神",
        ("乙", "戊"): "正财", ("乙", "己"): "偏财", ("乙", "庚"): "正官", ("乙", "辛"): "七杀",
        ("乙", "壬"): "正印", ("乙", "癸"): "偏印",
        ("丙", "甲"): "偏印", ("丙", "乙"): "正印", ("丙", "丙"): "比肩", ("丙", "丁"): "劫财",
        ("丙", "戊"): "食神", ("丙", "己"): "伤官", ("丙", "庚"): "偏财", ("丙", "辛"): "正财",
        ("丙", "壬"): "七杀", ("丙", "癸"): "正官",
        ("丁", "甲"): "正印", ("丁", "乙"): "偏印", ("丁", "丙"): "劫财", ("丁", "丁"): "比肩",
        ("丁", "戊"): "伤官", ("丁", "己"): "食神", ("丁", "庚"): "正财", ("丁", "辛"): "偏财",
        ("丁", "壬"): "正官", ("丁", "癸"): "七杀",
        ("戊", "甲"): "七杀", ("戊", "乙"): "正官", ("戊", "丙"): "偏印", ("戊", "丁"): "正印",
        ("戊", "戊"): "比肩", ("戊", "己"): "劫财", ("戊", "庚"): "食神", ("戊", "辛"): "伤官",
        ("戊", "壬"): "偏财", ("戊", "癸"): "正财",
        ("己", "甲"): "正官", ("己", "乙"): "七杀", ("己", "丙"): "正印", ("己", "丁"): "偏印",
        ("己", "戊"): "劫财", ("己", "己"): "比肩", ("己", "庚"): "伤官", ("己", "辛"): "食神",
        ("己", "壬"): "正财", ("己", "癸"): "偏财",
        ("庚", "甲"): "偏财", ("庚", "乙"): "正财", ("庚", "丙"): "七杀", ("庚", "丁"): "正官",
        ("庚", "戊"): "偏印", ("庚", "己"): "正印", ("庚", "庚"): "比肩", ("庚", "辛"): "劫财",
        ("庚", "壬"): "食神", ("庚", "癸"): "伤官",
        ("辛", "甲"): "正财", ("辛", "乙"): "偏财", ("辛", "丙"): "正官", ("辛", "丁"): "七杀",
        ("辛", "戊"): "正印", ("辛", "己"): "偏印", ("辛", "庚"): "劫财", ("辛", "辛"): "比肩",
        ("辛", "壬"): "伤官", ("辛", "癸"): "食神",
        ("壬", "甲"): "食神", ("壬", "乙"): "伤官", ("壬", "丙"): "偏财", ("壬", "丁"): "正财",
        ("壬", "戊"): "七杀", ("壬", "己"): "正官", ("壬", "庚"): "偏印", ("壬", "辛"): "正印",
        ("壬", "壬"): "比肩", ("壬", "癸"): "劫财",
        ("癸", "甲"): "伤官", ("癸", "乙"): "食神", ("癸", "丙"): "正财", ("癸", "丁"): "偏财",
        ("癸", "戊"): "正官", ("癸", "己"): "七杀", ("癸", "庚"): "正印", ("癸", "辛"): "偏印",
        ("癸", "壬"): "劫财", ("癸", "癸"): "比肩",
    }
    pillars = bazi[:4] if isinstance(bazi, (list, tuple)) else []
    dm_short = day_master[0] if len(day_master) > 0 else ""
    if not dm_short:
        return "需要完整的八字排盘数据来进行十神分析。"
    shishen_list = []
    for pillar in pillars:
        tg = pillar[0] if len(pillar) >= 2 else ""
        if tg:
            ss = _SHI_SHEN_MAP.get((dm_short, tg), "")
            if ss:
                shishen_list.append(f"{pillar}→{ss}")
    if shishen_list:
        return f"以日主{day_master}为基准，你的四柱天干对应的十神分别是：{'；'.join(shishen_list)}。十神的组合反映了你与外界的关系模式。"
    return "通过十神分析，可以了解你的性格特点和人际关系模式。"


def _build_geju_analysis(geju, day_master):
    """Build a geju analysis paragraph."""
    if not geju or geju in ("?", "（待判断）"):
        return f"你的日主是{day_master}。格局是八字分析的高级内容，需结合完整的排盘数据来判断你是属于正格还是特殊格局。"
    return (
        f"你的格局是**{geju}**。这个格局说明了你命局的整体特征和人生基调。"
        f"不同的格局有不同的喜忌和运势特征。"
    )


def _build_yongshen_analysis(yongshen, day_master):
    """Build a yongshen analysis paragraph."""
    if not yongshen or yongshen in ("?", "（待分析）"):
        return f"用神是八字中对你最有利的五行。以你的日主{day_master}为基础，通过分析八字五行的强弱和平衡关系来确定用神。"
    return (
        f"你的用神是**{yongshen}**。这意味着在你的命局中，"
        f"{yongshen}这个五行对你是最有利的，"
        f"在日常生活中可以多接触与{yongshen}相关的事物来增强运势。"
    )


def _build_dayun_analysis(day_master, geju, yongshen):
    """Build a dayun analysis paragraph."""
    parts = [f"你的日主是{day_master}。"]
    if geju and geju not in ("?", "（待判断）"):
        parts.append(f"格局为{geju}。")
    if yongshen and yongshen not in ("?", "（待分析）"):
        parts.append(f"用神为{yongshen}。")
    parts.append("大运每十年一换，不同的大运会对你的运势产生不同影响。")
    return "".join(parts)


def _build_wealth_analysis(day_master, bazi):
    """Build a wealth analysis paragraph."""
    # Check if any pillar contains wealth-related elements
    _CAI_XING = {"戊": "偏财", "己": "正财"}  # 甲木日主的财星
    if not day_master:
        return "需要你的八字排盘数据才能进行财运分析。"
    dm = day_master[0]
    # For 甲/乙木, 戊己土 is wealth; for 丙/丁火, 庚辛金; for 戊/己土, 壬癸水; for 庚/辛金, 甲乙木; for 壬/癸水, 丙丁火
    cai_map = {
        "甲": ("戊", "己", "土"), "乙": ("己", "戊", "土"),
        "丙": ("庚", "辛", "金"), "丁": ("辛", "庚", "金"),
        "戊": ("壬", "癸", "水"), "己": ("癸", "壬", "水"),
        "庚": ("甲", "乙", "木"), "辛": ("乙", "甲", "木"),
        "壬": ("丙", "丁", "火"), "癸": ("丁", "丙", "火"),
    }
    info = cai_map.get(dm)
    if info:
        return (
            f"你的正财星是{info[0]}，偏财星是{info[1]}，属{info[2]}。\n"
            f"在你的八字中，财星的强弱和位置决定了你的财富格局。"
            f"如果财星在月令当旺，或在地支有根，通常财运较好。"
        )
    return "通过分析你八字中的财星位置和强弱，可以了解你的财运特点。"


def _build_love_analysis(day_master):
    """Build a love/marriage analysis paragraph."""
    # Spouse star analysis based on day master
    spouse_map = {
        "甲": ("辛", "正官"), "乙": ("庚", "七杀"),
        "丙": ("癸", "正官"), "丁": ("壬", "七杀"),
        "戊": ("乙", "正官"), "己": ("甲", "七杀"),
        "庚": ("丁", "正官"), "辛": ("丙", "七杀"),
        "壬": ("己", "正官"), "癸": ("戊", "七杀"),
    }
    dm = day_master[0] if day_master else ""
    info = spouse_map.get(dm)
    if info:
        return (
            f"你的配偶星是{info[0]}（{info[1]}）。\n"
            f"配偶星在八字中的位置和强弱，反映了你的感情模式和婚姻状况。"
            f"日支（婚姻宫）的五行属性也会影响你的感情生活。"
        )
    return "通过八字中的配偶星和婚姻宫（日支）来分析感情运势。"


def _build_career_analysis(day_master):
    """Build a career analysis paragraph."""
    career_map = {
        "甲": ("庚辛", "金", "管理、金融、法律、军警"),
        "乙": ("庚辛", "金", "精细加工、医疗、金融"),
        "丙": ("壬癸", "水", "贸易、物流、传媒、教育"),
        "丁": ("壬癸", "水", "咨询、文化传播、水利"),
        "戊": ("甲乙", "木", "农林、教育、文化、设计"),
        "己": ("甲乙", "木", "园艺、出版、教育"),
        "庚": ("丙丁", "火", "能源、科技、餐饮、文化"),
        "辛": ("丙丁", "火", "珠宝、精密仪器、餐饮"),
        "壬": ("戊己", "土", "房地产、建筑、矿业"),
        "癸": ("戊己", "土", "地产、仓储、农业"),
    }
    dm = day_master[0] if day_master else ""
    info = career_map.get(dm)
    if info:
        return (
            f"你的官星为{info[0]}，属{info[1]}。\n"
            f"从你的日主{day_master}来看，比较适合的行业方向包括：{info[2]}。\n"
            f"官星的旺衰和位置反映了职场发展潜力和贵人运。"
        )
    return "通过八字中的官星和印星来分析事业运势。"


def _build_health_analysis(day_master_wuxing):
    """Build a health analysis paragraph."""
    health_map = {
        "木": ("肝、胆、眼睛", "春季", "绿色、东方", "少熬夜，多吃绿色蔬菜"),
        "火": ("心脏、小肠、血脉", "夏季", "红色、南方", "注意情绪管理，避免过度劳累"),
        "土": ("脾胃、肌肉", "季末（每个季节最后一个月）", "黄色、中部", "饮食规律，注意消化系统"),
        "金": ("肺、大肠、皮肤", "秋季", "白色、西方", "注意呼吸系统保养，保持皮肤清洁"),
        "水": ("肾、膀胱、耳朵", "冬季", "黑色、北方", "注意保暖，避免过度疲劳"),
    }
    info = health_map.get(day_master_wuxing)
    if info:
        return (
            f"你的日主五行属{day_master_wuxing}。\n"
            f"对应身体部位：{info[0]}。\n"
            f"调理建议：{info[3]}。"
        )
    return "根据你的八字五行特点来调整养生策略，可以达到更好的保健效果。"


def list_topics() -> str:
    """List all available tutorial topics."""
    lines = ["📚 **八字学堂** — 从零开始学命理", ""]
    for section, topics in CURRICULUM.items():
        lines.append(f"**{section}篇**")
        for t in topics:
            lines.append(f"  • {t}")
    lines.append("")
    lines.append("发送「学堂 话题名」开始学习，例如：")
    lines.append("  • 学堂 什么是八字")
    lines.append("  • 学堂 五行生克")
    lines.append("  • 学堂 十神分析")
    return "\n".join(lines)
