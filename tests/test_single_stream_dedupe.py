"""k5 单稿流修复（2026-09-04，用户实锤一条 AI 回复显示两遍）：

现象：草稿叙事 + 润色定稿两次全文灌同一条 SSE 流 + 收尾整段重发 →
用户看到同一回复两遍（数据库只存定稿，屏幕与回看对不上）。
用户拍板：按豆包/元宝做法——正文只生成/流出一遍（最终定稿），
引擎分析期间只显示思考状态。

三处改动：
1. handler.py process() 单稿门控：引擎 handler 的草稿 chunk 拦截暂存，
   will_polish（本稿将润色重写）→ 丢弃；否则（本稿即最终稿）→ 按原序 flush。
2. chat_stream.py compute_stream_remaining：covered==0 / 前缀内无句子边界时
   不再无条件整段重发——定稿正文已完整流出过 → 只补流后追加的尾部增量。
3. chat_stream.py done 事件携带定稿全文 content（前端本批不消费，字段先就位）。

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_single_stream_dedupe.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.api.chat_stream import compute_stream_remaining  # noqa: E402

# ================================================================
# 1) compute_stream_remaining 场景矩阵（北极星断言，逐字）
# ================================================================

# 场景 C/D 的定稿长正文（≥20 字符，含干支与句末标点——真实润色正文形态）
_BODY_C = (
    "己卯 己巳 乙丑 辛巳。你的命盘四柱齐整，日主乙木生于巳月，"
    "食伤当令而思虑细腻，做事讲究条理，追求可见的成效。"
)
_BODY_D = "定稿乙全文"


class TestComputeStreamRemainingK5:
    """场景 A-F：模拟真实请求的 streamed/reply 形态。"""

    def test_scene_a_downgraded_empty_stream_returns_full_reply(self):
        """场景A（降级/非流式，现状回归）：streamed="" → 返回 reply 全文。"""
        reply = _BODY_C + "\n\n📊 命盘图片：http://x.example/114949.png"
        remaining = compute_stream_remaining(reply, "")
        print(f"A: streamed='' -> remaining == reply 全文: {remaining == reply}")
        assert remaining == reply

    def test_scene_b_suffix_overlap_tail_only(self):
        """场景B（正常流式+尾部追加，现状回归）：reply="正文ABCD\n\n📊图http://x.png"，
        streamed="正文ABCD" → 返回 "\n\n📊图http://x.png"
        （covered=len(streamed)，前缀内无句子边界但尾部以换行句界开始 → 只补尾部）。"""
        reply = "正文ABCD\n\n📊图http://x.png"
        streamed = "正文ABCD"
        remaining = compute_stream_remaining(reply, streamed)
        print(f"B: reply={reply!r} streamed={streamed!r} -> {remaining!r}")
        assert remaining == "\n\n📊图http://x.png"

    def test_scene_c_polish_body_streamed_covered_zero_tail_only(self):
        """场景C（本 bug 核心·定稿全文已流 + 图后追加，covered==0）：
        reply=长正文+\n\n📊 命盘图片，streamed=欢迎语+同长正文+流后残渣
        （残渣如 tool 标签无分隔残留使流尾与 reply 前缀逐字失配 → covered==0；
        残渣与正文之间不留换行——换行恰是 reply 尾部的首字符，会让前缀命中
        多吞一个 \\n → 断言仍为 reply 尾部增量）→
        必须只返回 "\n\n📊 命盘图片：…"（旧行为返回整个 reply = bug）。"""
        reply = _BODY_C + "\n\n📊 命盘图片：http://x.example/114949.png"
        streamed = "欢迎回来，好久不见。\n\n" + _BODY_C + "<tool_calls>[{残渣}]</tool_calls>"
        remaining = compute_stream_remaining(reply, streamed)
        print(f"C: remaining={remaining!r}")
        assert remaining == "\n\n📊 命盘图片：http://x.example/114949.png"

    def test_scene_d_multipour_residue_tail_only(self):
        """场景D（多稿残留防御）：streamed = "草稿甲全文"+"定稿乙全文"，
        reply = "定稿乙全文"+"\n页脚" → 只返回 "\n页脚"。"""
        reply = _BODY_D + "\n页脚"
        streamed = "草稿甲全文" + _BODY_D
        remaining = compute_stream_remaining(reply, streamed)
        print(f"D: reply={reply!r} streamed={streamed!r} -> {remaining!r}")
        assert remaining == "\n页脚"

    def test_scene_e_body_never_streamed_full_reply(self):
        """场景E（正文从未流出）：streamed="欢迎回来，今天想聊点什么？"（仅 welcome），
        reply=长正文全文 → 返回 reply 全文（整段模拟流式兜底，覆盖润色失败降级场景）。"""
        reply = _BODY_C + "\n\n📊 命盘图片：http://x.example/114949.png"
        streamed = "欢迎回来，今天想聊点什么？"
        remaining = compute_stream_remaining(reply, streamed)
        print(f"E: 正文从未流出 -> remaining == reply 全文: {remaining == reply}")
        assert remaining == reply

    def test_scene_f_whole_reply_streamed_empty(self):
        """场景F（全文已流出）：streamed 含整个 reply → 返回 ""。"""
        reply = _BODY_C + "\n\n📊 命盘图片：http://x.example/114949.png"
        remaining = compute_stream_remaining(reply, reply)
        print(f"F: streamed 含整个 reply -> remaining={remaining!r}")
        assert remaining == ""


class TestComputeStreamRemainingHistoricalRetained:
    """句子边界回溯逻辑（covered>0 分支）原样保留——历史 scripts/test_stream_align.py
    关键用例回归（含句边界截断/句中截断回溯/前缀无边界整段兜底/welcome 前缀）。"""

    def test_sentence_boundary_cut_incremental(self):
        # 句边界处截断 → 仅补剩余句子（现状增量行为保留）
        reply = "今天运势整体不错，宜积极行动。忌冲动消费。"
        assert compute_stream_remaining(
            reply, "前面草稿内容。今天运势整体不错，宜积极行动。") == "忌冲动消费。"

    def test_mid_sentence_cut_backtracks_to_boundary(self):
        # 句子中间截断 → 回溯到「。」，补发完整句子（前缀「忌冲」重叠一次）
        reply = "今天运势整体不错，宜积极行动。忌冲动消费。"
        assert compute_stream_remaining(
            reply, "草稿开头不同。今天运势整体不错，宜积极行动。忌冲") == "忌冲动消费。"

    def test_backtrack_takes_nearest_boundary(self):
        reply = "直接、坦率地说，你最近的运势整体平稳。宜忌如下：忌冲动行事。"
        assert compute_stream_remaining(
            reply, "旧的草稿尾巴。直接、坦率地说，你最近的运势整体平稳。宜忌如下：忌"
        ) == "宜忌如下：忌冲动行事。"

    def test_newline_is_boundary(self):
        reply = "第一段话。\n第二段话内容。"
        assert compute_stream_remaining(
            reply, "草稿尾巴。第一段话。\n第二段话内") == "第二段话内容。"

    def test_covered_zero_short_no_overlap_whole_replay(self):
        # covered==0 且正文从未流出（无 ≥20 字符子串命中）→ 整段重发（保持现状）
        reply = "直接、坦率地说，你最近的运势整体平稳。"
        assert compute_stream_remaining(
            reply, "草稿流与润色稿开头完全不同。这只是一段草稿尾巴。") == reply

    def test_prefix_no_boundary_short_whole_replay(self):
        # 前缀内无句子边界且正文主体长度 <20（短文本无法证明完整流出）→ 整段兜底
        reply = "今天运势整体不错宜积极行动"
        assert compute_stream_remaining(
            reply, "前面。今天运势整体不错宜积极行") == reply

    def test_welcome_prefix_covered_full(self):
        # welcome 前缀场景：streamed = 欢迎语 + reply → 无补发
        reply = "好久不见，最近运势平稳。"
        assert compute_stream_remaining(
            reply, "欢迎回来。\n\n好久不见，最近运势平稳。") == ""

    def test_empty_guards(self):
        assert compute_stream_remaining("", "随便什么") == ""
        assert compute_stream_remaining("有内容。", "") == "有内容。"


# ================================================================
# 2) handler process() 单稿门控（集成级 mock 装配）
# ================================================================

_MSG_DREAM = "昨晚梦见从高处坠落，惊醒后一直心慌"
_UID = "u_k5_gate"
# 草稿（引擎 handler 内部 LLM 实时流——旧行为直接进 SSE 的第二遍源头）
_DRAFT_CHUNKS = [
    "你梦见从高处坠落，醒来后心有余悸。",
    "这通常对应近期工作或生活中的压力与失控感。",
    "综合来看，这是身体在提醒你放慢节奏，不必过度担忧。",
]
_DRAFT_REPLY = "".join(_DRAFT_CHUNKS)
# 定稿（润色 LLM 流——正文唯一来源）
_POLISH_CHUNKS = [
    "得嘞，我明白你的意思——坠落的梦常与失控感有关。",
    "近期压力不小的话，试着把大目标拆小，给自己留点喘息。",
    "命理上这叫「有惊无险」，放宽心即可。",
]
_POLISH_REPLY = "".join(_POLISH_CHUNKS)
_CITATION = {"index": 1, "type": "book", "title": "梦林玄解",
             "text": "梦坠高台而无所伤者，主有惊无险。"}


def _make_engine_handler(handler, register_citations: bool):
    """模拟引擎 handler：经 stream_cb 实时流草稿 chunk + thinking 事件，
    真实 handler 同款行为（注册本轮引用/返回草稿全文）。"""
    def _fake_engine(msg, user_id, stream_cb=None, session_id=None):
        if stream_cb is not None:
            stream_cb("thinking", {"text": "正在排盘…"})
            for txt in _DRAFT_CHUNKS:
                stream_cb("chunk", {"text": txt})
        if register_citations:
            handler._citations[user_id] = [_CITATION]
        return _DRAFT_REPLY
    return _fake_engine


def _fake_polish(msg, user_id, draft, stream_cb=None, extra_hint="",
                 search_hint="", session_id=None):
    """模拟 _polish_with_engine_draft：定稿全文经 stream_cb 实时流出并返回。"""
    if stream_cb is not None:
        for txt in _POLISH_CHUNKS:
            stream_cb("chunk", {"text": txt})
    return _POLISH_REPLY


class _Recorder:
    def __init__(self):
        self.events = []

    def stream_cb(self, evt_type, payload):
        self.events.append((evt_type, payload.get("text", "")))

    def chunk_texts(self):
        return [t for et, t in self.events if et == "chunk"]


def _make_handler(tmp_path):
    """真实 __init__ 装配（llm 全 Mock，api_key 空 → 零网络），
    参照 tests/test_bot.py make_mock_handler + _patch_intent 模式。"""
    from src.storage.models import init_db
    db_path = str(tmp_path / "gate.db")
    init_db(db_path)

    mock_llm = Mock()
    mock_llm.api_key = ""
    mock_llm.model = "deepseek-flash"
    mock_llm.analyze.return_value = Mock(response="分析结果")
    mock_llm.chat.return_value = Mock(response="🔮 回复")
    mock_llm.chat_conversation.return_value = "🔮 回复"
    mock_dao = Mock()
    mock_dao.db_path = db_path
    mock_dao.get_user_bazi.return_value = None
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None

    from src.bot.handler import MessageHandler
    from src.engines.message_analyzer import MessageAnalysis
    h = MessageHandler(
        engine=Mock(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=mock_llm, dao=mock_dao,
        session_dao=mock_session,
    )
    h.memory_system = None
    # 固定意图（测试不发起真实意图分析 LLM）
    h._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent="dream"))
    return h


@pytest.fixture
def dream_executor():
    """把能力注册表中 dream intent 执行器替换为 fake（Capability 为 frozen
    dataclass，生产 bind_executors 用 c.__dict__["executor"] 注入——测试同款
    写法）；测试结束恢复原执行器（全局状态，防跨测试/跨文件污染）。"""
    from src.bot.capability_registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES
               if c.cap_id == "dream" and c.cap_type == "intent")
    original = cap.executor
    injected = []

    def _set(fake):
        cap.__dict__["executor"] = fake
        injected.append(fake)

    yield _set
    cap.__dict__["executor"] = original
    assert injected  # 未被使用说明测试装配失效（防静默空跑）


class TestProcessSingleStreamGate:
    """will_polish=True 草稿 chunk 不流出；False 按原序 flush。"""

    def test_will_polish_true_drops_draft_chunks(self, tmp_path, monkeypatch,
                                                 dream_executor):
        """有引用 → 本稿将润色重写：引擎草稿 chunk 全部拦截丢弃，
        定稿（润色流）原样流出——正文只流一遍。"""
        from src.bot.handler import MessageHandler
        h = _make_handler(tmp_path)
        monkeypatch.setattr(h, "_polish_with_engine_draft", _fake_polish)
        dream_executor(_make_engine_handler(h, True))

        rec = _Recorder()
        reply = h.process(_MSG_DREAM, _UID, stream_cb=rec.stream_cb)

        # 定稿 chunk 完整流出
        assert rec.chunk_texts() == _POLISH_CHUNKS, rec.chunk_texts()
        # 草稿 chunk 一个都没流出（正文唯一来源 = 定稿流）
        for t in _DRAFT_CHUNKS:
            assert t not in rec.chunk_texts()
        # thinking 事件原样透传（引擎分析期间前端仍能看到思考状态）
        assert ("thinking", "正在排盘…") in rec.events
        # 返回值 = 定稿（落库/显示同稿）
        assert _POLISH_REPLY in reply

    def test_will_polish_false_flushes_draft_chunks_in_order(self, tmp_path,
                                                             monkeypatch,
                                                             dream_executor):
        """无引用（信息收集/无引擎产物，本稿即最终稿）→ 暂存草稿按原序
        flush 给真 stream_cb，用户不失内容。"""
        from src.bot.handler import MessageHandler
        h = _make_handler(tmp_path)
        polish_calls = []
        monkeypatch.setattr(
            h, "_polish_with_engine_draft",
            lambda *a, **kw: polish_calls.append(1) or "不应调用润色")
        dream_executor(_make_engine_handler(h, False))

        rec = _Recorder()
        reply = h.process(_MSG_DREAM, _UID, stream_cb=rec.stream_cb)

        # 草稿 chunk 按原序 flush（will_polish=False → 不丢弃）
        assert rec.chunk_texts() == _DRAFT_CHUNKS, rec.chunk_texts()
        assert polish_calls == []  # 无引用 → 不润色
        assert _DRAFT_REPLY in reply  # 屏幕正文与落库一致（本稿即最终稿）

    def test_non_streaming_mode_untouched(self, tmp_path, monkeypatch,
                                          dream_executor):
        """非流式（/api/chat 同步，stream_cb=None）：门控不启用（零包装），
        引擎草稿不经流式出口，润色照常（行为与旧版一致）。"""
        h = _make_handler(tmp_path)
        polish_calls = []
        monkeypatch.setattr(
            h, "_polish_with_engine_draft",
            lambda *a, **kw: polish_calls.append(1) or _POLISH_REPLY)
        dream_executor(_make_engine_handler(h, True))

        reply = h.process(_MSG_DREAM, _UID, stream_cb=None)

        assert polish_calls == [1]  # 有引用 → 仍走润色
        assert _POLISH_REPLY in reply  # 定稿为最终回复（同步出口）
