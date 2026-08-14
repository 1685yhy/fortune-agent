#!/usr/bin/env python3
"""Task 4（chat-ux）验证：流式收尾对齐 —— 构造「流尾≠reply头」场景，断言不丢头/不丢句。

根因：同一轮多次 LLM 调用（草稿流/润色稿/工具续写）灌同一条流，收尾用
`streamed_text.endswith(reply[:k])` 求最长前缀重叠 covered。一旦流出的尾巴与
reply 开头对不上（草稿流与润色稿开头不同），covered 截在句子中间，
reply 开头没流出过的句子被静默丢弃（用户看到「直接、」这类残缺片段）。

修法（compute_stream_remaining）：
  - covered 计算保持现有（最长后缀匹配）；
  - covered==0 → 整段重发（保留现状）；
  - 剩余起点恰在句子边界（. ！？… 换行）→ 增量补发 reply[covered:]；
  - 否则回溯到 reply 中 covered 之前最近的完整句子边界，从该句起点补发
    reply[边界+1:]，保证补发的句子完整、reply 中未流出过的句子绝不丢弃
    （截断句中已流出的前缀允许重叠一次）；
  - covered 之前不存在任何句子边界 → 整段补发（整个前缀是同一句，补发即句子完整）。

覆盖：
  1. 完美覆盖（流尾 = reply 全文）→ 无补发
  2. 句边界处截断 → 仅补剩余句子
  3. 句子中间截断（草稿流与润色稿开头不同）→ 回溯到最近句子边界，
     补发完整句子；无丢句、无重复句子
  4. covered=0（流尾与 reply 头完全无重叠）→ 整段重发（保留现状）
  5. 前缀内无任何句子边界 → 整段补发（保证句子完整）
  6. welcome 前缀场景（streamed = 欢迎语 + reply）→ 无补发
  7. emoji 指令收敛：prompts.py 三处不再鼓励 emoji
  8. 润色提示措辞：handler.py 润色 prompt 明确「表格用 markdown 表格格式」

用法：
  .venv/bin/python3 scripts/test_stream_align.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.chat_stream import compute_stream_remaining  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def sentences(s):
    """按句末标点（。！？… 换行）切出完整句子（含句末标点）。"""
    out = []
    cur = ""
    for ch in s:
        cur += ch
        if ch in "。！？…\n":
            if cur.strip():
                out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return out


def check_aligned(name, reply, streamed, expect_remaining):
    """断言补发文本等于期望，且最终流出的文本（streamed+remaining）
    包含 reply 全部句子、无重复句子。"""
    remaining = compute_stream_remaining(reply, streamed)
    final = streamed + remaining
    ok(f"{name}: remaining 正确", remaining == expect_remaining,
       f"got {remaining!r} want {expect_remaining!r}")
    ss = [x for x in sentences(reply) if x.strip()]
    missing = [x for x in ss if x not in final]
    dup = [x for x in ss if final.count(x) > 1]
    ok(f"{name}: reply 全部句子已流出（无丢句）", not missing,
       f"missing={missing!r} final={final!r}")
    ok(f"{name}: 无重复句子（除截断句前缀重叠一次）", not dup,
       f"dup={dup!r} final={final!r}")


print("== 对齐函数 compute_stream_remaining ==")

# 1. 完美覆盖：流尾 == reply 全文（润色稿完整流出）→ 无补发
reply1 = "今天运势整体不错，宜积极行动。忌冲动消费。"
check_aligned("1 完美覆盖", reply1, "今天运势整体不错，宜积极行动。忌冲动消费。", "")

# 2. 句边界处截断 → 仅补剩余句子（现状增量行为保留）
reply2 = "今天运势整体不错，宜积极行动。忌冲动消费。"
check_aligned("2 句边界截断", reply2,
              "前面草稿内容。今天运势整体不错，宜积极行动。", "忌冲动消费。")

# 3. 句子中间截断（草稿流与润色稿开头不同 → covered 截在句中）
#    streamed 尾 = reply[:17] = "…宜积极行动。忌冲"（「忌冲」已流出，句子不完整）
#    → 回溯到「。」，补发「忌冲动消费。」（完整句子，前缀「忌冲」重叠一次）
reply3 = "今天运势整体不错，宜积极行动。忌冲动消费。"
check_aligned("3 句中截断回溯", reply3,
              "草稿开头不同。今天运势整体不错，宜积极行动。忌冲", "忌冲动消费。")

# 3b. 截断在更靠前的位置（取 covered 前最近的一个句子边界；
#      「宜忌如下：」是句内引导语，冒号不是句边界 → 整句「宜忌如下：忌冲动行事。」重发）
reply3b = "直接、坦率地说，你最近的运势整体平稳。宜忌如下：忌冲动行事。"
check_aligned("3b 取最近边界", reply3b,
              "旧的草稿尾巴。直接、坦率地说，你最近的运势整体平稳。宜忌如下：忌",
              "宜忌如下：忌冲动行事。")

# 3c. 换行也是句子边界：截断在换行句内 → 回溯到换行后的句子起点
reply3c = "第一段话。\n第二段话内容。"
check_aligned("3c 换行边界回溯", reply3c,
              "草稿尾巴。第一段话。\n第二段话内", "第二段话内容。")

# 4. covered=0：流尾与 reply 头完全无重叠 → 整段重发（保留现状）
reply4 = "直接、坦率地说，你最近的运势整体平稳。"
check_aligned("4 无重叠整段重发", reply4,
              "草稿流与润色稿开头完全不同。这只是一段草稿尾巴。", reply4)

# 5. 前缀内无任何句子边界（整个前缀是同一句）→ 整段补发（保证句子完整）
reply5 = "今天运势整体不错宜积极行动"
check_aligned("5 无边界整段补发", reply5, "前面。今天运势整体不错宜积极行", reply5)

# 6. welcome 前缀场景：streamed = 欢迎语 + reply（开场在回复之外）→ 无补发
reply6 = "好久不见，最近运势平稳。"
check_aligned("6 welcome 前缀", reply6, "欢迎回来。\n\n好久不见，最近运势平稳。", "")

# 6b. 空 reply / 空 streamed 兜底
ok("6b 空 reply → 空补发", compute_stream_remaining("", "随便什么") == "")
ok("6b 空 streamed → 整段重发", compute_stream_remaining("有内容。", "") == "有内容。")

print("== emoji 指令收敛（prompts.py 三处） ==")
prompts_src = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "src/llm/prompts.py"), encoding="utf-8").read()
ok("不再鼓励「适当加 emoji」", "适当加 emoji" not in prompts_src)
ok("不再「善用 emoji 表达温度」", "善用 emoji" not in prompts_src)
ok("三处均改为「默认不使用 emoji」",
   prompts_src.count("默认不使用 emoji，仅在情绪表达极必要时使用不超过 1 个") == 3,
   f"count={prompts_src.count('默认不使用 emoji')}")

print("== 润色提示措辞（handler.py 表格格式） ==")
handler_src = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "src/bot/handler.py"), encoding="utf-8").read()
ok("润色提示明确「表格用 markdown 表格格式」", "markdown 表格格式" in handler_src)

print(f"\n结果: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
