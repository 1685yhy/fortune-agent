# -*- coding: utf-8 -*-
"""k49-r2：修正 r1 的两条真实路径回归（独立审查 Ring 1/Ring 2）。

R2-1（Critical）r1 把闸门接在**提取层采纳判据**
（`_numeric_date_looks_like_birth`，`_extract_partial_birth`/`_extract_bazi_info`
共用）上 → F2 渐进式出生信息累积被误伤：`5月13日，早上6点` 月日被吞
（20 条现实语料 18 条丢月日），真实路径「历史我今年50岁了 → 用户 5月13日，
10点以后，榆树市，男」不再建档出盘、改为复读追问（死循环）。

R2-2（Important）r1 的"语境必须与候选相邻"把合法真陈述整条丢弃：
`我出生在长春，1991年7月8日`（日期在后一小句）→ 基线建档出盘，r1 静默丢弃。

R2-3（既有同族缺陷）否定句式把**被否定**的年份写进档案：
`我不是1995年生的，是1999年生的` → 档案写成 1995（base/branch 逐字相同）。

修法（本文件锁死）：
- 提取层闸门改**否定式**判"整条消息就是这个日期"（时间/时辰/地点/性别/语气/
  出生语境剥离后无残留），并把"婚期"改由**日期紧后的非出生谓语**挡住
  （`结的婚`/`考试`/`要去出差`）——不再要求语境相邻；
- 豁免回 k48-r3 **消息级**口径；否定/纠正句式取**被肯定**的值
  （`person_dao.is_correction_text` 同源判据）。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零网络零 LLM（降级档）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

# 现实 F2 分步口述语料（审查点名 7 条 + 同族 13 条；括号内是期望月日）
F2_CORPUS = [
    ("5月13日，早上6点", 5, 13),
    ("5月13日 10点", 5, 13),
    ("5月13日辰时", 5, 13),
    ("5月13日晚上8点", 5, 13),
    ("5月13日，男", 5, 13),
    ("5月13日，长春", 5, 13),
    ("5月13日，大概6点左右", 5, 13),
    ("我1976年生的，5月13日，早上6点", 5, 13),
    ("5月13日，10点以后，榆树市，男", 5, 13),
    ("我今年50岁了，5月13日", 5, 13),
    ("5月13日 上午十点 长春 男", 5, 13),
    ("5月13日，子时", 5, 13),
    ("5月13日，大概10点左右，男", 5, 13),
    ("5月13日，女的", 5, 13),
    ("5月13日 上午9点", 5, 13),
    ("我出生在榆树市，5月13日", 5, 13),
    ("5月13日，凌晨三点", 5, 13),
    ("我的生日是5月13日", 5, 13),
    ("5月13日生日", 5, 13),
    ("5月13日，农历", 5, 13),
]

# R2-2 真陈述（日期在后一小句，基线必须建档出盘）
TRUE_LATE_CLAUSE = [
    "我出生在长春，1991年7月8日",
    "我出生在长春，1991年7月8日 女",
    "我出生在长春，是1991年7月8日",
    "我妈说我出生在长春，1991年7月8日",
]

# R2-3 否定/纠正句式（必须取被肯定值）
NEGATION_CASES = [
    ("我不是1995年生的，是1999年生的", 1999),
    ("我不是1995年的，我是1999年的", 1999),
    ("我1995年生的？不是，我1999年生的", 1999),
    ("不是1995年，我1999年生的", 1999),
    ("我记错了，不是1995年，是1999年", 1999),
    ("我不是1995年出生的，我是1999年出生", 1999),
]

# brief B 的 8 条非出生语境消息（r1 战果，r2 必须保住）
NON_BIRTH_MSGS = [
    "我4月5日要去出差",
    "3月8日妇女节快乐，我该送什么",
    "我5月20日有个面试，帮我看看",
    "10月1日国庆想去旅游",
    "我3月28日要交房租",
    "下个月3月8日我朋友结婚",
    "我今年想考公务员，4月5日考试",
    "最近工作压力大，3月8日要述职",
]

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")


def _mk_archive(db_path, user_id="u1", **over):
    from src.storage.person_dao import PersonDAO
    b = {"gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
         "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
         "city": "长春"}
    b.update(over)
    return PersonDAO(db_path).create_person(
        user_id, name="我", relation="自己", is_default=True, birth=b)


def _h(tmp_path, seed=ARCHIVE, user="u1"):
    from src.engines.bazi import BaziEngine
    from src.storage.chart_dao import ChartDAO
    from src.storage.dao import UserDAO
    db = str(tmp_path / "p.db")
    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.ziwei_engine = None
    h.llm = Mock()
    h.llm.api_key = ""
    h.dao = UserDAO(db)
    h.session_dao = None
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.memory = None
    h.memory_system = None
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.member_dao = None
    h._downgraded = {user: True}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._citations = {}
    h._fact_ctx = {}
    h._pregen_instant = {}
    h._consume_pregen_instant = Mock(return_value="")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="[复用档案]")
    if seed:
        _mk_archive(db, user, **seed)
    return h, db


def _snap(db, user_id="u1"):
    from src.storage.person_dao import PersonDAO
    p = PersonDAO(db).get_default_person(user_id)
    return None if not p else (p["birth_year"], p["birth_month"],
                               p["birth_day"], p["city"])


# ════════════════════════════════════════════════════════════════
# R2-1 F2 累积恢复（改前：20 条里 18 条丢月日）
# ════════════════════════════════════════════════════════════════
class TestF2AccumulationRestored:
    @pytest.mark.parametrize("msg,mm,dd", F2_CORPUS)
    def test_extraction_keeps_month_day(self, msg, mm, dd):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == (mm, dd), (msg, got)

    def test_审查点名最小复现(self):
        """审查给的最小复现（提取层直测）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("5月13日，早上6点")
        assert (got.get("month"), got.get("day"), got.get("hour")) == (5, 13, 6), got

    def test_real_accumulation_chain_builds_archive_and_chart(self, tmp_path):
        """**真实链路**（审查点名）：历史「我今年50岁了」→ 用户
        「5月13日，10点以后，榆树市，男」→ 建档 1976-05-13 榆树市 + 出盘；
        且不再复读追问月日。"""
        h, db = _h(tmp_path, seed=None)
        h.session_dao = Mock()
        h.session_dao.get_context_for_llm.return_value = [
            {"role": "user", "content": "我今年50岁了"}]
        out = h._handle_bazi("5月13日，10点以后，榆树市，男", "u1")
        assert "再告诉我" not in out and "出生月日" not in out, (
            "刚给完月日又被追问（R2-1 死循环形态）")
        snap = _snap(db)
        assert snap is not None, "F2 齐全后未建档"
        assert snap[:3] == (1976, 5, 13), snap
        assert snap[3] == "榆树市", snap
        chart = h.chart_dao.get_latest_chart("u1")
        assert chart is not None and chart["birth"]["month"] == 5, "未出盘"

    def test_engine_receives_accumulated_birth(self, tmp_path):
        """引擎实收累积值（2019-05-13 vs 1976-05-13 类错值一律不算）。"""
        h, db = _h(tmp_path, seed=None)
        calls = []
        real = h.engine

        class _Rec:
            def calculate(self, *a, **k):
                calls.append(a)
                return real.calculate(*a, **k)

        h.engine = _Rec()
        h.session_dao = Mock()
        h.session_dao.get_context_for_llm.return_value = [
            {"role": "user", "content": "我1976年生的"}]
        h._handle_bazi("5月13日，10点以后，榆树市，男", "u1")
        assert calls, "未走引擎"
        assert calls[0][:7] == (1976, 5, 13, 10, 0, "榆树市", "男"), calls[0][:7]


# ════════════════════════════════════════════════════════════════
# R2-1 反向：B 的战果一条不丢（r2 不许用"恢复 F2"换掉 B）
# ════════════════════════════════════════════════════════════════
class TestBStillHeld:
    @pytest.mark.parametrize("msg", NON_BIRTH_MSGS)
    def test_not_adopted_not_asked_not_written(self, tmp_path, msg):
        h = object.__new__(MessageHandler)
        assert "month" not in h._extract_partial_birth(msg), msg
        assert h._extract_bazi_info(msg) is None, msg
        h2, db = _h(tmp_path)
        out = h2._handle_bazi(msg, "u1")
        assert "您刚说的出生信息" not in out, f"{msg!r} 被问"
        assert _snap(db) == (1999, 3, 28, "长春"), f"{msg!r} 改档"

    @pytest.mark.parametrize("text", [
        "我1999年生的，视力4.5", "我1999年生的，血压11.8",
        "我1999年生的，offer给4.5k", "房租4.5千", "每天睡4.5小时",
        "各缴纳4.5%", "我1999年生的，视力是4.5", "我1999年生的，血糖5.6",
    ])
    def test_bare_decimal_still_not_month_day(self, text):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, (text, got)
        assert h._extract_bazi_info(text) is None, text

    @pytest.mark.parametrize("text", [
        "5月13日可以", "5月13日还有6月7日", "4月5日开会", "3月8日送什么",
    ])
    def test_unrelated_residue_still_rejected(self, text):
        """否定式闸门不给"别的叙述"放行（残留内容非空 → 非生辰）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, (text, got)


# ════════════════════════════════════════════════════════════════
# R2-2 真陈述（日期在后一小句）不得被丢弃
# ════════════════════════════════════════════════════════════════
class TestLateClauseStatementRestored:
    @pytest.mark.parametrize("msg", TRUE_LATE_CLAUSE)
    def test_extraction_keeps(self, msg):
        h = object.__new__(MessageHandler)
        got = h._extract_bazi_info(msg)
        assert got is not None and got[:3] == (1991, 7, 8), (msg, got)

    @pytest.mark.parametrize("msg", TRUE_LATE_CLAUSE)
    def test_builds_archive_and_chart(self, tmp_path, msg):
        h, db = _h(tmp_path, seed=None)
        h._handle_bazi(msg, "u1")
        assert _snap(db)[:3] == (1991, 7, 8), (msg, _snap(db))
        assert h.chart_dao.get_latest_chart("u1") is not None, msg

    def test_conflicting_archive_statement_not_dropped(self, tmp_path):
        """有档案且月日冲突：真陈述按消息级豁免**直接排盘**（不再静默丢弃、
        不再回确认问句——R2-2「优先直接写（真陈述）」）。

        断言口径 = 引擎实收新值（`_do_bazi_analysis` 入参）+ 无问句；档案行
        归属（默认命主 vs 新建命主）取决于既有的**主语判定**（`我出生在…` 这
        类句子在本仓库既有口径下被判 subject=other，是另一族既有行为，非本项
        范围——故此处不锁 persons 行）。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_year=1991))
        calls = []
        real = h.engine

        class _Rec:
            def calculate(self, *a, **k):
                calls.append(a)
                return real.calculate(*a, **k)

        h.engine = _Rec()
        h._do_bazi_analysis_orig = None
        out = h._handle_bazi("我出生在长春，1991年7月8日", "u1")
        assert "您刚说的出生信息" not in out, "真陈述被确认问句挡下（R2-2 回归）"
        assert calls and calls[0][:3] == (1991, 7, 8), (
            "真陈述值未进引擎（静默丢弃）", calls[:1])

    @pytest.mark.parametrize("msg", [
        "我出生在长春，1991年7月8日结的婚",
        "我出生在长春，1991年7月8日结婚",
        "我1991年7月8日结的婚",
    ])
    def test_wedding_still_not_birth(self, tmp_path, msg):
        """D 的战果（婚期不得写成生辰）改由**日期紧后的非出生谓语**承担。"""
        h0 = object.__new__(MessageHandler)
        assert h0._extract_bazi_info(msg) is None, msg
        h, db = _h(tmp_path, seed=None)
        h._handle_bazi(msg, "u1")
        assert _snap(db) is None, f"{msg!r} 婚期被建成生辰"


# ════════════════════════════════════════════════════════════════
# R2-3 否定句式取被肯定值
# ════════════════════════════════════════════════════════════════
class TestNegationTakesAffirmedYear:
    @pytest.mark.parametrize("msg,year", NEGATION_CASES)
    def test_partial_extraction_year(self, msg, year):
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth(msg).get("year") == year, msg

    @pytest.mark.parametrize("msg,year", NEGATION_CASES)
    def test_archive_not_overwritten_with_negated_year(self, tmp_path, msg, year):
        """真实链路：档案不得被写成被否定的 1995。"""
        h, db = _h(tmp_path)
        h._handle_bazi(msg, "u1")
        assert _snap(db)[0] == year, (msg, _snap(db))

    def test_explicit_correction_still_writes_affirmed_value(self, tmp_path):
        """k48 既有口径不回退：`之前填错了，其实是1995年3月8日出生的`
        （唯一命中未被否定）→ 仍写 1995-03-08。"""
        h, db = _h(tmp_path)
        h._handle_bazi("之前填错了，其实是1995年3月8日出生的", "u1")
        assert _snap(db)[:3] == (1995, 3, 8), _snap(db)

    def test_negated_month_day_skipped(self):
        """同族：被否定的月日候选跳过（`不是3月8日，是5月20日生的`）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我不是3月8日生的，是5月20日生的")
        assert (got.get("month"), got.get("day")) == (5, 20), got

    def test_non_correction_message_unchanged(self):
        """非纠正消息行为逐字不变（本项只在明示纠正句式下启用）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth("我1995年生的，视力4.5")["year"] == 1995
        assert h._extract_bazi_info("我是1995年3月8日出生的")[:3] == (1995, 3, 8)
