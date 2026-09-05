"""k7 卡尾单稿流根治（2026-09-05，18:47 真实请求重放实证：排盘回复两遍）：
- 第 1 遍 = 正文实时流（852 字 LLM delta，无卡无图）；
- 第 2 遍 = 收尾把整张卡片完整重发（981 字 = [card:paipan…]\n + 正文 + 卡尾）。

产品已拍板「末尾图卡」：正文只流一遍，流完收尾只补发卡壳头 + 图行 +
卡闭合 + 页脚（正文绝不重发）。

三处后端改动（全部在 src/api/chat_stream.py）：
1. events() chunk 收集键名归一：payload.get("content") or payload.get("text")
   （旧代码只认 "text"；SSE 出口事件键为 content，两种写法都收得到——
   漏收 → chunk_texts 恒空 → streamed_text 恒空 → 收尾无条件整段补发）；
2. compute_stream_remaining 中段命中分支：reply = 卡壳 + 正文 + 卡尾、
   正文已完整流出 → 镜像 k5 前缀查找（streamed 最长前缀在 reply 中作连续
   子串，≥90% 全长）→ 只补壳头 + 壳尾（k5 前缀查找因 reply 前缀是卡壳
   恒不中，旧逻辑落兜底整段重发 = 双份）；
3. split_sentences 逐字保真：捕获组分句保留句末标点后内容（旧实现逐段
   strip 吞掉段间空行/句末换行，981→969 丢 12 字符，前端 body 与实时流
   逐字失配、卡片去重认不出重复）。

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_chat_stream_k7.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.api.chat_stream import compute_stream_remaining  # noqa: E402
from src.api.chat_stream import split_sentences  # noqa: E402

# ================================================================
# 公共夹具（对齐任务书测试契约的逐字形态）
# ================================================================

# 排盘卡壳（27 字符级：标题属性 + 换行）
_CARD_HEAD = '[card:paipan title="我的命盘"]\n'
# 卡尾：图行 + 卡闭合 + 页脚
_CARD_TAIL = '\n\n📊 命盘图片:http://x/y.png\n[/card]\n\n———\n页脚'

# 正文（≥100 字、含句末标点，模拟真实排盘正文）
_BODY = (
    "你生于丙子年腊月，四柱官印相生，身强任财。"
    "大运逆行入财乡，中年以后积蓄渐丰，宜稳不宜急。"
    "流年逢驿马冲动，今年秋冬有出行之象，动静之间自有转机。"
    "凡事先谋而后动，遇贵人点拨可成大事，切记守正出奇。"
    "目下正逢运势上升之期，把握时机稳步前行，自可水到渠成。"
)
# 卡场景完整 reply（= 落库定稿形态）
_REPLY_CARD = _CARD_HEAD + _BODY + _CARD_TAIL


# ================================================================
# 1) compute_stream_remaining：卡场景 + 中段命中 + 回归矩阵
# ================================================================
class TestComputeStreamRemainingK7:
    """卡场景（根因②）与 k5/历史回归（改动 2 不得破坏既有语义）。"""

    def test_card_wrapped_body_streamed_refills_shell_only(self):
        """卡场景核心（任务书契约 #1）：reply = 卡壳+正文+卡尾，
        streamed = 正文（已完整实时流出）→ 只补壳头+壳尾，不含 BODY。"""
        remaining = compute_stream_remaining(_REPLY_CARD, _BODY)
        expected = _CARD_HEAD + _CARD_TAIL
        print(f"card scene: remaining={remaining!r}")
        assert remaining == expected
        assert _BODY not in remaining  # 正文绝不重发

    def test_whole_reply_streamed_returns_empty(self):
        """全流出回归（任务书契约 #2）：reply == streamed → 返回 ""。"""
        assert compute_stream_remaining(_REPLY_CARD, _REPLY_CARD) == ""

    def test_prefix_plus_tail_append_returns_tail(self):
        """前缀+尾追加回归（任务书契约 #3）：reply = 正文+"\n\n📊图"，streamed
        = 正文 → 只补尾部追加（k5 covered 分支，改动 2 不得回退）。"""
        reply = _BODY + "\n\n📊 命盘图片：http://x.example/114949.png"
        remaining = compute_stream_remaining(reply, _BODY)
        print(f"tail-append scene: remaining={remaining!r}")
        assert remaining == "\n\n📊 命盘图片：http://x.example/114949.png"

    def test_empty_stream_returns_full_reply(self):
        """整段兜底回归：streamed="" → 返回 reply 全文（降级/非流式）。"""
        assert compute_stream_remaining(_REPLY_CARD, "") == _REPLY_CARD

    def test_no_overlap_long_streamed_returns_full_reply(self):
        """整段兜底回归：正文从未流出（streamed 与 reply 无 ≥90% 公共子串，
        含首字符不在 reply → 中段查找自然不中）→ 返回 reply 全文。"""
        streamed = "草稿流与润色稿开头完全不同。这只是一段草稿尾巴内容。"
        assert compute_stream_remaining(_REPLY_CARD, streamed) == _REPLY_CARD

    def test_tail_distortion_within_8pct_mid_hit_no_char_lost(self):
        """失真容差（任务书契约 #5）：streamed = 正文尾部替换最后一句
        （差异 5 字 / 全 121 字 ≈ 4.1% ≤ 8%）→ 命中中段分支，返回不丢
        reply 任何字符（允许正文尾最后一句重叠重发一次）。"""
        # 差异句与原句同长（5 字），不改变总长——纯尾部差异
        drift = _BODY[:-5] + "徐徐图之。"
        remaining = compute_stream_remaining(_REPLY_CARD, drift)
        print(f"drift scene: remaining tail={remaining[-30:]!r}")
        # 只补：壳头 + 正文最后一句（未流出的差异尾）+ 卡尾——不整段重发
        assert remaining == _CARD_HEAD + _BODY[-5:] + _CARD_TAIL
        assert remaining != _REPLY_CARD  # 命中 mid 分支（非整段兜底）

    def test_empty_guards(self):
        assert compute_stream_remaining("", "任意流内容") == ""
        assert compute_stream_remaining("有内容。", "") == "有内容。"


# ================================================================
# 2) split_sentences：逐字保真契约（根因③）
# ================================================================
class TestSplitSentencesLossless:
    """契约：`"".join(split_sentences(t)) == t` 逐字相等；每块 ≤48；
    剔除的只有真空块（不 strip）；句末标点内容/段间空行/句末换行不丢。"""

    def test_join_roundtrip_mixed_punctuation(self):
        """任务书契约：≥200 字含「。！？…\n\n」混合文本 →
        join == 原文 且 每块 ≤48。"""
        t = (
            "你生于丙子年腊月，四柱官印相生，身强任财！\n\n"
            "大运逆行入财乡，中年以后积蓄渐丰。宜稳不宜急？此事更需谨慎！\n"
            "流年逢驿马冲动，今年秋冬有出行之象……动静之间自有转机；\n\n"
            "凡事先谋而后动，遇贵人点拨可成大事，切记守正出奇。\n"
            "目下正逢运势上升之期，把握时机稳步前行，自可水到渠成！\n\n"
            "另有文昌入命之兆，利于文书学业，逢考必有所获；"
            "然财帛宫逢天喜，桃花亦盛，需守心明志方不为浮华所动。\n"
            "十月之后贵人星显，凡谋多成，谨记谦逊待人以纳福泽。"
        )
        assert len(t) >= 200, len(t)
        blocks = split_sentences(t)
        joined = "".join(blocks)
        print(f"mixed: n_blocks={len(blocks)} lens={[len(b) for b in blocks]}")
        assert joined == t  # 逐字相等（不吞任何字符）
        assert all(len(b) <= 48 for b in blocks)

    def test_long_sentence_hard_cut_join_roundtrip(self):
        """超长单句（无句末标点，>48 字）：48 硬切只断块不丢字，
        join 仍还原原文；块数 >1。"""
        t = "长" * 200
        blocks = split_sentences(t)
        assert "".join(blocks) == t
        assert all(len(b) <= 48 for b in blocks)
        assert len(blocks) == 5  # 48*4=192 + 8

    def test_sentence_delimiter_stays_in_block(self):
        """句末分隔符留在所属句子块尾（分句语义不变）。"""
        assert split_sentences("你好。") == ["你好。"]
        assert split_sentences("第一句。第二句！") == ["第一句。", "第二句！"]

    def test_trailing_delimiter_and_edges(self):
        """以句末标点收尾（旧实现吞尾换行/空块）→ join 仍还原。"""
        assert "".join(split_sentences("好的。")) == "好的。"
        assert split_sentences("") == []
        assert "".join(split_sentences("\n\n")) == "\n\n"  # 纯空行不丢

    def test_whitespace_only_blocks_not_dropped(self):
        """剔除的只有真空块：含空白的块（"   "）保留，join 还原原文。"""
        t = "第一句。\n\n第二句。   "
        blocks = split_sentences(t)
        assert "".join(blocks) == t
        assert all(len(b) <= 48 for b in blocks)


# ================================================================
# 3) events() 集成：payload 键（text/content 两形态）+ 收尾只补壳
# ================================================================
import asyncio  # noqa: E402
from src.api.chat_stream import ChatStreamer  # noqa: E402

_UID_K7 = "u_k7_card_tail"
# 实时流按句分块（生产 LLM delta 形态）
_LIVE_PIECES = [
    "你生于丙子年腊月，四柱官印相生，身强任财。",
    "大运逆行入财乡，中年以后积蓄渐丰，宜稳不宜急。",
    "流年逢驿马冲动，今年秋冬有出行之象，动静之间自有转机。",
    "凡事先谋而后动，遇贵人点拨可成大事，切记守正出奇。",
    "目下正逢运势上升之期，把握时机稳步前行，自可水到渠成。",
]
_LIVE_BODY = "".join(_LIVE_PIECES)
assert _LIVE_BODY == _BODY  # 夹具自检：流 = 卡内正文（防静默装配错误）


class _K7Handler:
    """模拟 handler.process：正文以指定 payload 键实时流（queue 路径），
    返回 = 卡壳+正文+卡尾 的定稿 reply（落库形态）。

    key="text"：生产实际形态（handler.py/llm/client.py 全部按 {"text":…}
    发出；SSE 出口 _to_event 再渲染成 content 事件键）；
    key="content"：任务书实录阅读的形态——改动 1 后收集层两键都收
    （出口渲染 _to_event 本次契约外仍只认 text，故 content 键正文块的
    出口内容为空串，属于既有渲染行为，测试不在此断言出口正文）。
    """

    def __init__(self, key: str = "text"):
        self.key = key
        self.session_dao = None

    def pop_citations(self, uid):
        return []

    def gen_suggestions(self, uid, q, r):
        return []

    def process(self, message, user_id, stream_cb=None, deep_night=False,
                session_id=None, downgraded=False):
        if stream_cb is not None:
            for piece in _LIVE_PIECES:
                stream_cb("chunk", {self.key: piece})
        return _REPLY_CARD


class _K7MemberDAO:
    def check_quota(self, uid):
        return True

    def use_quota(self, uid):
        pass

    def get_membership(self, uid):
        return {"plan": "free"}


class _K7Req:
    message = "能重新帮我排个盘吗"
    message_type = "text"
    voice_text = ""
    image_url = ""
    deep_night = False
    session_id = None
    user_id = None


async def _collect(events_out: list, payload_key: str = "text"):
    st = ChatStreamer(
        handler=_K7Handler(key=payload_key),
        member_dao=_K7MemberDAO(),
        dao=None, sanitizer=None, auditor=None, validator=None,
        chat_quota_dao=None,
        ping_interval=15.0, chunk_gap_timeout=60.0,
        simulation_delay=0.0,
    )
    async for evt in st.events(_K7Req(), None,
                               {"method": "member", "user_id": _UID_K7}):
        events_out.append(evt)


class TestEventsSingleStream:
    """端到端（改动 2+3，生产形态 payload={"text":…}）：正文实时流只出
    一遍，收尾只补壳头+壳尾（不再整卡重发 = 双份）。"""

    def test_body_streamed_once_tail_refills_shell_only(self):
        events = []
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_collect(events, payload_key="text"))
        finally:
            loop.close()

        chunks = [e["content"] for e in events if e["type"] == "chunk"]
        text = "".join(chunks)
        # 无真空 chunk；正文实时块原样可见
        assert all(chunk for chunk in chunks)
        # 最终结构 = 正文文本 + 壳头 + 卡尾（正文只一遍，收尾不重发）
        assert text == _LIVE_BODY + _CARD_HEAD + _CARD_TAIL, repr(text[:80])
        # 正文恰好出现一次（修复前：卡壳前缀使 k5 前缀查找恒不中 → 收尾
        # 整卡重发 → 正文出现两次 = 用户看到两遍）
        assert text.count(_LIVE_BODY) == 1
        # 事件协议骨架：start 先行、done 收尾（done 携带定稿全文 = 落库同文）
        assert events[0]["type"] == "start"
        done = events[-1]
        assert done["type"] == "done"
        assert done["content"] == _REPLY_CARD


class TestEventsContentKeyCollection:
    """改动 1（任务书可选契约 #3）：content 键 chunk 也要收得进收集逻辑
    → streamed_text 非空 → 收尾只补壳头+壳尾，不再整卡重发。"""

    def test_content_key_payloads_collected_shell_only_refill(self):
        events = []
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_collect(events, payload_key="content"))
        finally:
            loop.close()

        chunks = [e["content"] for e in events if e["type"] == "chunk"]
        text = "".join(chunks)
        # 修复前（收集只认 text → 恒空 → 收尾整卡重发）：text 含正文一遍
        # + 卡壳 + 正文 + 卡尾（正文出现在整卡重发里）；修复后：正文已收进
        # streamed → 收尾只补壳 → text = 卡壳 + 卡尾（无正文）。
        # 注：正文实时块出口内容为空串是 _to_event 渲染只认 text 的既有
        # 行为（本次契约外），故只断言收尾产物。
        assert text == _CARD_HEAD + _CARD_TAIL, repr(text[:120])
        assert _LIVE_BODY not in text  # 收集成功 → 正文未在收尾重发
