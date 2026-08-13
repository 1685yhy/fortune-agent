"""深夜倾诉分类器(方案·灯下漫谈):消息 → 分类级主题。
红线:只输出分类标签,绝不落库原文/人名/事件(与灯语锚点同规则)。"""
CATEGORY_KEYWORDS = {
    "事业": ["事业", "工作", "面试", "加班", "升职", "跳槽", "老板", "同事", "辞职", "上班"],
    "感情": ["感情", "恋爱", "分手", "思念", "结婚", "对象", "喜欢", "想念", "心碎", "前任", "想起"],
    "健康": ["健康", "失眠", "睡不着", "累", "疲惫", "身体", "医院", "生病", "熬夜"],
    "财运": ["财运", "钱", "收入", "开销", "理财", "还债", "工资", "欠款"],
}
DEFAULT = "其他"


def classify_night_topic(message: str) -> str:
    """消息 → 分类级主题(关键词规则,命中第一个分类即返回)。"""
    if not message:
        return DEFAULT
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in message for k in kws):
            return cat
    return DEFAULT
