#!/usr/bin/env python3
"""验证 deepseek Anthropic 兼容端点是否支持原生 tool_use（批次 1 Task 1）。

用法: FORTUNE_API_KEY=<key> python scripts/verify_deepseek_tool_use.py [model]
默认模型与生产一致: deepseek-v4-flash；也可传 deepseek-chat / deepseek-reasoner。
退出码: 0=原生支持 1=不支持/被忽略 2=缺 key 3=网络/协议错误 4=未知
"""
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.llm.client import (  # noqa: E402
    ANTHROPIC_MESSAGES_URL, _anthropic_headers, _anthropic_model_name,
)

TOOLS = [{
    "name": "web_search",
    "description": "网络搜索，输入关键词获取最新信息",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        "required": ["query"],
    },
}]


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash"
    api_key = os.environ.get("FORTUNE_API_KEY")
    if not api_key:
        print("FATAL: 需要 FORTUNE_API_KEY 环境变量")
        return 2
    payload = {
        "model": _anthropic_model_name(model),
        "max_tokens": 300,
        "temperature": 0.3,
        "tools": TOOLS,
        "tool_choice": {"type": "auto"},
        "messages": [{"role": "user",
                      "content": "帮我查一下：今天北京天气怎么样？请用搜索工具。"}],
    }
    try:
        resp = httpx.post(ANTHROPIC_MESSAGES_URL,
                          headers=_anthropic_headers(api_key),
                          json=payload, timeout=90.0)
    except Exception as e:  # noqa: BLE001
        print(f"RESULT=ERROR 请求异常: {e}")
        return 3
    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        print("RESULT=ERROR 非 JSON 响应:", resp.text[:300])
        return 3
    if resp.status_code != 200:
        print("RESULT=NOT_SUPPORTED_OR_ERROR")
        print(json.dumps(data, ensure_ascii=False)[:500])
        return 1
    stop = data.get("stop_reason", "")
    content = data.get("content", [])
    tool_blocks = [b for b in content
                   if isinstance(b, dict) and b.get("type") == "tool_use"]
    text_blocks = [b for b in content
                   if isinstance(b, dict) and b.get("type") == "text"]
    print(f"stop_reason={stop}")
    print(f"tool_use_blocks={len(tool_blocks)} text_blocks={len(text_blocks)}")
    if tool_blocks:
        print("RESULT=SUPPORTED_NATIVE_TOOL_USE")
        print(json.dumps(tool_blocks[0], ensure_ascii=False)[:400])
        return 0
    if text_blocks:
        print("RESULT=IGNORED_TOOLS (纯文本，tools 被忽略)")
        print(json.dumps(text_blocks[0], ensure_ascii=False)[:300])
        return 1
    print("RESULT=UNKNOWN")
    print(json.dumps(data, ensure_ascii=False)[:500])
    return 4


if __name__ == "__main__":
    sys.exit(main())
