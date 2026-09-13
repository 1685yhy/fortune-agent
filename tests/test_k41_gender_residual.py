# -*- coding: utf-8 -*-
"""k41：性别守卫**末族**（无代词领属/修饰名词短语）收口 —— 规则级，不新增称谓词表。

需求 SSOT = `.superpowers/sdd/task-k41-brief.md` §2
（登记与修法建议 = `.superpowers/sdd/task-k40-review.md` §六「残余暴露」）

改前（k40 终审实测、`e45226b` 起既有、三版一致）：`房东的女儿1990年出生的
女孩子，我们合不合` / `邻居家女儿1990年出生的女孩子，我们合不合` /
`朋友介绍的女孩子1990年出生的，我们合不合` 这类**无代词**的领属/修饰名词短语
仍把消息里的性别取作**用户本人**性别 → G1 守卫改写档案（gender 男→女 + 写
chart + hehun 零调用）。

修法（k40 终审给的规则级口径）：子句句首为**非自述**的名词性修饰/领属短语
（`X的N` / `X家N` / `X介绍(的)N`，X 非 我/你/咱/俺/您/本人/自己）且中心语不是
自述数据名词（`_GENDER_SELF_DATA_NOUN`）→ 判第三人。**不新增称谓词表**。

三维断言（改前必失败）：`persons.gender` 零改写 / `chart_records` 零新增 /
`hehun` 工具正确调用。反向：T008 与自述族（我是女孩/我的出生信息是…男）一律
仍取本人性别（k40 矩阵逐条锁）。
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

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import MessageAnalyzer  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

# 终审 §六 点名的 e2e 3 条（逐字）
E2E_RESIDUAL = [
    "邻居家女儿1990年出生的女孩子，我们合不合",
    "房东的女儿1990年出生的女孩子，我们合不合",
    "朋友介绍的女孩子1990年出生的，我们合不合",
]
# 终审 §六 登记的提取层同族（同一规则关严，非逐条词表）
EXTRACT_RESIDUAL = [
    "同事介绍的女孩子1990年出生的，我们合不合",
    "别人介绍的女孩子1990年出生的，我们合不合",
    "家里介绍的女孩子1990年出生的，我们合不合",
    "媒人介绍的女孩子1990年出生的，我们合不合",
    "相亲的女孩子1990年出生的，我们合不合",
    "双方家长介绍的女孩子1990年出生的，我们合不合",
    "房东的女儿1990年出生的女孩子，我们合不合",
    "邻居家女儿1990年出生的女孩子，我们合不合",
    "朋友介绍的女孩子1990年出生的，我们合不合",
    "对方女儿1990年出生的，我们合不合",
    "这人1990年出生的，我们合不合",
]

_TMP_DIRS = []


def _db_path():
    d = tempfile.mkdtemp(prefix="fortune_k41_gender_")
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
    """与 k40 同型：真实 persons/DB 链路 + Mock LLM（零网络）+ 确定性 analyzer。"""
    from src.engines.bazi import BaziEngine
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


# ================================================================
# 一、提取层：无代词领属/修饰短语一律不取本人性别（改前必失败）
# ================================================================

def _has_oral_gender_word(msg: str) -> bool:
    from src.bot.handler import _ORAL_FEMALE_WORDS, _ORAL_MALE_WORDS
    return any(w in msg for w in _ORAL_FEMALE_WORDS + _ORAL_MALE_WORDS)


@pytest.mark.parametrize("msg", EXTRACT_RESIDUAL)
def test_residual_family_takes_no_gender(msg):
    """终审 §六 同族：提取层 `gender` 必须为 None（改前 9/11 取到 女）。

    守卫断言只对**含口语性别词**的形态适用：无性别词时消息本就没有「性别声明」
    可取（改前即 None，无 P0 面），守卫按定义不判（`_gender_ref_is_third_party`
    只在「存在性别词」时回答归属）。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None, msg
    if _has_oral_gender_word(msg):
        assert h._gender_ref_is_third_party(None, msg) is True, msg


def test_residual_rule_is_not_a_title_wordlist():
    """规则级（非词表）：把称谓换成词表外的**任意**名词，同一规则仍成立。"""
    h = object.__new__(MessageHandler)
    for x in ("发小的表姐", "楼下水果店的老板娘", "我教练的学员", "医生家的孩子"):
        msg = f"{x}1990年出生的女孩子，我们合不合"
        assert (h._extract_partial_birth(msg) or {}).get("gender") is None, msg


# ================================================================
# 二、端到端三维断言：档案零改写 / 排盘零新增 / 合婚正确调用
# ================================================================

@pytest.mark.parametrize("msg", E2E_RESIDUAL)
def test_residual_family_process_keeps_archive_and_hehun(msg):
    """改前必失败（终审实测：gender 男→女 + 写 chart + hehun 零调用）。"""
    db = _db_path()
    uid = "u_k41_%d" % (abs(hash(msg)) % 100000)
    _seed_person(db, uid, gender="男")
    h = _handler(db)
    calls = _spy_tool_calls(h)

    h.process(msg, uid, session_id="k41-res-" + uid)

    assert PersonDAO(db).get_default_person(uid)["gender"] == "男", msg
    assert ChartDAO(db).get_latest_chart(uid) is None, msg
    assert any(c[0] == "合婚" for c in calls), (msg, [c[0] for c in calls])


def test_residual_rule_unit_level_subject():
    """规则单元（①判第三人 / ②自述仍判本人），双向可辨。"""
    from src.bot.handler import _gender_word_subject
    m1 = "房东的女儿1990年出生的女孩子"
    assert _gender_word_subject(m1, m1.index("女孩子")) == "third"
    m2 = "朋友介绍的女孩子1990年出生的"
    assert _gender_word_subject(m2, m2.index("女孩子")) == "third"
    m3 = "我是女孩儿，不是男孩"
    assert _gender_word_subject(m3, m3.index("女孩")) == "self"
    m4 = "我是一个女孩"
    assert _gender_word_subject(m4, m4.index("女孩")) == "self"


# ================================================================
# 三、反向锁（不得误伤）：自述族 / 邻接锁仍取本人性别
# ================================================================

@pytest.mark.parametrize("msg,expect", [
    # T008 双向锁
    ("我是女孩儿，不是男孩", "女"),
    ("我是女生", "女"),
    ("我是一个女孩", "女"),
    ("我其实是个男的", "男"),
    # 自述数据名词（领属语中心语是「关于我的信息」→ 仍是本人）
    ("我的出生信息是1990年5月20日 15:30 北京 男", "男"),
    ("我的资料：1990年5月20日 15:30 北京 男", "男"),
    ("我的命盘 1990年5月20日 15:30 北京 男", "男"),
    # 邻接锁（评测集实测）：本人请求不得被当成「第三人领属语 + 出生信息」
    ("帮我排盘，我的出生信息是1990年5月20日 15:30 北京 男", "男"),
    ("帮我排个盘：1990年5月20日 15:30 北京 男", "男"),
    ("帮我排盘，1990年5月20日 15:30 北京 男", "男"),
    ("我想改个名，姓李，男，1988年8月8日 8:00 北京出生", "男"),
    ("帮我起个名，姓刘，女孩，2020年6月1日 10:00 上海出生", "女"),
])
def test_self_family_still_takes_own_gender(msg, expect):
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") == expect, msg
    assert h._gender_ref_is_third_party(None, msg) is False, msg


def test_registered_conservative_boundary_kept():
    """登记边界不回退：同句既提对方出生信息又自述性别 → 性别一律不取。"""
    h = object.__new__(MessageHandler)
    msg = "我是女的，我男朋友1990年生的"
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None, msg
    assert h._gender_ref_is_third_party(None, msg) is True, msg


def test_self_declaration_process_still_rewrites_profile():
    """端到端反向锁：档案男 + 「我是女孩儿，不是男孩」→ G1 纠正仍生效。"""
    db = _db_path()
    _seed_person(db, "u_k41_t008", gender="男")
    h = _handler(db)
    reply = h.process("我是女孩儿，不是男孩", "u_k41_t008", session_id="k41-t008")

    assert "女" in reply and "重新排盘" in reply
    assert ChartDAO(db).get_latest_chart("u_k41_t008") is not None
    assert PersonDAO(db).get_default_person("u_k41_t008")["gender"] == "女"
