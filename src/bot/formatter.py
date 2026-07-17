"""微信消息格式化 - 适配微信展示."""
from typing import List

MAX_MSG_LENGTH = 500  # 微信单条消息最佳长度


def _split_overflow_line(line: str, max_len: int) -> List[str]:
    """将超长行拆分为多段（优先在空格处拆分）"""
    parts = []
    while len(line) > max_len:
        # 在 max_len 范围内找最后一个空格
        split_at = line.rfind(' ', 0, max_len)
        if split_at == -1:
            # 无空格可拆，硬切
            split_at = max_len
        parts.append(line[:split_at].strip())
        line = line[split_at:].strip()
    if line:
        parts.append(line)
    return parts


def split_long_message(text: str, max_len: int = MAX_MSG_LENGTH) -> List[str]:
    """将长消息拆分为多条适合微信发送的片段"""
    if len(text) <= max_len:
        return [text]

    parts = []
    lines = text.split('\n')
    current = ""

    for line in lines:
        # 单行超长时先行拆分
        if len(line) > max_len:
            # 先把当前积累的 current 提交
            if current.strip():
                parts.append(current.strip())
                current = ""
            for sub_line in _split_overflow_line(line, max_len):
                parts.append(sub_line)
            continue

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
1️⃣ ✅ 八字命理（看命格、运势、事业、婚姻）
2️⃣ ✅ 紫微斗数（十二宫详解）
3️⃣ ✅ 易经占卜（具体事情问卦）
4️⃣ ✅ 风水分析（家居布局）
5️⃣ ✅ 择日（婚嫁、开业吉日）
6️⃣ ✅ 面相手相（通过面相手相看运势性格）
7️⃣ ✅ 奇门遁甲（运筹决策、方位吉凶）
8️⃣ ✅ 姓名学（起名改名、姓名分析）
9️⃣ ✅ 合婚配对（婚姻匹配、缘分分析）
🔟 ✅ 周公解梦（梦境解析、预兆解读）

请直接告诉我您想了解什么？"""
