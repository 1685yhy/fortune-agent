# -*- coding: utf-8 -*-
"""k41 合并用例矩阵 runner（数据 = `tests/k41_matrix_cases.py`）。

用途（本批控制方口径）：**一份矩阵**（k40 72 行 + k41 30 条 + 审查 Critical-1 /
Important-1 及其变体 + 本批自造），在三个干净版本上跑同一份文件：
  c4b53ef（k41 HEAD） / ffb5358（k40 终审） / 本批修复版
→ 逐条对照，证明「两版绿 → 本版红 = 0」，且两条缺陷（Critical-1 / Important-1）
在 base/head 上红、本版绿。

三维断言（每个 e2e 行都带）：
  ① persons.gender（档案性别）② chart_records（是否新增排盘）③ hehun 工具调用
自述句（e2e="correct"）另加「纠正生效」断言：重排 + 回执 + 档案双写新性别。
歧义类（kind="ambig"）另加「询问确认」断言（不静默改写、不静默丢弃）。

跑法（`OMP_NUM_THREADS=1`，DB 全落 /tmp）：
    OMP_NUM_THREADS=1 K41_MATRIX_OUT=/tmp/k41m/out.json \
        python3 -m pytest tests/test_k41_matrix.py -q -p no:cacheprovider
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import MessageAnalyzer  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402
from k41_matrix_cases import CASES  # noqa: E402

# 歧义确认问句的稳定片段（handler 侧确定性文案；不放宽为自由文本）
ASK_FRAGMENT = "另外确认一下"
# 记忆点：k41 矩阵行数快照（防「重复项占位」——重复行由下面的唯一性断言拦）
_EXPECT_MIN_ROWS = 140
# 男系口语词（用于给第三人句挑**会暴露 P0 的档案性别**：第三人性别与档案相反时
# 才会触发 G1 改写，否则「没改写」是巧合而非修复）
_MALE_WORDS = ("男孩", "男生", "男的", "男孩子", "小伙子")

_VERDICTS = {}
_TMP_DIRS = []


def _has_oral_word(msg: str) -> bool:
    from src.bot.handler import _ORAL_FEMALE_WORDS, _ORAL_MALE_WORDS
    return any(w in msg for w in _ORAL_FEMALE_WORDS + _ORAL_MALE_WORDS)


def _third_profile(msg: str) -> str:
    """第三人句的档案性别：与消息里的第三人性别**相反**（否则测不出 P0）。"""
    return "女" if any(w in msg for w in _MALE_WORDS) else "男"


def _record(cid, key, value):
    _VERDICTS.setdefault(cid, {})[key] = value


def _db_path():
    d = tempfile.mkdtemp(prefix="fortune_k41_matrix_")
    p = os.path.join(d, "t.db")
    init_db(p)
    _TMP_DIRS.append(d)
    return p


def _mock_result_engine(bazi=None, gender="男"):
    r = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                   "liunian", "liunian_full", "geju", "yongshen",
                   "shensha", "nayin", "gender"])
    r.bazi = bazi or ["庚午", "辛巳", "乙酉", "甲申"]
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


def _seed_person(db_path, user_id, gender="男", year=1990, month=5, day=20,
                 hour=15, minute=30, city="北京"):
    PersonDAO(db_path).create_person(
        user_id, "我", "本人",
        birth={"birth_year": year, "birth_month": month, "birth_day": day,
               "birth_hour": hour, "birth_minute": minute,
               "city": city, "gender": gender},
        is_default=True)


def _handler(db_path):
    from src.engines.bazi import BaziEngine  # noqa: F401
    from src.engines.hehun import HehunEngine

    llm = Mock()
    llm.api_key = ""
    llm.model = "test-model"
    llm.provider = "glm"
    llm.chat.return_value = Mock(response="（占位）")
    llm.chat_conversation.return_value = "（占位）"
    llm.analyze.return_value = Mock(response="（占位）")
    h = MessageHandler(
        engine=_mock_result_engine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(),
        hehun_engine=HehunEngine(),
        qimen_engine=Mock(),
        retriever=Mock(), llm=llm, dao=UserDAO(db_path),
        session_dao=SessionDAO(db_path))
    h.memory_system = None
    h._quick_flash = lambda prompt, **kw: "（占位）"
    h._start_pregen_instant = lambda msg, user_id="": None
    h._free_chat = Mock(return_value="（占位·自由对话）")
    _analyzer = MessageAnalyzer(api_key=None)
    h._analyze_message = (
        lambda msg, user_id="", session_id=None: _analyzer.analyze(msg))
    return h


def _spy_tool_calls(h):
    seen = []
    orig = h._execute_tool_call

    def spy(name, params, user_id, user_question=""):
        seen.append((name, params))
        return orig(name, params, user_id, user_question=user_question)
    h._execute_tool_call = spy
    return seen


@pytest.fixture(scope="session", autouse=True)
def _dump_verdicts():
    yield
    out = os.environ.get("K41_MATRIX_OUT")
    if out:
        Path(out).write_text(
            json.dumps(_VERDICTS, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")


# ================================================================
# 矩阵自检：唯一标识 + 无重复项占位（Critical-1 点名的矩阵缺口）
# ================================================================

def test_matrix_has_unique_ids_and_messages():
    cids = [c.cid for c in CASES]
    msgs = [c.msg for c in CASES]
    assert len(cids) == len(set(cids)), "cid 必须唯一"
    dupes = sorted({m for m in msgs if msgs.count(m) > 1})
    # 允许的重复：k40/k41 两份矩阵分别在各自文件里登记过的同句（E2E 与提取层同句），
    # 其余一律视为「重复项占位」，必须唯一。
    allowed_dupes = {
        "房东的女儿1990年出生的女孩子，我们合不合",
        "邻居家女儿1990年出生的女孩子，我们合不合",
        "朋友介绍的女孩子1990年出生的，我们合不合",
        "我是女孩儿，不是男孩",
        "我是一个女孩",
        "我的出生信息是1990年5月20日 15:30 北京 男",
        "我的资料：1990年5月20日 15:30 北京 男",
        "我的命盘 1990年5月20日 15:30 北京 男",
        "帮我排盘，我的出生信息是1990年5月20日 15:30 北京 男",
        "帮我排个盘：1990年5月20日 15:30 北京 男",
        "帮我排盘，1990年5月20日 15:30 北京 男",
        "我想改个名，姓李，男，1988年8月8日 8:00 北京出生",
        "帮我起个名，姓刘，女孩，2020年6月1日 10:00 上海出生",
        "我其实是个男的",
        "我是女生",
        # k40 自身在 `_LEAK_VARIANTS` 与 P0 列表里重复登记的两条（合并矩阵如实保留
        # 两处来源：一处锁提取层/守卫，一处锁 P0 提取层）——非「重复项占位」。
        "我女朋友1990年生，她说她是女生",
        "我朋友1990年5月20日出生的女生，帮我看看",
    }
    unexpected = [m for m in dupes if m not in allowed_dupes]
    assert not unexpected, f"重复项占位：{unexpected}"
    assert len(CASES) >= _EXPECT_MIN_ROWS, (
        f"矩阵被削薄（{len(CASES)} < {_EXPECT_MIN_ROWS}）——禁止重复项占位/漏行")


# ================================================================
# 一、提取层 + 守卫（全部行）
# ================================================================

@pytest.mark.parametrize("case", CASES, ids=[c.cid for c in CASES])
def test_matrix_expectations(case):
    h = object.__new__(MessageHandler)
    got = (h._extract_partial_birth(case.msg) or {}).get("gender")
    third = h._gender_ref_is_third_party(None, case.msg)
    _record(case.cid, "msg", case.msg)
    _record(case.cid, "kind", case.kind)
    _record(case.cid, "extract", got)
    _record(case.cid, "guard", third)

    if case.kind == "third":
        assert got is None, f"[{case.cid}] 第三人性别被取作本人：{case.msg}"
        # 无口语性别词的第三人句（如「对方女儿1990年出生的…」「这人…」）本就没有
        # 「性别声明」可取（改前即 None，无 P0 面）→ 守卫按定义不判（与 k41 用例同口径）。
        if _has_oral_word(case.msg):
            assert third is True, f"[{case.cid}] 守卫未判第三人：{case.msg}"
    elif case.kind == "self":
        assert got == (case.gender or None), (
            f"[{case.cid}] 本人自述性别丢失：{case.msg} → {got}")
        assert third is False, f"[{case.cid}] 自述被误判第三人：{case.msg}"
    else:  # ambig：结构不可辨 → 不取（不改写/不写画像），由询问确认兜底
        assert got is None, f"[{case.cid}] 歧义性别被取作本人：{case.msg}"
        assert third is True, f"[{case.cid}] 歧义类未拦（会被静默改写）：{case.msg}"


# ================================================================
# 二、端到端三维断言（档案性别 / chart / hehun；自述行加「纠正生效」）
# ================================================================

_E2E_CASES = [c for c in CASES if c.e2e != "none"]


@pytest.mark.parametrize("case", _E2E_CASES, ids=[c.cid for c in _E2E_CASES])
def test_matrix_e2e(case):
    db = _db_path()
    uid = "m_" + case.cid
    profile = "男"
    if case.e2e == "correct":
        profile = "女" if case.gender == "男" else "男"
    elif case.e2e in ("hehun", "none", "ambig"):
        # 第三人句：档案性别取「与第三人相反」→ 一旦守卫失效必然暴露 P0 改写
        profile = _third_profile(case.msg)
    _seed_person(db, uid, gender=profile)
    h = _handler(db)
    calls = _spy_tool_calls(h)

    reply = h.process(case.msg, uid, session_id="mx-" + case.cid)

    g = PersonDAO(db).get_default_person(uid)["gender"]
    chart = ChartDAO(db).get_latest_chart(uid) is not None
    hehun = any(c[0] == "合婚" for c in calls)
    _record(case.cid, "profile_in", profile)
    _record(case.cid, "profile_after", g)
    _record(case.cid, "chart", chart)
    _record(case.cid, "hehun", hehun)
    _record(case.cid, "ack_repair", "重新排盘" in reply)
    _record(case.cid, "ask", ASK_FRAGMENT in reply)

    if case.e2e == "hehun":
        assert g == profile, f"[{case.cid}] 档案性别被改写：{case.msg}"
        assert chart is False, f"[{case.cid}] 第三人句写了本人 chart：{case.msg}"
        assert hehun, f"[{case.cid}] 合婚工具未被调用：{case.msg}"
    elif case.e2e == "correct":
        assert "重新排盘" in reply, f"[{case.cid}] 自述纠正无回执：{case.msg}"
        assert chart is True, f"[{case.cid}] 自述纠正未重排：{case.msg}"
        assert g == case.gender, (
            f"[{case.cid}] 自述纠正未双写档案：{case.msg} → {g}")
    elif case.e2e == "ambig":
        assert g == profile, f"[{case.cid}] 歧义句静默改写档案：{case.msg}"
        assert ASK_FRAGMENT in reply, (
            f"[{case.cid}] 歧义句未回询问确认（静默丢弃/改写）：{case.msg}")
        if "合" in case.msg:
            assert hehun, f"[{case.cid}] 合婚工具未被调用：{case.msg}"
    else:  # none：第三人句只锁 gender 维（chart/hehun 维不适用）
        assert g == profile, f"[{case.cid}] 档案性别被改写：{case.msg}"
