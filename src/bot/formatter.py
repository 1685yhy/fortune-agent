"""微信消息格式化 - 适配微信展示."""
from typing import List

MAX_MSG_LENGTH = 500  # 微信单条消息最佳长度


def split_long_message(text: str, max_len: int = MAX_MSG_LENGTH) -> List[str]:
    """将长消息拆分为多条适合微信发送的片段"""
    if len(text) <= max_len:
        return [text]

    parts = []
    lines = text.split('\n')
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 <= max_len:
            current += line + '\n'
        else:
            if current.strip():
                parts.append(current.strip())
            current = line + '\n'

    if current.strip():
        parts.append(current.strip())

    return parts


def format_error(message: str) -> str:
    return f"⚠️ {message}"


def format_loading() -> str:
    return "🔮 正在为您排盘分析，请稍候..."


def format_greeting() -> str:
    return """👋 您好！我是命理助手

我能帮您分析以下内容：
1️⃣ 八字命理（看命格、运势、事业、婚姻）
2️⃣ 紫微斗数（十二宫详解）
3️⃣ 易经占卜（具体事情问卦）
4️⃣ 风水分析（家居布局）
5️⃣ 择日（婚嫁、开业吉日）

请直接告诉我您想了解什么？"""
