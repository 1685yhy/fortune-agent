# -*- coding: utf-8 -*-
"""k49-r3：独立审查（r2 复审）三张探针表 + Minor-2 的回归锁。

R3-1（Critical）r2 的否定启发式判反：`_negated_candidate_ids` 优先取"左侧 6 字
内候选"，于是中文 `A，不是B`（否定词在 B 前、左侧恰有 A）判反 → 把**被否认**的
值写进档案；另"年取肯定值 + 月日取否定值"的**合成体**（`1999-03-08`）。
修法：两侧分明（右侧优先且须无标点，否则回落左侧）+ **成分同源**（`1999年5月20日`
按日期组整体裁决）。

R3-2（Important）F2 会话式应答仍丢月日：剥离表未含**排盘请求词/会话确认词/
忘记类词** → 23 条会话式应答有 17 条丢月日（含仓库自带例句
`5月13日 10点 榆树市 男，帮我排个盘`）。修法：把"哪些 token 类该剥"写成
**一致规则**（七类：时间/相对年/地点/性别/出生语境/排盘请求/会话应答），
排盘请求类复用 `person_dao._CHART_INTENT_WORD_RE` 单一事实源；反向（事件谓语与
叙述连接词）**刻意不剥**。

R3-3（Important）婚期谓语表覆盖不足：r2 只拦 10/22。修法：按"（助动/量词前缀）*
+ 事件名词"的**构成规则**补齐词族（办酒席/摆酒/领结婚证/喜事/开席/喜宴/答谢宴/
嫁人/娶媳妇/办的喜酒…），并在**冲突层**断言（不是只断言提取层）。

Minor-2：`帮我看看八字，我5月20日有个面试` 仍被问（前缀组不跳过量词"个"）→
前缀组补量词。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零网络零 LLM（降级档）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")

# ── R3-1 探针表（审查者原表 4 行 + "必须同时成立" 3 行 + 同族 2 行）──────
# (档案, 消息, 期望 (年,月,日))
NEGATION_ROWS = [
    # 审查者实测表（r2 判反 → 现在必须与基线一致）
    (dict(ARCHIVE, birth_year=1995, birth_month=3, birth_day=8),
     "我1999年5月20日生的，不是1995年3月8日生的", (1999, 5, 20)),
    (dict(ARCHIVE), "我1999年生的，不是1995年生的", (1999, 3, 28)),
    (dict(ARCHIVE), "我生日是5月13日，不是3月8日", (1999, 5, 13)),
    (dict(ARCHIVE, birth_year=1968, birth_month=5, birth_day=13),
     "我1968年5月13日生的，不是1995年3月8日", (1968, 5, 13)),
    # 必须同时成立（不得为了修 A 型改坏这几型）
    (dict(ARCHIVE), "我不是1995年生的，是1999年生的", (1999, 3, 28)),
    (dict(ARCHIVE), "我1995年生的？不是，我1999年生的", (1999, 3, 28)),
    (dict(ARCHIVE), "不是3月28日，是5月13日", (1999, 5, 13)),
    # 同族（合成体反向：把**被肯定**的小句整组取回）
    (dict(ARCHIVE),
     "我不是1995年5月20日生的，是1999年3月8日生的", (1999, 3, 8)),
    (dict(ARCHIVE, birth_year=1995, birth_month=3, birth_day=8),
     "我1999年5月20日生的，不是1995年生的", (1999, 5, 20)),
]

# ── R3-2 探针表（审查者 23 条会话式应答，含仓库自带例句）────────────────
SESSION_REPLIES = [
    "5月13日 10点 榆树市 男，帮我排个盘",
    "5月13日 10点 榆树市 男，帮我排盘",
    "5月13日 10点 榆树市 男，看看八字",
    "5月13日 10点 榆树市 男，算算",
    "5月13日 10点 榆树市 男，谢谢",
    "5月13日，没错",
    "5月13日，应该没错",
    "5月13日，好的",
    "5月13日，行",
    "5月13日，我确定",
    "5月13日，具体时间忘了",
    "5月13日，时间记不清",
    "5月13日，时辰不定",
    "5月13日，确定没错",
    "5月13日，好的谢谢",
    "5月13日 10点 榆树市 男，麻烦帮我排个盘",
    "5月13日，就是这天",
    "5月13日 早上6点 榆树市 男，帮我看看八字",
    "5月13日，我确定了",
    "5月13日，嗯",
    "5月13日 10点 榆树市 男，帮我算算",
    "5月13日，应该是对的",
    "5月13日 上午十点 长春 男，帮我排盘",
]

# ── R3-3 探针表（22 条婚期形态；r2 只拦 10 条）──────────────────────────
WEDDING_SHAPES = [
    "我出生在长春，1991年7月8日结的婚",
    "我出生在长春，1991年7月8日结婚",
    "我出生在长春，1991年7月8日订婚",
    "我出生在长春，1991年7月8日离婚",
    "我出生在长春，1991年7月8日办的婚礼",
    "我出生在长春，1991年7月8日领证",
    "我出生在长春，1991年7月8日登记结婚",
    "我出生在长春，1991年7月8日结婚纪念日",
    "我出生在长春，1991年7月8日成的家",
    "我出生在长春，1991年7月8日办酒席",
    "我出生在长春，1991年7月8日摆酒",
    "我出生在长春，1991年7月8日领结婚证",
    "我出生在长春，1991年7月8日办喜事",
    "我出生在长春，1991年7月8日开席",
    "我出生在长春，1991年7月8日喜宴",
    "我出生在长春，1991年7月8日答谢宴",
    "我出生在长春，1991年7月8日嫁人",
    "我出生在长春，1991年7月8日娶媳妇",
    "我出生在长春，1991年7月8日办的喜酒",
    "我出生在长春，1991年7月8日摆酒席",
    "我出生在长春，1991年7月8日办婚宴",
    "我出生在长春，1991年7月8日开喜宴",
]

# brief B 的 8 条非出生语境消息 + Minor-2 形态（一条都不能回来）
NON_BIRTH_MSGS = [
    "我4月5日要去出差",
    "3月8日妇女节快乐，我该送什么",
    "我5月20日有个面试，帮我看看",
    "10月1日国庆想去旅游",
    "我3月28日要交房租",
    "下个月3月8日我朋友结婚",
    "我今年想考公务员，4月5日考试",
    "最近工作压力大，3月8日要述职",
    "帮我看看八字，我5月20日有个面试",
]


def _mk_archive(db_path, user_id="u1", **over):
    from src.storage.person_dao import PersonDAO
    b = {"gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
         "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
         "city": "长春"}
    b.update(over)
    return PersonDAO(db_path).create_person(
        user_id, name="我", relation="自己", is_default=True, birth=b)


def _h(tmp_path, seed=None):
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
    h._downgraded = {"u1": True}
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
        _mk_archive(db, "u1", **seed)
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
# R3-1 否定句式（9 行；改前 = r2 判反 4 行 + 合成体）
# ════════════════════════════════════════════════════════════════
class TestNegationVerdict:
    @pytest.mark.parametrize("seed,msg,expect", NEGATION_ROWS)
    def test_archive_matches_baseline(self, tmp_path, seed, msg, expect):
        """真实链路：档案不得被写成**被否认**的值（也不得出现合成体）。"""
        h, db = _h(tmp_path, seed=seed)
        h._handle_bazi(msg, "u1")
        snap = _snap(db)
        assert (snap[0], snap[1], snap[2]) == expect, (msg, snap)

    @pytest.mark.parametrize("msg,expect", [(r[1], r[2]) for r in NEGATION_ROWS])
    def test_extractors_consistent(self, msg, expect):
        """年/月日两条提取路径不得分裂：部分提取器报的年必须是被肯定值，
        消息含月日时月日也必须是被肯定值。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        if re.search(r'\d{4}', msg):        # 消息里确有年份 → 必须是被肯定值
            assert got.get("year") == expect[0], (msg, got)
        if re.search(r'\d{1,2}\s*月\s*\d{1,2}', msg):
            assert (got.get("month"), got.get("day")) == (expect[1],
                                                           expect[2]), (msg, got)

    def test_synthetic_not_mixed_from_two_clauses(self):
        """合成体防线：年与月日不得各取一半（r2 的 `1999-03-08` 形态）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我1999年5月20日生的，不是1995年3月8日生的")
        assert (got.get("year"), got.get("month"), got.get("day")) == (1999, 5, 20), got
        got2 = h._extract_partial_birth("我不是1995年5月20日生的，是1999年3月8日生的")
        assert (got2.get("year"), got2.get("month"), got2.get("day")) == (1999, 3, 8), got2

    def test_k48_correction_unchanged(self):
        """k48 既有口径不回退（唯一命中未被否定 → 照写）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_bazi_info("之前填错了，其实是1995年3月8日出生的")[:3] == \
            (1995, 3, 8)


# ════════════════════════════════════════════════════════════════
# R3-2 会话式应答（23 条；改前 r2 丢 17 条）
# ════════════════════════════════════════════════════════════════
class TestSessionRepliesKeepMonthDay:
    @pytest.mark.parametrize("msg", SESSION_REPLIES)
    def test_extraction_keeps_month_day(self, msg):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == (5, 13), (msg, got)

    def test_repo_fixture_sentence_full_extraction(self):
        """仓库自带例句（`tests/test_partial_birth.py` 同款形态）整条可用。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("5月13日 10点 榆树市 男，帮我排个盘")
        assert (got.get("month"), got.get("day"), got.get("hour"),
                got.get("city"), got.get("gender")) == (5, 13, 10, "榆树市", "男"), got

    def test_real_accumulation_chain_after_reply_words(self, tmp_path):
        """真实链路：历史「我今年50岁了」→ 用户「5月13日 10点 榆树市 男，帮我排个盘」
        → 建档 1976-05-13 榆树市 + 出盘（不再复读追问）。"""
        h, db = _h(tmp_path)
        h.session_dao = Mock()
        h.session_dao.get_context_for_llm.return_value = [
            {"role": "user", "content": "我今年50岁了"}]
        out = h._handle_bazi("5月13日 10点 榆树市 男，帮我排个盘", "u1")
        assert "再告诉我" not in out and "出生月日" not in out, out[:80]
        snap = _snap(db)
        assert snap is not None and snap[:3] == (1976, 5, 13), snap
        assert snap[3] == "榆树市", snap
        assert h.chart_dao.get_latest_chart("u1") is not None, "未出盘"

    @pytest.mark.parametrize("msg", NON_BIRTH_MSGS)
    def test_non_birth_still_rejected(self, tmp_path, msg):
        """反向：事件叙述 / Minor-2 形态一条都不能被放回来。"""
        h = object.__new__(MessageHandler)
        assert "month" not in h._extract_partial_birth(msg), msg
        h2, db = _h(tmp_path, seed=ARCHIVE)
        out = h2._handle_bazi(msg, "u1")
        assert "您刚说的出生信息" not in out, f"{msg!r} 被问"
        assert _snap(db) == (1999, 3, 28, "长春"), f"{msg!r} 改档"

    @pytest.mark.parametrize("text", [
        "5月13日可以", "5月13日还有6月7日", "4月5日开会", "3月8日送什么",
    ])
    def test_deliberate_non_strip_kept(self, text):
        """**刻意不剥**的两类（r2 已声明的选边，r3 保持）：日程口吻词（可以）与
        叙述连接词（还有/和/跟）——剥掉会把误报面重新打开。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, (text, got)


# ════════════════════════════════════════════════════════════════
# R3-3 婚期形态（22 条）+ 冲突层断言 + Minor-2
# ════════════════════════════════════════════════════════════════
class TestWeddingShapesBlocked:
    @pytest.mark.parametrize("msg", WEDDING_SHAPES)
    def test_extraction_rejects(self, msg):
        h = object.__new__(MessageHandler)
        assert h._extract_bazi_info(msg) is None, msg
        assert "month" not in h._extract_partial_birth(msg), msg

    @pytest.mark.parametrize("msg", WEDDING_SHAPES)
    def test_conflict_layer_no_new_person_no_overwrite(self, tmp_path, msg):
        """**冲突层断言**（审查点名补回强度）：有档案（1991-03-28）时婚期不得
        新建成命主、不得覆写默认行（r2 滑过时实测 n_persons 1→2）。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_year=1991))
        h._handle_bazi(msg, "u1")
        assert _count(db) == 1, f"{msg!r} 婚期被当成'另一个人的生辰'落库"
        assert _snap(db)[:3] == (1991, 3, 28), (msg, _snap(db))

    @pytest.mark.parametrize("msg", [
        "我出生在长春，1991年7月8日领结婚证",
        "我出生在长春，1991年7月8日办酒席",
    ])
    def test_conflict_layer_no_archive_no_silent_archive(self, tmp_path, msg):
        """无档案 + 婚期形态 → 不得静默建档（审查点名两种形态）。"""
        h, db = _h(tmp_path)
        h._handle_bazi(msg, "u1")
        assert _count(db) == 0, f"{msg!r} 婚期静默建档"
        assert _snap(db) is None

    def test_true_statement_still_writes(self, tmp_path):
        """反向：真陈述（同样的日期、无事件谓语）照旧建档出盘。"""
        h, db = _h(tmp_path)
        h._handle_bazi("我出生在长春，1991年7月8日", "u1")
        assert _snap(db)[:3] == (1991, 7, 8), _snap(db)
