"""Scenario-based entry API — 6 scenario cards for Phase 2 Decision Engine."""
from fastapi import APIRouter

router = APIRouter(tags=["scenarios"])

SCENARIOS = [
    {
        "id": "career_change",
        "icon": "💼",
        "title": "我该不该换工作",
        "description": "分析当前工作运势和跳槽的最佳时机",
        "prompt_template": "我在考虑换工作，请根据我的八字分析：1)当前工作的发展空间和运势 2)跳槽的最佳时机窗口 3)适合的行业方向 4)需要注意的风险",
        "required_info": ["birth_date", "birth_time", "gender"],
        "category": "career",
    },
    {
        "id": "love_relationship",
        "icon": "❤️",
        "title": "这段感情能走下去吗",
        "description": "看缘分深浅，分析感情发展走向",
        "prompt_template": "我想了解这段感情的发展前景，请根据我的八字分析：1)这段感情的缘分深浅 2)可能遇到的阻碍和挑战 3)感情发展的关键时间点 4)如何经营这段关系",
        "required_info": ["birth_date", "birth_time", "gender"],
        "category": "love",
    },
    {
        "id": "wealth_year",
        "icon": "💰",
        "title": "今年财运如何",
        "description": "分析正财偏财运，掌握财富机遇",
        "prompt_template": "我想了解今年的财运，请根据我的八字分析：1)今年正财运和偏财运趋势 2)财运最佳的时间窗口 3)适合的投资和理财方向 4)需要注意的破财风险",
        "required_info": ["birth_date", "birth_time", "gender"],
        "category": "wealth",
    },
    {
        "id": "health_concern",
        "icon": "🏥",
        "title": "健康需要注意什么",
        "description": "从命理角度分析健康隐忧",
        "prompt_template": "我想了解健康方面的注意事项，请根据我的八字分析：1)命局中哪些五行偏弱，对应哪些身体部位容易出问题 2)大运流年对健康的影响 3)需要注意的年份和季节 4)日常养生保健建议",
        "required_info": ["birth_date", "birth_time", "gender"],
        "category": "health",
    },
    {
        "id": "property_move",
        "icon": "🏠",
        "title": "适合搬家/买房吗",
        "description": "分析不动产方面的运势，选对时机行动",
        "prompt_template": "我想了解在房产方面的运势，请根据我的八字分析：1)当前是否适合买房或搬家 2)对居住环境的风水建议 3)适合的方位和朝向 4)不动产投资的吉凶时机",
        "required_info": ["birth_date", "birth_time", "gender"],
        "category": "property",
    },
    {
        "id": "compatibility",
        "icon": "🤝",
        "title": "和他/她合不合",
        "description": "合婚配对分析，看两人的命理是否相合",
        "prompt_template": "我想了解两人的缘分和配对情况，请根据双方的八字分析：1)两人的五行互补和冲突 2)感情中的主要矛盾点 3)长期相处的前景 4)如何调和彼此的差异",
        "required_info": ["birth_date", "birth_time", "gender"],
        "category": "compatibility",
    },
]


@router.get("/scenarios")
async def get_scenarios():
    """Return 6 scenario cards."""
    return {"scenarios": SCENARIOS, "total": len(SCENARIOS)}
