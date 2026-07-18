"""八字学堂 — beginner-friendly fortune-telling education content.

Uses the existing RAG knowledge base to generate structured tutorials
for beginners. Covers basic concepts progressively.
"""

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


def get_lesson(topic: str, retriever=None) -> str:
    """Generate a beginner-friendly lesson on a given topic using RAG.

    Args:
        topic: Topic name from CURRICULUM keys
        retriever: RAG retriever for classical text lookup

    Returns:
        Formatted markdown lesson text
    """
    # Find the topic in curriculum
    section = None
    keywords = []
    for sec, topics in CURRICULUM.items():
        if topic in topics:
            section = sec
            keywords = topics[topic]
            break

    if not keywords:
        # Try fuzzy match
        for sec, topics in CURRICULUM.items():
            for t, kws in topics.items():
                if topic in t or t in topic:
                    section = sec
                    keywords = kws
                    break
            if section:
                break

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
