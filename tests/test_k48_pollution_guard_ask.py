# -*- coding: utf-8 -*-
"""k48：真实用户会话暴露的四类问题（档案污染 / 双盘拼接 / 守卫误报 / 先问后写）。

来源：2026-09-14 晚用户体验版实机反馈「比之前强很多但还有问题」→ 控制方从其
生产会话（session s_mtme8nc9afzo）逐条定位。四条的**用户真实语句**即本文件
的双向用例锚点（每条都含"该拦"与"不该拦"两侧）。

1. P0 档案被职场文本污染（根因）
   用户原话（第三轮，offer 对比）：
   「我目前有2个offer…（北京，顶格五险一金，口径统一）…个人企业年金各缴纳
     4.5%…」
   修前 `_extract_partial_birth` 输出
   {'month': 4, 'day': 5, '_md_lunar': False, 'city': '北京'}
   →「4.5%」被当「4月5日」、「（北京…）」被当出生地 → 写进档案 → 同会话两张盘。
   修后必须提取为 **空**；同时真实 F2 场景（年龄/完整日期/性别口语）照样提取。

2. P2 冲突时"先问后写"（不再静默改档案）
   高置信出生语境 → 仍直接写（"说一次就记住"体验保留）；
   与既有档案冲突（年/月/日/城市不一致）且非明示纠正、非表单 → 不写 + 回确认。

3. P0.5 一个气泡两张盘（服务端保险）
   流式已推润色版；D2 把整条换成引擎原稿 → 出口"找重叠、只补未发部分"，
   两条文本完全不同 → 重叠 0 → 整条又发一遍 → 气泡 = 两张盘。
   新判据：重叠 0 且已流文本本身 ≥40 字（已流过一个完整回答）→ 判为替换，
   不整条追加（交由前端 done 事件整泡替换）；正文几乎没流出 → 仍整条补发。

4. P1 准确性守卫误报（否定/劝阻语境）
   实测 20:11:24 `Accuracy issue in stream response: 投资建议（禁止）: 稳赚`
   ——而原文是**提醒用户别被"稳赚"话术带走**（劝阻语境）。守卫只记日志不
   改回复 → 误报会淹没真违规。真违规（稳赚不赔/保证收益/跟着我买）仍要拦。

隔离：tmp_path 真实 SQLite；零网络零 LLM（测试 LLM 一律免费 glm-4-flash 且
本文件不调用）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import logging  # noqa: E402
from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

# ── 用户真实语句（控制方从生产会话逐字摘录）──────────────────────────
OFFER_MSG = (
    "我目前有2个offer，一个是北京的大厂，一个是上海的创业公司"
    "（北京，顶格五险一金，口径统一），个人企业年金各缴纳4.5%，"
    "月薪20000元，我该怎么选"
)


def make_handler(**kw) -> MessageHandler:
    """object.__new__ 装配（跑真实方法体，零 __init__ 副作用）。"""
    h = object.__new__(MessageHandler)
    h.engine = None
    h.llm = None
    h.dao = None
    h.retriever = None
    h.memory = None
    h.memory_system = None
    h.session_dao = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    for k, v in kw.items():
        setattr(h, k, v)
    return h


# ================================================================
# 1) P0 语境闸门：该拦的拦
# ================================================================

class TestP0GateRejectsNonBirthText:
    """用户 offer 原话与同族的"职场/合同/账单"文本绝不提取任何出生信息。"""

    def test_user_offer_message_extracts_nothing(self):
        """用户真实语句（根因复现）：修前 {'month':4,'day':5,'city':'北京'}
        → 修后必须为 {}。"""
        h = make_handler()
        got = h._extract_partial_birth(OFFER_MSG)
        assert got == {}, f"offer 文本仍被当生辰提取: {got}"

    def test_user_offer_message_extracts_nothing_full_extractor(self):
        """同族收口：全量提取器 `_extract_bazi_info` 同一条原话也必须是 None
        （修前 "4.5%" → month=4/day=5 形态同样成立）。"""
        h = make_handler()
        assert h._extract_bazi_info(OFFER_MSG) is None

    @pytest.mark.parametrize("text", [
        "个人企业年金各缴纳4.5%",
        "公积金按12%缴纳，公司缴纳7%",
        "月薪20000元，年终奖另算",
        "房贷利率4.9%，等额本息",
        "合同约定违约金5.5万元",
        "这个项目ROI是3.5倍",
        "体脂率18.5%，体重120斤",
    ])
    def test_percent_money_salary_text_rejected(self, text):
        """① 百分比/小数/金额/薪资类数字不得当日期（brief 明确点名 4.5%）。"""
        h = make_handler()
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, f"{text!r} → {got}"
        assert h._extract_bazi_info(text) is None

    def test_city_in_business_parens_not_taken(self):
        """② 括号里的业务/办公地名不是出生地（用户原话的「（北京…）」）。"""
        h = make_handler()
        got = h._extract_partial_birth(
            "我在一家公司（北京，五险一金顶格）做产品经理，帮我看看事业")
        assert "city" not in got, f"括号业务地名被当出生地: {got}"

    def test_city_in_office_context_not_taken_without_birth_word(self):
        """② 城市只在出生语境取：纯职场语境（办公地/base）不取城市。"""
        h = make_handler()
        got = h._extract_partial_birth(
            "我办公地在上海，公司在深圳，平时出差去杭州")
        assert "city" not in got, f"职场地名被当出生地: {got}"

    def test_isolated_numbers_not_a_date(self):
        """③ 整体必须像在说生辰：孤立数字/编号不成日期。"""
        h = make_handler()
        got = h._extract_partial_birth("订单号 4、5 两件商品，一共 3、6 个包裹")
        assert "month" not in got and "day" not in got, got


class TestP0GateKeepsRealBirthText:
    """双向用例：真实 F2 场景必须照旧提取（体验绝不回退）。"""

    def test_full_solar_date_still_extracted(self):
        """brief 点名必保：`1999年3月28日 早上十点 长春` 仍要提取。"""
        h = make_handler()
        got = h._extract_partial_birth("1999年3月28日 早上十点 长春")
        assert got.get("year") == 1999
        assert (got.get("month"), got.get("day")) == (3, 28)

    def test_age_still_inferred(self):
        """brief 点名必保：`我今年50岁` → 年份推算。"""
        h = make_handler()
        assert h._extract_partial_birth("我今年50岁了",
                                        current_year=2026)["year"] == 1976

    def test_oral_gender_still_extracted(self):
        """brief 点名必保：`我是女孩儿，不是男孩` → 女（G1 口语词扩展）。"""
        h = make_handler()
        assert h._extract_partial_birth("我是女孩儿，不是男孩")["gender"] == "女"

    def test_city_in_birth_context_still_taken(self):
        """城市在出生语境（"出生"）仍取最内层"XX市"（既有 test_partial_birth
        同款用例）。"""
        h = make_handler()
        got = h._extract_partial_birth("三月初三，吉林省长春市榆树市出生，男")
        assert got.get("city") == "榆树市"

    def test_full_extractor_keeps_plain_formats(self):
        """全量提取器既有格式零回退（dash 年份 / 显式城市 / 性别）。"""
        h = make_handler()
        assert h._extract_bazi_info("1990-05-20 15:00 深圳 女") == (
            1990, 5, 20, 15, 0, "深圳", "女")
        assert h._extract_bazi_info("1990年5月20日 15点 广州市 男") == (
            1990, 5, 20, 15, 0, "广州市", "男")

    def test_month_day_with_explicit_cn_form_still_taken(self):
        """完整日期形态（月+日）照旧提取——闸门只拦"不像日期"的数字。"""
        h = make_handler()
        got = h._extract_partial_birth("我4月5日出生")
        assert (got.get("month"), got.get("day")) == (4, 5)

    def test_birth_text_mixed_with_salary_keeps_birth(self):
        """混排：生日与薪资同句 → 生日照取、薪资不被当日期（局部闸门，
        不是整条消息一封了之）。"""
        h = make_handler()
        got = h._extract_partial_birth("我1999年3月28日生的，现在月薪20000元")
        assert got.get("year") == 1999
        assert (got.get("month"), got.get("day")) == (3, 28)


# ================================================================
# 2) P2 先问后写（对话路径）
# ================================================================

ARCHIVE = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
           "city": "北京", "gender": "男"}


def _h_bazi(**kw) -> MessageHandler:
    """_handle_bazi 装配（同 tests/test_bazi_archive_priority.make_handler）。"""
    h = object.__new__(MessageHandler)
    h.engine = None
    h.llm = None
    h.dao = None
    h.retriever = None
    h.memory = None
    h.memory_system = None
    h.session_dao = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    from unittest.mock import Mock
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=dict(ARCHIVE))
    h._do_bazi_analysis = Mock(return_value="分析结果")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="")
    h._gen_gender_correction_ack = Mock(return_value="收到")
    h._gen_birth_conflict_ask = Mock(return_value="您刚说的和档案不一致，先确认一下？")
    h._quick_flash = Mock(return_value="")
    h._emit_stream_event = Mock()
    h._save_bazi_records = Mock()
    h._persist_chart_result = Mock()
    h._sync_person_profile = Mock()
    for k, v in kw.items():
        setattr(h, k, v)
    return h


class TestAskBeforeWrite:
    """k48-r3 分层口径（对话层"先问后写"）：

    - **无出生语境的零散/含混**冲突 → 问（本类主用例）；
    - **显式出生陈述**（带 出生/生的/农历… 等语境词，含不带年份的月日陈述）
      → 直接写 + 排盘（见 TestExplicitStatementWritesDirectly，k9_B1 行为）；
    - **年份大差**冲突恒问（k19 阈值，与 B3-1 规则3 同源）；
    - 存储层不参与判定（`person_dao` 恢复 k19 语义，见 TestStorageKeepsK19）。
    """

    def test_conflict_month_day_asks_and_does_not_chart(self):
        """无出生语境的零散月日冲突 → 必被问且**不写不排**（用户档案
        1990-05-20，消息只说「3月8日」——正是从非出生文本抠出月日的形态）。"""
        h = _h_bazi()
        out = h._handle_bazi("帮我排个盘，3月8日", "u1")
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_bazi_analysis.assert_not_called()
        assert out == "您刚说的和档案不一致，先确认一下？"

    def test_conflict_year_asks(self):
        """年份冲突（既有行为保留，B3-1 规则3）：档案 1990，消息 1995 → 问一句。"""
        h = _h_bazi()
        h._handle_bazi("我是1995年3月8日出生的", "u1")
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_bazi_analysis.assert_not_called()

    def test_conflict_city_asks(self):
        """无出生语境的零散城市冲突（新值 vs 已存城市）→ 问一句。"""
        h = _h_bazi()
        h._handle_bazi("帮我排个盘，广州", "u1")
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_bazi_analysis.assert_not_called()

    def test_explicit_correction_writes_directly(self):
        """明示纠正句式 → 直接写（不问）——与 k19 守卫同源豁免口径。"""
        h = _h_bazi()
        h._handle_bazi("之前填错了，其实是1995年3月8日出生的", "u1")
        h._gen_birth_conflict_ask.assert_not_called()
        h._do_bazi_analysis.assert_called_once()

    def test_non_conflicting_supplement_not_asked(self):
        """双向用例：非冲突的渐进累积**不被问**（体验不变）——档案 1990-05-20
        已有时辰，消息只补时辰 → 直接排。"""
        h = _h_bazi()
        h._handle_bazi("我出生时辰是上午11点", "u1")
        h._gen_birth_conflict_ask.assert_not_called()
        h._do_bazi_analysis.assert_called_once()
        assert h._do_bazi_analysis.call_args[0][0] == 1990

    def test_same_values_not_asked(self):
        """重复报同一生辰（无冲突）→ 不问。"""
        h = _h_bazi()
        h._handle_bazi("我是1990年5月20日出生的", "u1")
        h._gen_birth_conflict_ask.assert_not_called()
        h._do_bazi_analysis.assert_called_once()

    def test_incomplete_archive_still_accumulates_silently(self):
        """档案不完整（无月日）→ 走 F2 渐进累积，绝不问"冲突"（无既有值
        可比 = 无冲突）。"""
        h = _h_bazi()
        h._get_user_birth_profile = lambda uid: {"year": 1990, "month": None,
                                                 "day": None}
        h._handle_bazi("我是3月8日出生的", "u1")
        h._gen_birth_conflict_ask.assert_not_called()


# ================================================================
# 5) k48-r2 复审轮（C-1 / I-1 / I-2 / I-3 / I-4 + Minor）
# ================================================================

class TestC1GateFormIsOnCandidateNotMessage:
    """C-1（Critical）：闸门③ 原为**整条消息**判据（`\\d{4}\\s*年`）→ 全量提取器
    本就要求 4 位年份 ⇒ ③ 恒真、实际只剩①黑名单在挡。改为对**日期候选本身**
    的形态要求 + 候选附近出生语境，裸 4.5/4.5k/4.5千/4.5小时/4.5% 一律不是日期。

    审查实测三条（改前 `_extract_bazi_info` 都得 (1999,4,5,北京)）：
    """

    @pytest.mark.parametrize("text", [
        "我1999年生的，offer给4.5k",     # 审查实测：年份+裸小数
        "房租4.5千",                     # 审查实测：裸小数
        "每天睡4.5小时",                 # 审查实测：裸小数
    ])
    def test_review_cases_month_day_never_extracted(self, text):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, f"{text!r} → {got}"
        assert h._extract_bazi_info(text) is None, f"{text!r} 全量提取未作废"

    def test_review_case_keeps_legit_year_only(self):
        """`我1999年生的…` 的**年份**是真实出生陈述 → 保留（月日不得取）。"""
        h = make_handler()
        assert h._extract_partial_birth("我1999年生的，offer给4.5k") == {
            "year": 1999}

    def test_e2e_no_archive_no_chart_no_write(self):
        """C-1 e2e（审查点名）：无档案用户发 `我1999年生的，offer给4.5k`
        → **不得出盘、不得写档**（与事故同型的 1999-04-05 错盘绝不出现）。"""
        h = _h_bazi(_get_user_birth_profile=Mock(return_value=None))
        h._extract_bazi_info = Mock(
            side_effect=lambda m: MessageHandler._extract_bazi_info(h, m))
        out = h._handle_bazi("我1999年生的，offer给4.5k", "u1")
        h._do_bazi_analysis.assert_not_called()
        h._sync_person_profile.assert_not_called()
        assert out == "渐进引导"          # 落回缺什么问什么，不出盘不写档

    @pytest.mark.parametrize("text,expect", [
        # I-3：分隔符形态真正支持（带出生语境）——改前被静默废弃
        ("我11.20出生", (11, 20)),
        ("我5/20出生", (5, 20)),
        ("我8-15出生", (8, 15)),
        ("我10.10生日", (10, 10)),
    ])
    def test_i3_ambiguous_separator_forms_supported_with_birth_ctx(self, text,
                                                                   expect):
        h = make_handler()
        got = h._extract_partial_birth(text)
        assert (got.get("month"), got.get("day")) == expect, f"{text!r} → {got}"

    @pytest.mark.parametrize("text", [
        "各缴纳4.5%", "每天睡4.5小时", "房租4.5千", "offer给4.5k",
    ])
    def test_i3_ambiguous_forms_still_rejected_without_birth_ctx(self, text):
        """反向：同样的分隔符形态但**无出生语境**（或语境太远）→ 仍不取。"""
        h = make_handler()
        got = h._extract_partial_birth(text)
        assert "month" not in got and "day" not in got, f"{text!r} → {got}"

    def test_birth_ctx_must_be_near_candidate_not_anywhere(self):
        """出生语境必须**紧邻候选**：`我1999年生的，offer给4.5k` 里 "生的"
        离候选很远 → 不构成放行（否则闸门形同虚设）。"""
        h = make_handler()
        got = h._extract_partial_birth("我1999年生的，offer给4.5k")
        assert "month" not in got and "day" not in got, got

    def test_i2_prefers_first_accepted_candidate(self):
        """I-2：只取首个候选 → 被否决即丢整条月日。改为遍历全部候选后取
        **首个通过闸门的**（不是"最后一个"——最后一个会误取婚期；
        未来日期由 MessageAnalyzer.birth_dates_all_future 单独兜底）。"""
        h = make_handler()
        got = h._extract_partial_birth("房贷利率4.9%，我1990年5月20日出生")
        assert (got.get("year"), got.get("month"), got.get("day")) == \
            (1990, 5, 20), got
        info = h._extract_bazi_info("房贷利率4.9%，我1990年5月20日出生")
        assert info is not None and info[:3] == (1990, 5, 20), info

    def test_i2_bogus_candidate_must_not_swallow_real_one(self):
        r"""I-2 家族：`1985.3.28 出生 男` 的首个正则候选在无 `(?<!\d)` 时是
        `85.3`（month=85 无效）——若它把后面的 `3.28` 一起消耗掉，整条月日
        就丢了（改前实测）。现在候选须**同时**过闸门与合法月日校验。"""
        h = make_handler()
        got = h._extract_partial_birth("1985.3.28 出生 男")
        assert (got.get("month"), got.get("day")) == (3, 28), got

    def test_i3_full_separator_form_also_works_in_partial(self):
        """I-3：`1990-05-20` 这类分隔符形态在**部分**提取器同样不得被静默
        废弃（改前部分提取器把它丢成 {}，与全量提取器口径分裂）。"""
        h = make_handler()
        got = h._extract_partial_birth("1990-05-20 15:00 深圳 女")
        assert (got.get("month"), got.get("day")) == (5, 20), got
        assert got.get("city") == "深圳"

    def test_strong_form_needs_no_birth_context(self):
        """双向：**带日期单位**的强形态（M月D日 / YYYY-M-D）无出生词也照取
        （既有格式零回退：「1990-05-20 15:00 深圳 女」）。"""
        h = make_handler()
        assert h._extract_bazi_info("1990-05-20 15:00 深圳 女") == (
            1990, 5, 20, 15, 0, "深圳", "女")
        assert h._extract_partial_birth("我4月5日出生")["month"] == 4


class TestI1ConfirmCanBeAnswered:
    """I-1（Important）：G1 场景改前"问=1、排=0、纠正=0"，且按提示重发完整
    生辰→再被问（循环）、回「确认」→按旧档案（错月日+旧性别）排盘。

    修法：① 确认可承接（暂存本轮待更新字段，确认词 → 应用并排盘）；
    ② 更窄收口：完整生辰陈述（年+月+日齐 + 出生语境）视为明示声明直接写，
       只有零散/含混的才走确认问句。"""

    def test_complete_statement_writes_directly_no_ask(self):
        """② 完整生辰陈述（年+月+日 + 出生词）→ 直接写，不问。

        fixture 与 G1 实测同型：档案月日是**错的占位值** 1990-02-26，
        消息给真生辰 1990-05-20 —— 改前（k48 首版）会被确认问句拦下，
        现在必须排盘+纠正。"""
        h = _h_bazi(_get_user_birth_profile=Mock(return_value={
            "year": 1990, "month": 2, "day": 26, "hour": 7, "minute": 0,
            "city": "北京", "gender": "男"}))
        h._handle_bazi("我是1990年5月20日7点北京生的女孩儿", "u9")
        h._gen_birth_conflict_ask.assert_not_called()
        h._do_bazi_analysis.assert_called_once()
        args = h._do_bazi_analysis.call_args[0]
        assert (args[1], args[2], args[6]) == (5, 20, "女"), args[:7]

    def test_explicit_lunar_md_statement_writes_directly(self):
        """k9_B1 行为恢复（r3 全量回归实锤的回归点）：档案 solar 1990-05-20 +
        「**我是农历腊月廿六出生的**」（不带年份的月日陈述，带出生语境）→
        **直接写 + 排盘**，绝不被确认问句挡下。"""
        h = _h_bazi()
        h._handle_bazi("我是农历腊月廿六出生的", "u9")
        h._gen_birth_conflict_ask.assert_not_called()
        h._do_bazi_analysis.assert_called_once()
        args = h._do_bazi_analysis.call_args[0]
        # 引擎收**公历**（R2-6 单点转换）：原值 1990-12-26（lunar）→ 1991-02-10
        assert (args[0], args[1], args[2]) == (1991, 2, 10), args[:5]

    def test_explicit_md_statement_writes_directly(self):
        """同上：带出生语境的月日陈述「我是3月8日出生的」→ 直接写（显式陈述
        的口径 = **有出生语境词**，不要求年份齐全）。"""
        h = _h_bazi()
        h._handle_bazi("我是3月8日出生的", "u9")
        h._gen_birth_conflict_ask.assert_not_called()
        assert h._do_bazi_analysis.call_args[0][1:3] == (3, 8)

    def test_fragment_conflict_still_asks(self):
        """反向：**无出生语境**的零散月日冲突**仍要问**（本项要保的行为）。"""
        h = _h_bazi()
        h._handle_bazi("帮我排个盘，3月8日", "u9")
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_bazi_analysis.assert_not_called()

    def test_year_conflict_of_complete_statement_still_guarded(self):
        """边界（与 k19 年份守卫同口径）：完整陈述但**年份大差**（>2）仍拦
        ——21:44 事故正是 4 年跳变；② 的"直接写"只放行 月/日/城市，
        年份仍由 k19 阈值守护。"""
        h = _h_bazi()
        h._handle_bazi("我是1995年3月8日出生的", "u9")
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_bazi_analysis.assert_not_called()

    def test_pending_confirm_applies_and_charts(self):
        """① 确认可承接：问句发出后回「确认」→ 应用待更新值并排盘（不再循环）。"""
        h = _h_bazi()
        h._handle_bazi("帮我排个盘，3月8日", "u9")   # 无出生语境 → 问
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_bazi_analysis.reset_mock()
        h._handle_bazi("确认", "u9")                 # 承接
        h._do_bazi_analysis.assert_called_once()
        args = h._do_bazi_analysis.call_args[0]
        assert (args[1], args[2]) == (3, 8), f"应应用待更新月日，实收 {args[:3]}"

    def test_pending_archive_choice_charts_with_archive(self):
        """① 反向：回「按档案」→ 用档案排（既有语义），且待更新值作废。"""
        h = _h_bazi()
        h._handle_bazi("帮我排个盘，3月8日", "u9")
        h._do_bazi_analysis.reset_mock()
        h._handle_bazi("按档案", "u9")
        h._do_bazi_analysis.assert_called_once()
        args = h._do_bazi_analysis.call_args[0]
        assert (args[1], args[2]) == (5, 20), f"应按档案，实收 {args[:3]}"

    def test_pending_stale_dropped_on_unrelated_message(self):
        """① 反向：待更新值不得跨轮误伤——用户改说别的（非确认词）→ 作废。"""
        h = _h_bazi()
        h._handle_bazi("帮我排个盘，3月8日", "u9")
        h._do_bazi_analysis.reset_mock()
        h._handle_bazi("我出生时辰是上午11点", "u9")   # 非确认词 → 正常叠加
        h._do_bazi_analysis.assert_called_once()
        args = h._do_bazi_analysis.call_args[0]
        assert (args[1], args[2]) == (5, 20), f"待更新值应作废，实收 {args[:3]}"


class TestI4ZiweiConflictAsks:
    """I-4：紫微路径冲突时静默不写、盘面照排 → 盘面与档案分裂。
    修：与 _handle_bazi 同一判定点（先问后写）。"""

    def _h_ziwei(self, **kw):
        h = object.__new__(MessageHandler)
        h.engine = None
        h.llm = None
        h.dao = None
        h.retriever = None
        h.memory = None
        h.memory_system = None
        h.session_dao = None
        h._downgraded = {}
        h._analysis_facts = {}
        h._extract_bazi_info = Mock(
            return_value=(1995, 3, 8, 10, 0, "北京", "女"))
        h._get_user_birth_profile = Mock(return_value=dict(ARCHIVE))
        h._gen_birth_conflict_ask = Mock(return_value="先确认一下？")
        h._do_ziwei_analysis = Mock(return_value="紫微分析")
        for k, v in kw.items():
            setattr(h, k, v)
        return h

    def test_ziwei_conflict_asks(self):
        """年份冲突（>2，完整陈述也拦）→ 问一句，不排紫微。"""
        h = self._h_ziwei()
        out = h._handle_ziwei("帮我排紫微，1995年3月8日10点北京女", "u1")
        h._gen_birth_conflict_ask.assert_called_once()
        h._do_ziwei_analysis.assert_not_called()
        assert out == "先确认一下？"

    def test_ziwei_guard_fail_open_on_read_error(self):
        """守卫是安全网：档案读取失败 → 放行照排，不得让紫微整体失败
        （Mock dao 装配实测过这条：`_get_user_birth_profile` 抛异常
        → 曾把整轮变成"服务暂时不可用"）。"""
        h = self._h_ziwei(_get_user_birth_profile=Mock(
            side_effect=RuntimeError("boom")))
        h._handle_ziwei("帮我排紫微，1995年3月8日10点北京女", "u1")
        h._do_ziwei_analysis.assert_called_once()
        h._gen_birth_conflict_ask.assert_not_called()

    def test_ziwei_no_conflict_charts(self):
        """双向：与档案一致 → 照排紫微（既有行为零回退）。"""
        h = self._h_ziwei(_extract_bazi_info=Mock(
            return_value=(1990, 5, 20, 10, 0, "北京", "男")))
        h._handle_ziwei("帮我排紫微，1990年5月20日10点北京男", "u1")
        h._gen_birth_conflict_ask.assert_not_called()
        h._do_ziwei_analysis.assert_called_once()


class TestMinorGuardAndPII:
    """Minor：①守卫同类误报（承诺"保证收益"…是骗子）+ 后窗口放行真承诺
    （跟着我买，保证收益翻倍，请警惕风险）；②守卫日志不得含原文片段（PII）。"""

    def test_warning_about_promise_with_pianzi_exempt(self):
        """警告性表述（…是骗子）不算违规。"""
        assert _violations('承诺"保证收益"的都是骗子，别信。') == []

    def test_real_promise_with_trailing_warning_still_caught(self):
        """真承诺 + 尾巴挂个"请警惕风险"→ 仍判违规（后窗口不得放行）。"""
        assert _violations("跟着我买，保证收益翻倍，请警惕风险。")

    def test_guard_log_has_no_raw_message(self, tmp_path, caplog):
        """守卫日志 PII：年份告警日志**不得含原文片段**（只记 ctx_len/ctx_kind）。"""
        from src.storage.person_dao import PersonDAO
        pdao = PersonDAO(str(tmp_path / "u.db"))
        _mk_default(pdao)
        p = pdao.list_persons("u1")[0]
        secret = "我住在北京市朝阳区某小区3号楼501"
        with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
            pdao.update_person("u1", p["id"], birth={"birth_year": 1995},
                               birth_ctx=secret)
        assert "④-4" in caplog.text
        assert secret[:8] not in caplog.text, "日志含原文片段（PII）"
        assert "朝阳区" not in caplog.text, "日志含原文片段（PII）"


# ================================================================
# 2b) P2 存储层兜底：冲突写入被拦（"不写"的另一半）
# ================================================================

from src.storage.person_dao import (  # noqa: E402
    PersonDAO, FORM_EXPLICIT_CTX, birth_conflict_fields, is_correction_text,
)


def _mk_default(pdao, user_id="u1", **over):
    b = {"gender": "女", "birth_year": 1990, "birth_month": 5,
         "birth_day": 20, "birth_hour": 15, "birth_minute": 0,
         "calendar": "solar", "city": "北京"}
    b.update(over)
    return pdao.create_person(user_id, name="我", relation="自己",
                              is_default=True, birth=b)


class TestStorageKeepsK19:
    """k48-r3 分层修正：**存储层恢复 k19 语义**——只有年份守卫（warn-only +
    明示/表单豁免），**不新增任何拒绝/拦截**。存储层是被调用方，永远接受
    显式写入；"要不要先问用户"是对话层 `handler._handle_bazi` 的事。

    事故复盘（k48 首版把"先问后写"下沉到本层）：开始拒绝**合法**写入
    （普通改城市 / 仅翻真太阳时开关 / 月日增补）→ 全量回归 8 条真回归。"""

    def test_month_day_update_writes(self, tmp_path):
        """合法月日更新（含无出生语境的普通写入）→ **照写**。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao)
        back = pdao.update_person("u1", p["id"],
                                  birth={"birth_month": 3, "birth_day": 8},
                                  birth_ctx="帮我排个盘，3月8日")
        assert (back["birth_month"], back["birth_day"]) == (3, 8)
        assert pdao.get_person("u1", p["id"])["birth_month"] == 3

    def test_city_update_writes(self, tmp_path):
        """普通改城市（k11c/常规档案维护）→ **照写**，存储层不拦。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao)
        back = pdao.update_person("u1", p["id"], birth={"city": "广州市"})
        assert back["city"] == "广州市"

    def test_solar_time_flip_writes(self, tmp_path):
        """仅翻真太阳时开关（k11c 实测回归）→ **照写**（birth_enc 全量替换
        含既有 city，不得因"城市未变"类判据被拦）。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao, solar_time=1)
        back = pdao.update_person("u1", p["id"], birth={"solar_time": 0},
                                  birth_ctx="")
        assert back["solar_time"] == 0
        assert back["city"] == "北京", "翻开关不得丢既有字段"

    def test_year_guard_warn_only_writes(self, tmp_path, caplog):
        """年份大差（>2，非明示）→ k19 语义 = **告警但照写**（绝不拒绝）。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao)
        with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
            back = pdao.update_person("u1", p["id"], birth={"birth_year": 1995})
        assert back["birth_year"] == 1995, "存储层不得拦截合法写入"
        assert "④-4" in caplog.text and "告警" in caplog.text
        assert "拒绝" not in caplog.text

    def test_explicit_correction_writes(self, tmp_path):
        """明示纠正句式 → 直接写（左移不误伤真纠正）。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao)
        back = pdao.update_person("u1", p["id"],
                                  birth={"birth_month": 3, "birth_day": 8},
                                  birth_ctx="之前填错了，其实是3月8日出生的")
        assert (back["birth_month"], back["birth_day"]) == (3, 8)

    def test_form_sentinel_writes(self, tmp_path):
        """表单哨兵（用户亲手编辑）→ 直接写。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao)
        back = pdao.update_person("u1", p["id"], birth={"city": "广州市"},
                                  birth_ctx=FORM_EXPLICIT_CTX)
        assert back["city"] == "广州市"

    def test_progressive_fill_of_missing_field_writes(self, tmp_path):
        """双向用例：渐进补全**缺失**字段（非冲突）→ 照写（体验不变）。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao, birth_hour=None, city="")
        back = pdao.update_person("u1", p["id"],
                                  birth={"birth_hour": 11, "city": "长春"},
                                  birth_ctx="我出生时辰是上午11点，吉林长春")
        assert back["birth_hour"] == 11 and back["city"] == "长春"

    def test_pure_year_guard_small_gap_writes(self, tmp_path):
        """年份差 ≤2（年龄推算噪声带）→ 不算冲突，照写（k19 阈值同口径）。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        p = _mk_default(pdao)
        back = pdao.update_person("u1", p["id"], birth={"birth_year": 1991},
                                  birth_ctx="我今年35岁")
        assert back["birth_year"] == 1991

    def test_non_default_person_not_guarded(self, tmp_path):
        """非默认命主（家人/朋友档案）生日本就不同 → 不受守卫。"""
        pdao = PersonDAO(str(tmp_path / "u.db"))
        _mk_default(pdao)
        friend = pdao.create_person(
            "u1", name="爸爸", relation="父母", is_default=False,
            birth={"gender": "男", "birth_year": 1968, "birth_month": 5,
                   "birth_day": 13, "calendar": "solar", "city": "上海"})
        back = pdao.update_person("u1", friend["id"], birth={"city": "广州市"})
        assert back["city"] == "广州市"


class TestBirthConflictFieldsSingleSource:
    """判定实现唯一（k48 同族收口）：handler 问与 person_dao 拦同一函数。"""

    def test_year_gap_threshold(self):
        ex = {"birth_year": 1990}
        assert birth_conflict_fields(ex, {"year": 1995}) == ["year"]
        assert birth_conflict_fields(ex, {"year": 1991}) == []      # ≤2 噪声带
        assert birth_conflict_fields(ex, {"birth_year": 1995}) == ["year"]

    def test_month_day_and_city(self):
        ex = {"birth_year": 1990, "birth_month": 5, "birth_day": 20,
              "city": "北京"}
        assert birth_conflict_fields(ex, {"month": 3, "day": 8}) == ["month_day"]
        assert birth_conflict_fields(ex, {"city": "广州"}) == ["city"]
        assert birth_conflict_fields(ex, {"month": 5, "day": 20}) == []
        # 只报月（不带日）但既有档案有月 → 月不一致仍是冲突
        assert birth_conflict_fields(ex, {"month": 3}) == ["month_day"]
        assert birth_conflict_fields(ex, {"day": 25}) == ["month_day"]
        assert birth_conflict_fields(ex, {"city": ""}) == []
        # 既有侧缺该字段 = 补全（F2 渐进累积正常流程）→ 不是冲突
        assert birth_conflict_fields({"birth_year": 1990}, {"month": 3}) == []
        assert birth_conflict_fields({"birth_year": 1990, "city": ""},
                                     {"month": 3, "city": "长春"}) == []

    def test_correction_and_no_archive_exempt(self):
        ex = {"birth_year": 1990, "birth_month": 5, "birth_day": 20}
        assert birth_conflict_fields(ex, {"year": 1995},
                                     ctx="其实是1995年") == []
        assert birth_conflict_fields(ex, {"year": 1995},
                                     ctx=FORM_EXPLICIT_CTX) == []
        assert birth_conflict_fields(None, {"year": 1995}) == []
        assert is_correction_text("我3月8日出生的") is False


# ================================================================
# 3) P0.5 流式出口：替换 vs 续写（服务端保险）
# ================================================================

from src.api.chat_stream import compute_stream_remaining  # noqa: E402

POLISHED = (  # 已流出的"润色版"完整回答（一个像样的完整回答）
    "己卯 己巳 乙丑 壬午。你的命盘四柱齐整，日主乙木生于巳月，"
    "食伤当令而思虑细腻，做事讲究条理，追求可见的成效。"
    "大运顺行入财乡，中年以后积蓄渐丰。"
)
ENGINE_DRAFT = (  # D2 回退的"引擎原稿"——与润色版两条文本完全不同
    "根据您的出生信息：1999年4月5日 北京。四柱为 己卯 丁卯 丁亥 乙巳。"
    "日主丁火生于卯月，印星当令，性格温润而有主见，为人重情重义。"
    "今年流年丙午，与日支相合，事业上有贵人相助之象。"
)


class TestStreamReplacementNoDoublePost:
    def test_d2_replacement_not_appended(self):
        """D2 双盘形态：流出一条完整润色稿 + 定稿是完全不同的引擎原稿
        （重叠 0）→ **不整条追加**（交由前端 done 整泡替换）。"""
        assert compute_stream_remaining(ENGINE_DRAFT, POLISHED) == ""

    def test_short_streamed_still_full_append(self):
        """双向用例：正文几乎没流出（已流文本很短）→ **仍整条补发**，
        绝不因新判据丢内容。"""
        reply = "今天运势整体不错，宜积极行动。忌冲动消费。"
        for short in ("草稿流与润色稿开头完全不同。这只是一段草稿尾巴。",
                      "欢迎回来，今天想聊点什么？",
                      "前面草稿内容。"):
            assert compute_stream_remaining(reply, short) == reply, short

    def test_true_continuation_appends_only_diff(self):
        """双向用例：真续写（流尾 == 定稿头）→ 只补差集。"""
        streamed = "己卯 己巳 乙丑 壬午。你的命盘四柱齐整。"
        reply = streamed + "大运顺行入财乡，中年以后积蓄渐丰。"
        assert compute_stream_remaining(reply, streamed) == \
            "大运顺行入财乡，中年以后积蓄渐丰。"

    def test_threshold_is_named_constant(self):
        """阈值必须是具名常量（报告可引用、后续可调），不是魔法数字。"""
        import src.api.chat_stream as cs
        assert isinstance(cs.REPLACEMENT_MIN_STREAMED, int)
        assert cs.REPLACEMENT_MIN_STREAMED == 40


# ================================================================
# 4) P1 准确性守卫：否定/劝阻语境
# ================================================================

from src.validators.response_checker import ResponseValidator  # noqa: E402


def _violations(text: str) -> list:
    return ResponseValidator().validate(text, engine_data_used=True)["violations"]


class TestAccuracyGuardNegation:
    def test_user_case_warn_against_cuanshuo_not_a_violation(self):
        """用户实测原话（20:11:24 误报源头）：提醒用户别被"稳赚"话术带走
        → 不是违规（否定/劝阻语境）。"""
        text = ('命理上讲求的是趋吉避凶，而不是听人许诺。若有人跟您说'
                '某个项目“稳赚”，请务必警惕——别被“稳赚”话术带走，'
                '任何投资都要独立判断。')
        assert _violations(text) == [], _violations(text)

    @pytest.mark.parametrize("text", [
        '切勿相信“稳赚不赔”的说法。',
        '谨防打着“保证收益”旗号的骗局。',
        '不要相信任何“稳赚”的承诺。',
        '警惕“内幕消息”四个字。',
        '别被“绝对会翻身”这类话术影响判断。',
    ])
    def test_warning_contexts_exempt(self, text):
        """劝阻/否定语境（别被/不要/警惕/谨防/不要相信）不算违规。"""
        assert _violations(text) == [], (text, _violations(text))

    @pytest.mark.parametrize("text", [
        "这个项目稳赚不赔，跟着我买就对了。",
        "跟着我买，保证收益翻倍。",
        "老师带单，必涨，内幕消息。",
    ])
    def test_real_violations_still_caught(self, text):
        """真违规（承诺性表述）仍要拦——守卫只收窄误报，不放走真违规。"""
        assert _violations(text), text

    def test_negation_far_from_match_not_exempt(self):
        """否定词与命中词不同语境（"别错过，稳赚不赔"）→ 仍判违规
        （否定的必须是这句话本身，不是别的句子）。"""
        assert _violations("别错过这个活动，稳赚不赔。")

    def test_unrelated_safe_text_no_violation(self):
        """普通回复零违规（守卫不误伤正常内容）。"""
        assert _violations("您的命盘日主乙木，宜稳中求进，忌冲动。") == []
