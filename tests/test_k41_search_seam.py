# -*- coding: utf-8 -*-
"""k41（T104 收尾）：**检索判定与回复路径解耦** —— 该搜的先搜、上下文注入、来源体现。

需求 SSOT = `.superpowers/sdd/task-k41-brief.md` §1
（归因证据 = `.superpowers/sdd/e6-red-triage-round2-20260913.md` §2.T104）

改前（实锤）：自动检索只挂在 `process()` 的 `_will_polish` 块内，该块条件含
`intent not in ("xuetang","advisor")` → LLM 把实体问句判成 advisor 时整块跳过：
**检索根本没发生**，回复里既无实体名也无来源痕迹（T104 L2 断言
`contains: ["易宝支付","来源"]` 全灭）。

改后口径（控制方拍板）：`decide_search` 判「该搜」（尤其 `reason=entity`）时，
无论后续走 advisor / bazi / free_chat / 其它路径，都在**路由前**检索一次，
结果注入该路径的上下文，并在回复出口统一补来源尾注。

红线（本文件的「不该搜」半边）：`decide_search` 既有语义（层 1-4 + 硬锚否决）
**不得放宽** —— 本项是把「该搜的搜到」，不是「多搜」。

双向用例：① 实体强问词必搜；② 本地命理问句必不搜（同一 advisor 路径下反向锁）。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import src.bot.handler as handler_mod  # noqa: E402
from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import MessageAnalysis  # noqa: E402
from src.rag.search_trigger import decide_search  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

# 评测语料逐字（data/eval/agent_tasks.jsonl）
T104_MSG = "易宝支付这家公司靠不靠谱？我正考虑换工作过去，我的盘你之前排过。"
# 「实体只是背景」的本地命理问句（k11b 硬锚否决的定义形态）
LOCAL_MSG = "在易宝支付上班，今年运势怎么样"
# 无命名实体的本地命理问句
LOCAL_NOENTITY_MSG = "帮我看看今年的流年运势"

_FAKE_RESULTS = [{
    "title": "易宝支付有限公司 - 官网",
    "url": "https://www.example-news.com/yibao",
    "text": "易宝支付是一家持牌第三方支付机构，2011 年获得支付牌照，总部在北京。",
}]

_TMP_DIRS = []


def _db_path():
    d = tempfile.mkdtemp(prefix="fortune_k41_seam_")
    p = os.path.join(d, "t.db")
    init_db(p)
    _TMP_DIRS.append(d)
    return p


def _mock_result_engine(gender="男"):
    r = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                   "liunian", "liunian_full", "geju", "yongshen",
                   "shensha", "nayin", "gender"])
    r.bazi = ["庚午", "辛巳", "乙酉", "甲申"]
    r.day_master = "乙木"
    r.wuxing = {"木": 3, "火": 2, "金": 2, "水": 1, "土": 2}
    r.shishen = ["正官", "七杀", "正财", "偏印"]
    r.dayun = [(4, "庚辰"), (14, "己卯"), (24, "戊寅")]
    r.liunian = {"2026": "庚午", "2027": "辛未"}
    r.liunian_full = []
    r.geju = "七杀格"
    r.yongshen = "木"
    r.shensha = ["天乙贵人"]
    r.nayin = ["路旁土", "白蜡金", "泉中水", "井泉水"]
    r.gender = gender
    eng = Mock()
    eng.calculate.return_value = r
    return eng


def _seed_person(db_path, user_id, gender="男"):
    PersonDAO(db_path).create_person(
        user_id, "我", "本人",
        birth={"birth_year": 1990, "birth_month": 5, "birth_day": 20,
               "birth_hour": 15, "birth_minute": 30,
               "city": "北京", "gender": gender},
        is_default=True)


def _handler(db_path, analysis_intent=None, needs_search=False):
    """真实 DB 链路 + Mock LLM（零网络）+ 钉死的意图分析（复现 T104 的 advisor 判定）。"""
    from src.engines.hehun import HehunEngine

    llm = Mock()
    llm.api_key = ""            # 空 key → 润色/工具循环门关闭（确定性、零网络）
    llm.model = "test-model"
    llm.provider = "glm"
    llm.chat.return_value = Mock(response="（占位）")
    llm.chat_conversation.return_value = "（占位）"
    llm.analyze.return_value = Mock(response="（占位）")
    h = MessageHandler(
        engine=_mock_result_engine(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        hehun_engine=HehunEngine(), qimen_engine=Mock(),
        retriever=Mock(), llm=llm, dao=UserDAO(db_path),
        session_dao=SessionDAO(db_path))
    h.memory_system = None
    h._quick_flash = lambda prompt, **kw: "（占位）"
    h._start_pregen_instant = lambda msg, user_id="": None
    h._analyze_message = lambda msg, user_id="", session_id=None: MessageAnalysis(
        needs_soothe=False, soothe_text="", emotion_label=None,
        intent=analysis_intent, needs_search=needs_search)
    return h


def _patch_search(monkeypatch):
    """钉死联网检索通道（零网络）：记录 query。"""
    calls = []

    def fake_search(query, limit=5):
        calls.append(query)
        return list(_FAKE_RESULTS)

    monkeypatch.setattr(handler_mod, "search_web", fake_search)
    monkeypatch.setattr(handler_mod, "web_search_available", lambda *a, **k: True)
    return calls


@pytest.fixture
def advisor_env(monkeypatch):
    """advisor 路径：Mock 掉 AdaptiveAdvisor.generate（真实 LLM 面），
    并把「检索结果块」原样回显进建议文案 —— 检索没注入 → 回复里就没有实体名
    （断言因此双向可辨：改前必失败）。"""
    def _build(db_path, intent="advisor", needs_search=False):
        h = _handler(db_path, analysis_intent=intent, needs_search=needs_search)
        seen = {}

        class _FakeAdvisor:
            def generate(self, bazi_result, user_context="", api_key=""):
                seen["user_context"] = user_context
                if "【网络检索结果】" in user_context:
                    block = user_context.split("【网络检索结果】")[1]
                    advice = block.strip().splitlines()[0][:60]
                else:
                    advice = "（无检索结果）"
                return {"actions": [{"category": "事业", "advice": advice,
                                     "timing": "", "confidence": "high",
                                     "concrete_steps": "", "success_metric": ""}],
                        "serendipity": "", "daily_tip": "", "style_notes": ""}

        monkeypatch.setattr(handler_mod, "AdaptiveAdvisor", _FakeAdvisor)
        return h, seen
    return _build


# ================================================================
# 一、判定语义（双向：该搜的判搜 / 不该搜的不判）——红线锁，不得放宽
# ================================================================

def test_decide_search_entity_ask_must_search():
    """该搜：命名实体 + 强实体问词（靠不靠谱）→ should_search，reason=entity。"""
    dec = decide_search(T104_MSG)
    assert dec.should_search is True
    assert dec.reason == "entity"
    assert dec.entity == "易宝支付"


@pytest.mark.parametrize("msg,reason", [
    (LOCAL_NOENTITY_MSG, "local"),      # 硬锚（流年/运势）
    (LOCAL_MSG, "local"),               # 实体只是背景：『在易宝支付上班…运势』
    ("股市行情怎么样", "finance"),        # 金融硬排除
])
def test_decide_search_local_and_finance_never_search(msg, reason):
    """不该搜（红线）：本地命理锚/金融行情一律不触发——解耦不得放宽判定。"""
    dec = decide_search(msg)
    assert dec.should_search is False
    assert dec.reason == reason


# ================================================================
# 二、advisor 路径：先检索 + 注入上下文 + 来源痕迹（T104 主用例）
# ================================================================

def test_advisor_path_searches_and_reply_has_entity_and_source(advisor_env,
                                                               monkeypatch):
    """改前必失败：LLM 判 advisor → 检索不发生（回复无实体名/无来源）。
    改后：路由前先检索 → 检索块注入 advisor 上下文 → 回复体现来源。"""
    db = _db_path()
    _seed_person(db, "u_seam_adv")
    h, seen = advisor_env(db, intent="advisor")
    queries = _patch_search(monkeypatch)

    reply = h.process(T104_MSG, "u_seam_adv", session_id="k41-adv")

    # ① 检索真实发生（query = 实体名）
    assert queries and "易宝支付" in queries[0], queries
    # ② 结果块进了 advisor 的上下文（LLM 才有事实可依）
    assert "【网络检索结果】" in seen.get("user_context", ""), seen
    assert "持牌第三方支付机构" in seen.get("user_context", "")
    # ③ 回复体现实体与来源（尾注为确定性纯文本）
    assert "易宝支付" in reply
    assert "来源" in reply and "example-news.com" in reply


def test_advisor_path_local_question_does_not_search(advisor_env, monkeypatch):
    """反向锁（不得多搜）：同一条 advisor 路径 + 本地命理问句（硬锚「运势」）
    → 零检索、零来源尾注（改前改后一致；与上一条互为双向）。"""
    db = _db_path()
    _seed_person(db, "u_seam_adv2")
    h, seen = advisor_env(db, intent="advisor")
    queries = _patch_search(monkeypatch)

    reply = h.process(LOCAL_MSG, "u_seam_adv2", session_id="k41-adv2")

    assert queries == [], queries
    assert "【网络检索结果】" not in seen.get("user_context", "")
    assert "来源" not in reply


# ================================================================
# 三、free_chat 路径：同一接缝（判该搜即搜，与走哪条回复路径无关）
# ================================================================

def test_free_chat_path_gets_ground_block_and_source(monkeypatch):
    """改前必失败：intent=None（自由聊）时自动检索不挂在 `_will_polish` 块内
    → 检索不发生。改后：接缝在路由前，free_chat 也拿到检索块 + 来源尾注。"""
    db = _db_path()
    _seed_person(db, "u_seam_free")
    h = _handler(db, analysis_intent=None)
    queries = _patch_search(monkeypatch)

    seen = {}

    def fake_free_chat(msg, user_id, **kw):
        seen.update(kw)
        # 模拟 LLM：把注入的检索块消化成正文（没注入 → 正文无实体名）
        hint = kw.get("extra_hint", "") or ""
        if "【网络检索结果】" in hint:
            return "易宝支付：据公开检索信息，它是一家持牌支付机构。"
        return "（无检索）"

    h._free_chat = fake_free_chat
    reply = h.process(T104_MSG, "u_seam_free", session_id="k41-free")

    assert queries and "易宝支付" in queries[0], queries
    assert "【网络检索结果】" in seen.get("extra_hint", "")
    assert "易宝支付" in reply and "来源" in reply


def test_advisor_keyword_branch_also_searches_and_traces(advisor_env,
                                                         monkeypatch):
    """第二入口（「建议/怎么办」关键词早退分支）：同一接缝同样生效。"""
    db = _db_path()
    _seed_person(db, "u_seam_adv3")
    h, seen = advisor_env(db, intent=None)   # 关键词分支优先于 intent
    queries = _patch_search(monkeypatch)

    msg = "易宝支付这家公司靠不靠谱？我该怎么办"
    reply = h.process(msg, "u_seam_adv3", session_id="k41-adv3")

    assert queries and "易宝支付" in queries[0], queries
    assert "【网络检索结果】" in seen.get("user_context", "")
    assert "易宝支付" in reply and "来源" in reply


# ================================================================
# 四、单点实现（防「另写一套」）：一次路由前检索，全路径复用
# ================================================================

def test_process_calls_ground_search_exactly_once(advisor_env, monkeypatch):
    """成本护栏：一轮只检索一次（接缝是**唯一**调用点，各路径消费同一份结果）。"""
    db = _db_path()
    _seed_person(db, "u_seam_once")
    h, _ = advisor_env(db, intent="advisor")
    _patch_search(monkeypatch)

    n = {"calls": 0}
    orig = h._ground_search_for_turn

    def spy(msg, user_id, analysis):
        n["calls"] += 1
        return orig(msg, user_id, analysis)

    h._ground_search_for_turn = spy
    h.process(T104_MSG, "u_seam_once", session_id="k41-once")
    assert n["calls"] == 1, n


def test_k5_polish_gate_ignores_web_only_citations():
    """k5 润色门不变式（k41）：自动检索的 web 引用**不得**把「无引擎产物的轮次」
    （信息收集/错误回复）推进润色路径 —— 该门判据 = 引擎/古籍引用。

    改前该门在自动检索之前求值（检索挂在门内）；接缝上移后必须以非 web 引用
    为准，否则「只有检索结果」的信息收集轮会被 LLM 重写（F2 文案漂移）。"""
    from src.bot.handler import MessageHandler as _MH
    from src.rag.citation import make_citation
    db = _db_path()
    h = _handler(db, analysis_intent=None)
    h._citations["u_gate"] = [make_citation(1, "web", "检索结果", source="网络")]
    assert _MH._has_engine_citations(h, "u_gate") is False
    h._citations["u_gate"].append(make_citation(2, "book", "古籍", source="滴天髓"))
    assert _MH._has_engine_citations(h, "u_gate") is True
    assert _MH._has_engine_citations(h, "u_none") is False


def test_single_ground_search_call_point_in_process():
    """结构锁：process 内只有一处「先检索」调用点（另写第二套即红）。"""
    import inspect
    src = inspect.getsource(MessageHandler.process)
    assert src.count("self._ground_search_for_turn(") == 1
    assert "self._engine_domain_ground_search(" not in src, (
        "process 内不得直调底层实现（复用单一实现）")
