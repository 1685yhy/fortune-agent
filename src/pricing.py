"""Pricing configuration — single source of truth for all pricing data."""
from typing import Dict, List

FREE_FEATURES: List[str] = [
    "八字排盘",
    "日主五行分析",
    "基础解读（前200字）",
    "追问对话（无限次）",
]

PAID_FEATURES: Dict[str, dict] = {
    "full_reading": {
        "name": "完整命理深度解读",
        "price_cny": 9.90,
        "description": "包含财运/事业/感情/健康四大维度完整分析 + 行动建议",
        "cost_note": "AI分析成本约 ¥0.03，我们收费 ¥9.90（含人工优化和持续服务）",
    },
    "yearly_report": {
        "name": "年度运势报告",
        "price_cny": 19.90,
        "description": "未来12个月逐月运势分析 + 关键节点预警",
    },
    "compatibility": {
        "name": "合盘分析",
        "price_cny": 12.90,
        "description": "两人八字合盘 + 关系匹配度 + 相处建议",
    },
}

# New user benefit
NEW_USER_FREE_READINGS: int = 3

# Anti-scam declaration
TRUST_STATEMENT: str = (
    "我们不承诺扭转命数，不卖符咒器物，不代断言生死祸福。"
    "我们只做基于传统命理学的分析和建议。"
)
