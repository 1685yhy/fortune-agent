# -*- coding: utf-8 -*-
"""k49-r4：事件表单字条目误伤真生辰（r3 新引入）+ 单字条目规则。

现象（审查 r3 终审实测）：
`_extract_partial_birth("1991年7月8日 酒泉 男")` —— 基线/r2 得
`{'year':1991,'month':7,'day':8,…}`，**r3 只剩 `{'year': 1991}`**；
`我1991年7月8日在酒泉市人民医院出生` 同样只剩 year（小句里的"出生"救不回来）。
真实链路：历史「我1991年生的」→ 用户「1991年7月8日 酒泉 男」→ r3 回
「已记下：出生于1991年、男。再告诉我出生月日…」= 刚给完月日又被追问（复读）。

根因：`_EVENT_TAIL_RE` 的**单字条目** `酒`/`席` 会匹配无关词前缀（酒泉/酒店/
席家村/席梦思），且 ①b（事件谓语）排在 `birth_ctx_near` 之前，连紧后出生词都救
不回（r4 用**显式 shield** 处理这一半：日期紧后先出现出生语境词 → 不判事件日）。

规则（r4 固化）：**事件名词表不收单字，除非该字没有常见"非事件"构词**——
`酒/席/考` 换成多字词形（覆盖面不减），只保留 `嫁/娶`（白名单）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

import src.bot.handler as handler_mod  # noqa: E402
from src.bot.handler import MessageHandler  # noqa: E402

# 审查实测的 10 条误伤（均为地名/品牌/普通名词，不是生活事件）
FALSE_POSITIVE_SHAPES = [
    "1991年7月8日 酒泉 男",
    "1991年7月8日 酒泉市",
    "1991年7月8日 酒店",
    "1991年7月8日 酒家",
    "1991年7月8日 酒泉出生",
    "我1991年7月8日在酒泉市人民医院出生",
    "1991年7月8日 的酒泉出生证",
    "1991年7月8日 席家村",
    "1991年7月8日 席梦思",
    "1991年7月8日 席",
]

# 审查点名的 3 条"语义确为事件，必须保持拦住"
MUST_BLOCK = [
    "我出生在长春，1991年7月8日嫁到长春",
    "我出生在长春，1991年7月8日娶亲",
    "我出生在长春，1991年7月8日考上大学",
]

# 词形替换后必须**仍拦**（覆盖面不减的证据）
EVENT_WORD_FORMS = [
    "我出生在长春，1991年7月8日办酒席",
    "我出生在长春，1991年7月8日摆酒",
    "我出生在长春，1991年7月8日办酒",
    "我出生在长春，1991年7月8日喝酒",
    "我出生在长春，1991年7月8日喜酒",
    "我出生在长春，1991年7月8日喜宴",
    "我出生在长春，1991年7月8日开席",
    "我出生在长春，1991年7月8日摆席",
    "我出生在长春，1991年7月8日宴席",
    "我出生在长春，1991年7月8日席面",
    "我出生在长春，1991年7月8日考试",
    "我出生在长春，1991年7月8日考场",
    "我出生在长春，1991年7月8日嫁人",
    "我出生在长春，1991年7月8日娶媳妇",
    "我出生在长春，1991年7月8日摆酒席",
    "我出生在长春，1991年7月8日办婚宴",
]

# 单字审计表：字 → 代表性"非事件"词（必须**不**被拦）
SINGLE_CHAR_AUDIT = {
    "酒": "酒泉",
    "席": "席家村",
    "考": "考拉",
}


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


# ════════════════════════════════════════════════════════════════
# 1) 10 条误伤（改前 r3：全部只剩 year）
# ════════════════════════════════════════════════════════════════
class TestNonEventWordsNotBlocked:
    @pytest.mark.parametrize("msg", FALSE_POSITIVE_SHAPES)
    def test_extraction_keeps_month_day(self, msg):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == (7, 8), (msg, got)

    def test_minimal_repro_from_review(self):
        """审查给的最小复现（逐字）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("1991年7月8日 酒泉 男")
        assert (got.get("year"), got.get("month"), got.get("day")) == (1991, 7, 8), got

    def test_e2e_no_repeat_question(self, tmp_path):
        """真实链路（审查 E2E）：历史「我1991年生的」→ 用户「1991年7月8日 酒泉 男」
        → 建档 1991-07-08 + 出盘，不再复读追问月日。"""
        h, db = _h(tmp_path)
        h.session_dao = Mock()
        h.session_dao.get_context_for_llm.return_value = [
            {"role": "user", "content": "我1991年生的"}]
        out = h._handle_bazi("1991年7月8日 酒泉 男", "u1")
        assert "再告诉我" not in out and "出生月日" not in out, out[:80]
        snap = _snap(db)
        assert snap is not None and snap[:3] == (1991, 7, 8), snap
        assert h.chart_dao.get_latest_chart("u1") is not None, "未出盘"


# ════════════════════════════════════════════════════════════════
# 2) 事件形态仍拦（3 条点名 + 词形替换覆盖不减）
# ════════════════════════════════════════════════════════════════
class TestEventShapesStillBlocked:
    @pytest.mark.parametrize("msg", MUST_BLOCK + EVENT_WORD_FORMS)
    def test_extraction_rejects(self, msg):
        h = object.__new__(MessageHandler)
        assert h._extract_bazi_info(msg) is None, msg
        assert "month" not in h._extract_partial_birth(msg), msg

    @pytest.mark.parametrize("msg", MUST_BLOCK + EVENT_WORD_FORMS)
    def test_conflict_layer_no_new_person(self, tmp_path, msg):
        """冲突层：有档案时不得新建成命主、不得覆写默认行。"""
        from src.storage.person_dao import PersonDAO
        h, db = _h(tmp_path, seed=dict(birth_year=1991, birth_month=3,
                                       birth_day=28, city="长春", gender="男"))
        h._handle_bazi(msg, "u1")
        assert PersonDAO(db).count_persons("u1") == 1, msg
        assert _snap(db)[:3] == (1991, 3, 28), (msg, _snap(db))


# ════════════════════════════════════════════════════════════════
# 3) 出生语境优先（shield）——不换序也不削弱婚期
# ════════════════════════════════════════════════════════════════
class TestBirthWordShield:
    def test_birth_word_then_event_word_is_birth(self):
        """日期紧后先是出生语境词 → 生辰（后面的"办酒席"是另一件事）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我1991年7月8日出生，那天办酒席")
        assert (got.get("month"), got.get("day")) == (7, 8), got

    def test_event_word_first_still_event(self):
        """反向：日期紧后先是事件词 → 事件日（**shield 不削弱婚期拦截**）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_bazi_info("我1991年7月8日结婚生的孩子") is None
        got = h._extract_partial_birth("我1991年7月8日结婚生的孩子")
        assert got.get("month") is None, got

    def test_birth_word_leading_predicate_direct(self):
        """谓词直证：`_birth_word_leads` 只看**紧后起点**。"""
        assert handler_mod._birth_word_leads("出生在长春")
        assert handler_mod._birth_word_leads("，生的")
        assert not handler_mod._birth_word_leads("结婚生的孩子")
        assert not handler_mod._birth_word_leads("")


# ════════════════════════════════════════════════════════════════
# 3b) 同族顺手：城市候选的**日期残尾**（E 项同类未覆盖形态）
# ════════════════════════════════════════════════════════════════
class TestCityDateRemnantPrefix:
    """`我1991年7月8日在长春市人民医院出生`：XX市 正则的窗口从"日在"起算 →
    基线/本批 r3 均得脏值 `日在酒泉市`（基线逐字同形 = 既有缺陷）。日期残尾
    单字（日/月/年/号）进前缀表，**校验兜底**（剥完必须是已知城市）保证不会
    误改以该字开头的地名。
    """

    def test_known_city_stripped(self):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我1991年7月8日在长春市人民医院出生")
        assert got.get("city") == "长春市", got
        full = h._extract_bazi_info("我1991年7月8日在长春市人民医院出生")
        assert full is not None and full[5] == "长春市", full

    @pytest.mark.parametrize("name", ["日照市", "日喀则市", "长春", "在庄市"])
    def test_rare_or_plain_names_untouched(self, name):
        """剥完不是已知城市 → 原样返回（宁可不动不可改错）。"""
        assert handler_mod._clean_city_name(name) == name

    def test_unknown_city_residual_documented(self):
        """**已知残留（既有，非本批引入）**：`酒泉` 不在既有城市库
        （CITY_LONGLAT/COMMON_CITIES）→ 剥完"酒泉市"通不过校验 → city 仍为
        基线值 `日在酒泉市`（d2341fa 逐字同形）。本测试只锁 R4-1 的目标——
        **月日必须取回**；脏城市值的根治需要更全的行政区划表（已列报告待裁决）。
        """
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我1991年7月8日在酒泉市人民医院出生")
        assert (got.get("month"), got.get("day")) == (7, 8), got


# ════════════════════════════════════════════════════════════════
# 4) 单字规则（结构性锁：新增单字必须先改白名单）
# ════════════════════════════════════════════════════════════════
class TestSingleCharRule:
    @staticmethod
    def _noun_alts():
        pat = handler_mod._EVENT_TAIL_RE.pattern
        nouns = pat.split("(?:")[-1].rstrip(")")
        return [x for x in nouns.split("|") if x]

    def test_no_unlisted_single_char_nouns(self):
        """事件名词表里除白名单（嫁/娶）外**不得有单字条目**——单字会前缀误伤
        （酒泉/席家村/考拉 实测）。新增单字必须同步改白名单与报告说明。"""
        singles = [x for x in self._noun_alts() if len(x) == 1]
        assert set(singles) == set(handler_mod._EVENT_SINGLE_CHAR_WHITELIST), (
            "事件表单字条目超出白名单", singles)

    @pytest.mark.parametrize("char,word", sorted(SINGLE_CHAR_AUDIT.items()))
    def test_audited_single_char_forms_are_not_events(self, char, word):
        """审计表：被删的三个单字（酒/席/考）的代表性非事件词不得被拦。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(f"1991年7月8日 {word}")
        assert (got.get("month"), got.get("day")) == (7, 8), (char, word, got)

    @pytest.mark.parametrize("char,word", [("嫁", "嫁到长春"), ("娶", "娶亲")])
    def test_kept_single_chars_still_block_events(self, char, word):
        """白名单单字（嫁/娶）的事件形态必须仍拦（审查点名的应拦形态）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_bazi_info(f"我出生在长春，1991年7月8日{word}") is None

    def test_whitelist_is_documented_tuple(self):
        assert handler_mod._EVENT_SINGLE_CHAR_WHITELIST == ("嫁", "娶")
