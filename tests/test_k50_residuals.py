# -*- coding: utf-8 -*-
"""k50：k49 审查登记的 6 条残留收口（每条都有 k49 终版 9595c44 上的"改前失败"）。

结构（总则①：结构化判据优先于加词；新增词条必为多字并列出反例）：
- K50-1 中文数字月日纳入**同一套**否定裁决（候选适配器 `_CnMdCand` 进
  `_date_groups`/`_negated_candidate_ids`，不另起第二套）
- K50-2 城市采纳判据从"在不在城市库"改为"**形态**像城市名"
  （2-4 汉字核心 + 市/省/县/区/州/盟/旗/镇/乡 尾缀；库只用于经纬度）
- K50-3 剥离规则补两个 token 类（推测语气 / 口语化时段）
- K50-4 单位词**必须紧贴数字**（去掉 `^\\s*`）——结构化一字符修复
- K50-5 城市候选与日期同族：须与出生语境**同一小句**，且被非出生谓语挡
- K50-6 工具/对话路径区分"**显式**说了北京"与"**缺省**填了北京"

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零网络零 LLM（降级档）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler, _clean_city_name  # noqa: E402
from src.engines.bazi import CITY_LONGLAT  # noqa: E402

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")

# ── K50-1 ────────────────────────────────────────────────────────────
CN_NEGATION = [
    ("我不是腊月廿六生的，是正月初一生的", (1, 1)),
    ("我是正月初一生的，不是腊月廿六生的", (1, 1)),
    ("不是正月初一，是腊月廿六生的", (12, 26)),
]
CN_REGRESSION = [
    ("我是农历腊月廿六出生的", (12, 26)),
    ("我腊月廿六生日", (12, 26)),
    ("我农历三月初三生", (3, 3)),
    ("我是阴历八月十五出生的", (8, 15)),
    ("我的生日是农历腊月廿六", (12, 26)),
    ("三月初三，吉林省长春市榆树市出生，男", (3, 3)),
]

# ── K50-3：会话式应答（本批新增 3 条 + 上批 23 条）────────────────────
SESSION_NEW = ["5月13日，印象中是", "5月13日，可能是", "5月13日，天快黑的时候"]
SESSION_PREV = [
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
# 反向：18 条"仍应拒绝"（K50-3 明确要求：加词不得放行别的内容）
REVERSE_18 = [
    "我4月5日要去出差",
    "3月8日妇女节快乐，我该送什么",
    "我5月20日有个面试，帮我看看",
    "10月1日国庆想去旅游",
    "我3月28日要交房租",
    "下个月3月8日我朋友结婚",
    "我今年想考公务员，4月5日考试",
    "最近工作压力大，3月8日要述职",
    "帮我看看八字，我5月20日有个面试",
    "5月13日可以",
    "5月13日还有6月7日",
    "4月5日开会",
    "3月8日送什么",
    "5月13日见客户",
    "5月13日可能出差",
    "5月13日约了医生",
    "5月13日交房租",
    "3月8日要述职",
]

# ── K50-4：单位黑名单（结构化修复的反例）──────────────────────────────
UNIT_KEEP_REJECT = [
    "个人企业年金各缴纳4.5%",
    "月薪20000元，年终奖另算",
    "offer给4.5k",
    "每天睡4.5小时",
    "房贷利率4.9%，等额本息",
    "房租4.5千",
    "体脂率18.5%，体重120斤",
    "公积金按12%缴纳，公司缴纳7%",
]


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
# K50-1 中文数字月日否定裁决
# ════════════════════════════════════════════════════════════════
class TestCnNegation:
    @pytest.mark.parametrize("msg,expect", CN_NEGATION)
    def test_affirmed_cn_month_day(self, msg, expect):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == expect, (msg, got)

    @pytest.mark.parametrize("msg,expect", CN_REGRESSION)
    def test_cn_no_negation_unchanged(self, msg, expect):
        """无否定词的中文月日句逐字不变（含 k49 提到的榆树市形态）。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == expect, (msg, got)

    def test_cn_uses_same_group_helpers(self):
        """成分同源直证：中文月日候选与数字月日候选进同一份候选表参与裁决。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我不是腊月廿六生的，是正月初一生的")
        assert (got.get("month"), got.get("day")) == (1, 1), got
        # 混排（中文 + 数字）也走同一裁决：被肯定的数字月日胜出
        got2 = h._extract_partial_birth("不是腊月廿六生的，是5月20日生的")
        assert (got2.get("month"), got2.get("day")) == (5, 20), got2

    def test_e2e_archive_not_overwritten_by_denied(self, tmp_path):
        """E2E：档案 5/5 + `我不是腊月廿六生的，是正月初一生的` → 取被肯定值。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_month=5, birth_day=5))
        h._handle_bazi("我不是腊月廿六生的，是正月初一生的", "u1")
        assert _snap(db)[1:3] == (1, 1), _snap(db)


# ════════════════════════════════════════════════════════════════
# K50-2 城市库外地名脏值
# ════════════════════════════════════════════════════════════════
class TestCityFormAdoption:
    @pytest.mark.parametrize("msg,expect", [
        ("我1991年7月8日在酒泉市人民医院出生", "酒泉市"),
        ("我1991年7月8日在日照市人民医院出生", "日照市"),
        ("我1991年7月8日在日喀则市人民医院出生", "日喀则市"),
        ("我出生在长春市", "长春市"),
        ("我在长春市", "长春市"),
        ("三月初三，吉林省长春市榆树市出生，男", "榆树市"),
    ])
    def test_clean_city(self, msg, expect):
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth(msg).get("city") == expect, msg

    @pytest.mark.parametrize("name", ["在庄市", "我在北", "日喀则市", "日照市",
                                      "长春", "庄市", ""])
    def test_red_lines_untouched(self, name):
        """红线：不能自证是地名的（核心 1 字 / 无尾缀）一律原样不动。"""
        assert _clean_city_name(name) == name

    def test_all_117_known_cities_untouched(self):
        """117 城穷举 0 误伤（库内名字不因形态判据被改）。"""
        bad = [c for c in CITY_LONGLAT if _clean_city_name(c) != c]
        assert not bad, bad

    def test_form_rule_direct(self):
        """形态判据直证：2-4 汉字 + 尾缀 ✓；无尾缀/超长/时间词核心 ✗。"""
        from src.bot.handler import _city_form_ok
        assert _city_form_ok("酒泉市") and _city_form_ok("日喀则市")
        assert _city_form_ok("乌鲁木齐市")
        assert not _city_form_ok("在北")          # 无尾缀
        assert not _city_form_ok("庄市")          # 核心 1 字
        assert not _city_form_ok("今天市")        # 闭类时间词核心
        assert not _city_form_ok("这是一个很长的地方市")   # 核心 >4 字

    def test_e2e_three_stores_consistent(self, tmp_path):
        """E2E：干净城市值三源同值（persons / bazi_info / chart_records）。"""
        from src.storage.dao import UserDAO
        h, db = _h(tmp_path, seed=None, user="u2")
        h._handle_bazi("我1991年7月8日在酒泉市人民医院出生", "u2")
        from src.storage.person_dao import PersonDAO
        p = PersonDAO(db).get_default_person("u2")
        bi = UserDAO(db).get_user_bazi("u2") or {}
        ch = (h.chart_dao.get_latest_chart("u2") or {}).get("birth", {})
        assert p["city"] == bi.get("city") == ch.get("city") == "酒泉市", (
            p["city"], bi.get("city"), ch.get("city"))


# ════════════════════════════════════════════════════════════════
# K50-3 会话式应答（3 条新 + 23 条上批 + 18 条反向）
# ════════════════════════════════════════════════════════════════
class TestSessionReplyClasses:
    @pytest.mark.parametrize("msg", SESSION_NEW + SESSION_PREV)
    def test_month_day_kept(self, msg):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == (5, 13), (msg, got)

    @pytest.mark.parametrize("msg", REVERSE_18)
    def test_reverse_still_rejected(self, msg):
        h = object.__new__(MessageHandler)
        assert "month" not in h._extract_partial_birth(msg), msg

    def test_e2e_new_classes_build_archive(self, tmp_path):
        """E2E：历史「我1991年生的」→「5月13日，天快黑的时候」→ 建档 + 出盘。"""
        h, db = _h(tmp_path, seed=None)
        h.session_dao = Mock()
        h.session_dao.get_context_for_llm.return_value = [
            {"role": "user", "content": "我1991年生的"}]
        out = h._handle_bazi("5月13日，天快黑的时候", "u1")
        assert "再告诉我" not in out, out[:80]
        assert _snap(db)[:3] == (1991, 5, 13), _snap(db)
        assert h.chart_dao.get_latest_chart("u1") is not None


# ════════════════════════════════════════════════════════════════
# K50-4 单位词必须紧贴数字
# ════════════════════════════════════════════════════════════════
class TestUnitAdjacency:
    @pytest.mark.parametrize("msg", ["1991年7月8日 个人 男", "1991年7月8日 次数 男",
                                     "1991年7月8日 个别 男", "1991年7月8日 人次 男"])
    def test_no_adjacent_digit_not_unit(self, msg):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert (got.get("month"), got.get("day")) == (7, 8), (msg, got)

    @pytest.mark.parametrize("msg", UNIT_KEEP_REJECT)
    def test_adjacent_units_still_rejected(self, msg):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg)
        assert "month" not in got and "day" not in got, (msg, got)
        assert h._extract_bazi_info(msg) is None, msg

    def test_rule_is_structural(self):
        """结构直证：单位正则**不允许**前置空白（必须紧贴候选末尾的数字）。"""
        from src.bot.handler import _NON_DATE_UNIT_RE
        assert _NON_DATE_UNIT_RE.match("个")            # 紧贴
        assert not _NON_DATE_UNIT_RE.match(" 个")       # 隔了空格 → 不是单位
        assert not _NON_DATE_UNIT_RE.match(" 个人")


# ════════════════════════════════════════════════════════════════
# K50-5 婚期句/现居句城市
# ════════════════════════════════════════════════════════════════
class TestCityNotFromEventClause:
    def test_wedding_clause_city_not_taken(self):
        """婚期小句里的城市不作出生地；出生小句的（贵阳）照取。"""
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我出生在贵阳，1991年7月8日在长春市结的婚")
        assert got.get("city") == "贵阳", got

    def test_residence_clause_city_not_taken(self):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth("我出生在长春，我现在住广州")
        assert got.get("city") is None, got

    @pytest.mark.parametrize("msg,expect", [
        ("我出生在长春市", "长春市"),
        ("我在长春市", "长春市"),
        ("三月初三，吉林省长春市榆树市出生，男", "榆树市"),
        ("我1990-05-20 15:00 深圳 女", "深圳"),
        ("我来自吉林，长春市", "长春市"),
    ])
    def test_red_lines(self, msg, expect):
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth(msg).get("city") == expect, msg

    def test_no_birth_word_keeps_baseline(self):
        """无出生语境词的消息维持既有行为（不因本项被拦）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth("我1990-05-20 15:00 深圳 女")["city"] == "深圳"
        assert h._extract_partial_birth("我在长春市")["city"] == "长春市"

    def test_e2e_archive_city_kept(self, tmp_path):
        """E2E：档案城市不被婚期句/现居句改写。"""
        h, db = _h(tmp_path)                      # 档案城市 长春
        h._handle_bazi("我出生在长春，我现在住广州", "u1")
        assert _snap(db)[3] == "长春", _snap(db)
        h2, db2 = _h(tmp_path / "b")
        h2._handle_bazi("我出生在贵阳，1991年7月8日在长春市结的婚", "u1")
        assert _snap(db2)[3] != "长春市", _snap(db2)


# ════════════════════════════════════════════════════════════════
# K50-6 工具/对话路径：显式北京 vs 缺省北京
# ════════════════════════════════════════════════════════════════
class TestBeijingDefaultVsExplicit:
    def test_tool_default_beijing_does_not_overwrite(self, tmp_path):
        """档案长春 + 工具参数北京（原文无北京）→ 不改档 + 问句。"""
        h, db = _h(tmp_path)
        r = h._tool_bazi("1999年3月28日10点55分 北京 男", "u1")
        assert _snap(db)[3] == "长春", "缺省北京静默改档"
        assert not r.ok and "不太一致" in r.text, r.text
        assert ("u1", "") in h._pending_birth, "未暂存待更新值（承接半环）"

    def test_tool_explicit_beijing_in_turn_writes(self, tmp_path):
        """用户原文**显式**说了北京 → 判冲突但按显式陈述口径直写（k48-r3 豁免）。"""
        h, db = _h(tmp_path)
        r = h._tool_bazi("1999年7月8日10点 北京 男", "u1",
                         "我是1999年7月8日在北京出生的", "s1")
        assert r.ok, r.text
        assert _snap(db)[3] == "北京", _snap(db)

    def test_tool_no_archive_builds_normally(self, tmp_path):
        """红线：无档案用户报北京 → 正常建档排盘。"""
        h, db = _h(tmp_path, seed=None, user="u9")
        r = h._tool_bazi("1999年3月28日10点55分 北京 男", "u9")
        assert r.ok, r.text
        assert _snap(db, "u9") == (1999, 3, 28, "北京"), _snap(db, "u9")

    def test_tool_archive_without_city_not_conflict(self, tmp_path):
        """红线：档案无城市（引擎缺省不参与比较）→ 不判冲突。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, city=""))
        r = h._tool_bazi("1999年3月28日10点55分 北京 男", "u1")
        assert r.ok, r.text

    def test_dialogue_default_beijing_not_conflict(self, tmp_path):
        """对话路径同口径：消息里没有城市 token（缺省北京）→ 不判城市冲突。"""
        h = Mock()
        h._extract_partial_birth = MessageHandler._extract_partial_birth.__get__(
            object.__new__(MessageHandler))
        assert not MessageHandler._extract_partial_birth(
            object.__new__(MessageHandler), "我是1990年5月20日出生的").get("city")
        assert MessageHandler._extract_partial_birth(
            object.__new__(MessageHandler), "我是1990年5月20日在北京出生的"
        ).get("city") == "北京"

    def test_tool_params_city_consistent_with_archive_not_conflict(self, tmp_path):
        """**细化判据**（全量回归实测补）：参数里写的城市与档案一致、只是**提取器
        不认识**（`长春` 无"市"尾缀且不在对话城市库 → 回落缺省"北京"）→ 不得
        误判冲突（否则 k11c 的 `1999年5月13日 10:55 长春 男` 被无端拦下）。
        判据下在"参数字面所说的城市"（`_extract_partial_birth` 的 city 键），
        不是引擎缺省值。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_month=5, birth_day=13))
        r = h._tool_bazi("1999年5月13日 10:55 长春 男", "u1")
        assert r.ok, r.text                       # 不因缺省北京被无端拦下
        # 注：本形态下档案城市仍会被**引擎缺省"北京"**覆盖（提取器不认识无"市"
        # 尾缀、又不在对话城市库的地名）——这是 k49 报告已披露的**既有**另一族
        # 问题（"城市缺省覆盖"），本批未修、不在 K50-6 验收内；证据与影响见
        # 报告 K50-6 节"诚实披露"。

    def test_tool_params_no_city_at_all_not_conflict(self, tmp_path):
        """参数字面没提城市（引擎缺省"北京"不算主张）→ 不判冲突。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_month=5, birth_day=13))
        r = h._tool_bazi("1999年5月13日 10:55 男", "u1")
        assert r.ok, r.text

    def test_tool_params_city_differs_and_absent_in_turn_conflict(self, tmp_path):
        """参数字面说了别的城市 + 用户原文没有它 → 判冲突走问句。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, birth_month=5, birth_day=13))
        r = h._tool_bazi("1999年5月13日 10:55 西安 男", "u1")
        assert not r.ok and "不太一致" in r.text, r.text

    def test_city_explicit_helper_single_source(self):
        """`_city_explicit_in` 复用同一套城市提取（不另写识别）。"""
        from src.bot.handler import _city_explicit_in
        assert _city_explicit_in("我是1999年7月8日在北京出生的", "北京") is True
        assert _city_explicit_in("1999年3月28日10点55分 男", "北京") is False
        assert _city_explicit_in("", "北京") is False
        # 括号内的业务地名不算（沿用既有门）
        assert _city_explicit_in("我在公司（北京，五险一金）上班", "北京") is False
