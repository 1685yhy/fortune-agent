# -*- coding: utf-8 -*-
"""k49 B（Important-2 + 残留①）+ D（残留②）：出生语境**相邻**收口。

B 现象（四维验收实测，证据 `/tmp/k48verify-main/evidence/ask_cur.json`）：
8 条**非出生语境**的普通日期消息里 7 条被问出生信息（基线 0 条）——
`我4月5日要去出差` / `3月8日妇女节快乐，我该送什么` / `我5月20日有个面试` /
`10月1日国庆想去旅游` / `我今年想考公务员，4月5日考试` /
`最近工作压力大，3月8日要述职` / `下个月3月8日我朋友结婚`。
残留①：`我1999年生的，视力4.5` → 抽出 month=4/day=5（无档案时直接建错档）。

D 现象（残留②）：`我出生在长春，1991年7月8日结的婚` → 形态判据只看"全文有
没有出生语境词"、不要求与日期**相邻** → 豁免生效 → 婚期可静默写档（无档案时
直接建成生辰；有档案时年份差 ≤2 也照写）。

期望：候选附近**没有**出生语境 → **不问也不写**（打断去掉、安全底线不变）；
真出生陈述（10 条夹具 + F2 分步裸日期）一条都不能退。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零网络零 LLM（降级档）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")

# 验收实测的 8 条非出生语境消息（7 条被问 + 1 条与档案同值未问）
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

# 10 条真出生陈述夹具（md_supplement 同源，`/tmp/k48verify-main/md_*.json`）
TRUE_STATEMENTS = [
    "我是农历腊月廿六出生的", "我是3月8日出生的", "我是3月8日生的",
    "我生日是3月8日", "3月8日是我的生日", "我是阴历八月十五出生的",
    "我农历三月初三生", "我腊月廿六生日", "我是3月8日生日",
    "我的生日是农历腊月廿六",
]

# 残留①裸小数族（c4_radius 语料）
BARE_DECIMALS = [
    "我1999年生的，视力4.5", "我1999年生的，视力是4.5",
    "我1999年生的，血压11.8", "我1999年生的，血糖5.6",
    "我1999年生的，身高1.75", "我1999年生的，体温36.5",
]


def _mk_archive(db_path, user_id="u1", **over):
    from src.storage.person_dao import PersonDAO
    b = {"gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
         "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
         "city": "长春"}
    b.update(over)
    return PersonDAO(db_path).create_person(
        user_id, name="我", relation="自己", is_default=True, birth=b)


def _h(tmp_path, seed=ARCHIVE, users=("u1",)):
    from src.engines.bazi import BaziEngine
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
    h.chart_dao = None
    h.member_dao = None
    h._downgraded = {u: True for u in users}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._citations = {}
    h._fact_ctx = {}
    # subject=other（他人盘确定性卡片路径）装配点
    h._pregen_instant = {}
    h._consume_pregen_instant = Mock(return_value="")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="[复用档案]")
    if seed:
        for u in users:
            _mk_archive(db, u, **seed)
    return h, db


def _snap(db, user_id="u1"):
    from src.storage.person_dao import PersonDAO
    p = PersonDAO(db).get_default_person(user_id)
    return None if not p else (p["birth_year"], p["birth_month"],
                               p["birth_day"], p["city"])


def _count(db, user_id="u1"):
    from src.storage.person_dao import PersonDAO
    return PersonDAO(db).count_persons(user_id)


# ════════════════════════════════════════════════════════════════
# B-1 非出生语境的普通日期：不问、不写（改前 7/8 被问）
# ════════════════════════════════════════════════════════════════
class TestNonBirthDatesNeitherAskNorWrite:
    @pytest.mark.parametrize("msg", NON_BIRTH_MSGS)
    def test_not_asked_and_not_written(self, tmp_path, msg):
        h, db = _h(tmp_path)
        out = h._handle_bazi(msg, "u1")
        assert "您刚说的出生信息" not in out, f"{msg!r} 被问出生信息"
        assert _snap(db) == (1999, 3, 28, "长春"), f"{msg!r} 改动了档案"

    @pytest.mark.parametrize("text", BARE_DECIMALS)
    def test_bare_decimal_not_month_day(self, text):
        """残留①：裸小数（视力4.5/血压11.8…）不得成月日。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, f"{text!r} → {got}"
        assert h._extract_bazi_info(text) is None, f"{text!r} 全量提取未作废"

    def test_bare_decimal_no_archive_no_bogus_chart(self, tmp_path):
        """残留①端到端：无档案用户发裸小数 → 不出盘、不建档。"""
        h, db = _h(tmp_path, seed=None)
        out = h._handle_bazi("我1999年生的，视力4.5", "u1")
        assert _count(db) == 0, "裸小数被当生辰建档"
        assert "1999年4月5日" not in out

    def test_gap_before_fix_contrast(self, tmp_path):
        """对照：同样的月日本身在**出生语境**里必须照取（证明收窄的是语境
        而不是日期形态——改前这批消息是与"真陈述"同形态才被误问的）。"""
        h, db = _h(tmp_path)
        h._handle_bazi("我是4月5日出生的", "u1")
        assert _snap(db) == (1999, 4, 5, "长春")


# ════════════════════════════════════════════════════════════════
# B-2 真出生陈述一条都不退（10 条夹具）
# ════════════════════════════════════════════════════════════════
class TestTrueStatementsStillWrite:
    @pytest.mark.parametrize("msg", TRUE_STATEMENTS)
    def test_direct_write_no_ask(self, tmp_path, msg):
        h, db = _h(tmp_path)
        out = h._handle_bazi(msg, "u1")
        assert "您刚说的出生信息" not in out, f"{msg!r} 被确认问句挡下"
        assert _snap(db)[:3] != (1999, 3, 28), f"{msg!r} 未直接写档"

    def test_bare_month_day_message_still_accumulates(self):
        """F2 分步口述：整条消息就是一个裸日期 → 仍取（既有夹具同口径）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth("5月13日")["month"] == 5
        assert h._extract_partial_birth("我5月13日")["day"] == 13

    def test_correction_phrased_bare_date_still_accumulates(self):
        """F2 纠正口吻：`其实是5月13日` → 仍取（明示纠正句式与
        `birth_conflict_fields` 豁免同源判据 `person_dao.is_correction_text`；
        既有夹具 `test_md_lunar_marker_latest_wins` 同口径）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("其实是5月13日")
        assert (got.get("month"), got.get("day")) == (5, 13), got

    def test_non_birth_filler_not_accepted(self):
        """反向（宁漏勿误）：白名单外的残留内容一律不当生辰。"""
        h = object.__new__(MessageHandler)
        for t in ("5月13日可以", "5月13日还有6月7日", "4月5日开会",
                  "3月8日送什么"):
            got = h._extract_partial_birth(t)
            assert "month" not in got and "day" not in got, (t, got)

    def test_full_date_without_ctx_still_extracted(self):
        """无出生词的**完整日期**形态零回退（既有格式）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_bazi_info("1990-05-20 15:00 深圳 女") == (
            1990, 5, 20, 15, 0, "深圳", "女")
        assert h._extract_bazi_info("1999年3月28日 早上十点 长春")[:3] == (
            1999, 3, 28)


# ════════════════════════════════════════════════════════════════
# B-3 真冲突仍要问（收窄不是关闭）
# ════════════════════════════════════════════════════════════════
class TestRealConflictStillAsks:
    def test_chart_request_with_conflicting_date_asks(self, tmp_path):
        """排盘请求（消息级出生意图）+ 与档案冲突的月日 → 仍问一句
        （k48 既有行为；8 条误报消息**不含**该类意图词，故不受影响）。"""
        h, db = _h(tmp_path)
        out = h._handle_bazi("帮我排个盘，3月8日", "u1")
        assert "您刚说的出生信息" in out
        assert _snap(db) == (1999, 3, 28, "长春")

    def test_year_conflict_still_asks(self, tmp_path):
        h, db = _h(tmp_path)
        out = h._handle_bazi("我是1995年3月8日出生的", "u1")
        assert "您刚说的出生信息" in out
        assert _snap(db) == (1999, 3, 28, "长春")


# ════════════════════════════════════════════════════════════════
# D 婚期（残留②）：语境不相邻 → 不问不写；相邻 → 直写
# ════════════════════════════════════════════════════════════════
class TestWeddingDateNotABirth:
    def test_wedding_date_no_archive_no_chart_no_person(self, tmp_path):
        """无档案：`我出生在长春，1991年7月8日结的婚` → 不得建成生辰
        （改前 persons 被建成 1991-07-08）。"""
        h, db = _h(tmp_path, seed=None)
        h._handle_bazi("我出生在长春，1991年7月8日结的婚", "u1")
        assert _count(db) == 0, "婚期被建成生辰档案"
        assert _snap(db) is None

    def test_wedding_date_with_archive_not_written(self, tmp_path):
        """有档案（年份差 ≤2，k19 不拦）：月日不得被婚期覆写、不得新建成命主。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_year=1991))
        h._handle_bazi("我出生在长春，1991年7月8日结的婚", "u1")
        assert _snap(db) == (1991, 3, 28, "长春")
        assert _count(db) == 1, "婚期新建成命主行"

    def test_true_statement_with_year_still_writes(self, tmp_path):
        """反向（真陈述）：`我1991年7月8日出生在长春` → 照旧写档。"""
        h, db = _h(tmp_path, seed=None)
        h._handle_bazi("我1991年7月8日出生在长春", "u1")
        assert _snap(db)[:3] == (1991, 7, 8)

    def test_wedding_exempt_narrowed_in_predicate(self):
        """谓词层直证：豁免改按**语境相邻**（同一实现 person_dao）。"""
        from src.storage.person_dao import (birth_conflict_fields,
                                            is_explicit_birth_statement)
        saved = {"birth_year": 1990, "birth_month": 5, "birth_day": 20}
        new = {"year": 1991, "month": 7, "day": 8}
        wedding = "我出生在长春，1991年7月8日结的婚"
        true_stmt = "我1991年7月8日出生在长春"
        w_span = true_span = (7, 13)      # 两个句子的日期候选区间
        assert is_explicit_birth_statement(wedding) is True       # 整串口径（既有）
        assert is_explicit_birth_statement(wedding, span=w_span) is False
        assert is_explicit_birth_statement(true_stmt, span=true_span) is True
        # 年份冲突恒查（k19）：把年份差挪开，看月日是否被豁免
        saved0 = {"birth_year": 1991, "birth_month": 3, "birth_day": 28}
        assert birth_conflict_fields(saved0, new, ctx=wedding,
                                     span=w_span) == ["month_day"]
        assert birth_conflict_fields(saved0, new, ctx=true_stmt,
                                     span=true_span) == []
