# -*- coding: utf-8 -*-
"""k33/A19：流式与清洗残留四项（审计行逐项）。

1. refill 吞换行（k5 Minor-1）：补发点落在空行边界时回退一位，补发块以完整
   空行开头（前端拼接吞首换行时段落不再被并成一行）；
2. 版本页脚误剥（k7b Minor-1）：正文行首恰是「解读版本：…」的普通文案不得被
   `strip_card_decor_for_llm` 剥掉（收紧为「版本号 + 页脚成分」签名）；
3. `---` 分隔行尾随锚定（k7b Minor-2）：与 `———` 对称（允许行尾空白）；
4. L2 记忆压缩通道清洗（k7b 遗留）：`MemoryCompactor` 喂 LLM 的文本与降级摘要
   不得含卡装饰/版本页脚/TOOL 标签（只读清洗，库内原文不动）。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_cleanup_residue.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.api.chat_stream import compute_stream_remaining  # noqa: E402
from src.bot.card_mark import strip_card_decor_for_llm  # noqa: E402
from src.bot.memory_compactor import MemoryCompactor  # noqa: E402

# ══════════════════════════════════════════════════════════════
# 1) refill 吞换行
# ══════════════════════════════════════════════════════════════

_BODY20 = "甲" * 20  # 正文主体（≥20：触发 k5 尾部对齐）


class TestRefillNewline:
    def test_blank_line_boundary_steps_back_one(self):
        """补发点恰在空行边界（…\\n|\\n…）→ 回退一位，补发块以空行开头。

        构造：reply = 正文 + 空行 + 尾部；streamed 含 reply[:21]（正文+\n）
        但不以 reply 前缀结尾（covered=0）→ 落 k5 尾部对齐，best=21，
        reply[20]==reply[21]=="\\n"（旧实现补发 "\\n尾部" → 空行被吞一位）。
        """
        reply = _BODY20 + "\n\n尾部内容"
        streamed = "X" + reply[:21] + "Y"

        out = compute_stream_remaining(reply, streamed)

        assert out == "\n\n尾部内容", "空行边界必须整段补发（不许吞换行）"
        assert out.startswith("\n\n")
        assert "尾部内容" in out

    def test_no_character_lost_after_step_back(self):
        """回退一位只多补一个换行：正文一字不重、不丢。"""
        reply = _BODY20 + "\n\n尾部内容"
        streamed = "X" + reply[:21] + "Y"
        out = compute_stream_remaining(reply, streamed)
        assert out == reply[20:]
        assert reply.startswith(_BODY20 + "\n")
        # 补发块是 reply 的真后缀（无重复正文）
        assert reply.endswith(out)

    def test_non_blank_boundary_unchanged(self):
        """边界不是空行 → 行为不变（回归锁）。"""
        reply = _BODY20 + "\n尾部内容"
        streamed = "X" + reply[:21] + "Y"
        out = compute_stream_remaining(reply, streamed)
        assert out == "尾部内容"

    def test_whole_reply_streamed_returns_empty(self):
        reply = _BODY20 + "\n\n尾部内容"
        assert compute_stream_remaining(reply, reply) == ""


# ══════════════════════════════════════════════════════════════
# 2) 版本页脚误剥（防误伤）
# ══════════════════════════════════════════════════════════════

_REAL_FOOTER = "解读版本: v5.0.0 | 生成时间: 2026-08-23T23:20:29+08:00"


class TestVersionFooterStrip:
    def test_real_footer_line_stripped(self):
        text = f"正文一。\n{_REAL_FOOTER}\n正文二。"
        assert strip_card_decor_for_llm(text) == "正文一。\n正文二。"

    def test_real_footer_without_pipe_stripped(self):
        assert strip_card_decor_for_llm("正文一。\n解读版本：v5.0.0\n正文二。") == \
            "正文一。\n正文二。"

    def test_body_line_starting_with_version_word_kept(self):
        """防误伤红线：正文行首恰是「解读版本：」的普通文案不得被剥。"""
        text = "解读版本：这是我们第 5 版，说说版本演进\n正文二。"
        assert strip_card_decor_for_llm(text) == text

    def test_body_line_version_number_then_prose_kept(self):
        """版本号后接正文（非页脚成分）→ 不剥。"""
        text = "解读版本：2026 年的版本演进聊一聊\n正文二。"
        assert strip_card_decor_for_llm(text) == text

    def test_footer_already_removed_defensive_noop(self):
        """B3-2-C 后页脚不再生成：无页脚文本恒等（strip 后）。"""
        text = "正文一。\n\n正文二。"
        assert strip_card_decor_for_llm(text) == text


# ══════════════════════════════════════════════════════════════
# 3) --- 分隔行尾随锚定（与 ——— 对称）
# ══════════════════════════════════════════════════════════════

class TestDashSeparatorAnchor:
    def test_dash_line_stripped(self):
        assert strip_card_decor_for_llm("正文甲\n----\n正文乙") == "正文甲\n正文乙"

    def test_dash_line_with_trailing_spaces_stripped(self):
        """旧正则 `^\\s*---+$` 不容尾随空白（与 ——— 不对称）→ 本项对齐。"""
        assert strip_card_decor_for_llm("正文甲\n----  \n正文乙") == "正文甲\n正文乙"

    def test_emdash_line_symmetric(self):
        assert strip_card_decor_for_llm("正文甲\n———  \n正文乙") == "正文甲\n正文乙"

    @pytest.mark.parametrize("text", [
        "他说这是分隔符 --- 的意思",
        "正文甲 a---b 正文乙",
        "正文甲\n——不是分隔——\n正文乙",
    ])
    def test_body_with_dashes_kept(self, text):
        assert strip_card_decor_for_llm(text) == text


# ══════════════════════════════════════════════════════════════
# 4) L2 记忆压缩通道清洗
# ══════════════════════════════════════════════════════════════

_DIRTY_ASSISTANT = (
    '[card:paipan title="我的命盘"]\n'
    "你生于丙子年腊月，四柱官印相生。\n"
    "📊 命盘图片:http://x.example/y.png\n"
    "[/card]\n"
    "———\n"
    "解读版本: v5.0.0 | 生成时间: 2026-08-23T23:20:29+08:00"
)
_DIRTY_TOOL_MSG = ('先查一下 <tool_calls>[{"tool": "web_search", '
                   '"params": {"query": "x"}}]</tool_calls> 再回答。')


class _CaptureLLM:
    def __init__(self, out):
        self.out = out
        self.prompts = []

    def __call__(self, api_key, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return self.out


_SUMMARY_OUT = ("<summary>\n对话要点：用户问了排盘\n用户状态：平稳\n"
                "关键结论：无\n未完事项：无\n</summary>\n<memories>\n无\n</memories>")


class TestL2CompactionCleaning:
    def _compactor(self, out=_SUMMARY_OUT):
        cap = _CaptureLLM(out)
        c = MemoryCompactor(api_key="k", llm_fn=cap)
        return c, cap

    def test_summarize_block_prompt_has_no_decor(self):
        c, cap = self._compactor()
        block = [{"role": "user", "content": "帮我排盘"},
                 {"role": "assistant", "content": _DIRTY_ASSISTANT}]
        summary, _mem = c._summarize_block(block, "")

        assert summary  # 摘要正常产出
        prompt = cap.prompts[0]
        assert "[card:" not in prompt
        assert "[/card]" not in prompt
        assert "📊" not in prompt and "http://" not in prompt
        assert "解读版本" not in prompt
        assert "———" not in prompt
        assert "你生于丙子年腊月" in prompt  # 正文保留

    def test_summarize_block_strips_tool_tags(self):
        c, cap = self._compactor()
        block = [{"role": "assistant", "content": _DIRTY_TOOL_MSG}]
        c._summarize_block(block, "")
        prompt = cap.prompts[0]
        assert "<tool_calls>" not in prompt and "web_search" not in prompt
        assert "先查一下" in prompt and "再回答。" in prompt

    def test_fallback_summary_cleaned(self):
        """降级摘要同口径清洗（同类一并修）。"""
        c, _cap = self._compactor()
        text = c._fallback_summary([{"role": "assistant", "content": _DIRTY_ASSISTANT}])
        assert "[card:" not in text and "解读版本" not in text
        assert "你生于丙子年腊月" in text

    def test_compact_end_to_end_cleaned(self):
        """compact 全链：喂 LLM 的块文本已清洗（不落库、只读）。"""
        cap = _CaptureLLM(_SUMMARY_OUT)
        # 小窗口 → 触发分块（尾部保留 + 旧消息进压缩块）
        c = MemoryCompactor(api_key="k", llm_fn=cap, window_limit=512)
        msgs = [{"role": "assistant", "content": _DIRTY_ASSISTANT}] * 20
        res = c.compact(msgs)
        assert res.old_count > 0, "夹具前提：必须有旧消息进压缩块"
        assert res.summary_text
        assert cap.prompts, "应发生压缩调用"
        joined = "\n".join(cap.prompts)
        assert "[card:" not in joined and "解读版本" not in joined
