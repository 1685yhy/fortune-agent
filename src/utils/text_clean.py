"""文本清理工具 - emoji 强收敛（独立轻量模块）。

v2026-08-17：PM 反馈回复 emoji 过多显 low，所有 LLM 输出统一剔除 emoji。
本模块从 src/llm/client.py 抽出，供统一模型层与各引擎直调点共用——
引擎直接 import client.py 会拉起 bazi/retriever 等重依赖，此处零依赖。

覆盖块：
  U+1F000-1FAFF  表情/扩展象形/符号（主 emoji 区）
  U+2600-26FF    杂项符号（☀⛅☕⚠ 等，常被渲染为 emoji）
  U+2700-27BF    印刷符号（✂✈✓✕ 等）
  U+2B00-2BFF    杂项符号箭头（⭕⭐ 等）
  变体选择符 FE0F / ZWJ 200D / 键盘帽 20E3
另剔孤立代理项（\uD800-\uDFFF）：流式分片可能切断代理对，残缺半对一并剔除，
不会残留乱码。中文（一-）、全角标点（　-〿、＀-）、
半角标点/字母数字均不受影响。
"""
import re

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "️‍⃣]"
)
_LONE_SURROGATE_RE = re.compile("[\uD800-\uDFFF]")


def strip_emoji(text: str) -> str:
    """剔除回复文本中的 emoji 字符与孤立代理项，保留中文/标点/字母数字。"""
    if not text:
        return text
    return _LONE_SURROGATE_RE.sub("", _EMOJI_RE.sub("", text))
