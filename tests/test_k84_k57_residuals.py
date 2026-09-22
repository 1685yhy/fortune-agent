# -*- coding: utf-8 -*-
"""k84-必修3：**k49-review 登记的 6 条残留**（"用户说了生日但系统没听对"）的收口门禁。

背景（**先把话说准**）：控制方任务书把这 6 条标为「k57 残留 6 条」，但它们的
**权威来源是 k49 审查登记的 6 条残留**，逐条对应 `tests/test_k50_residuals.py`
的 K50-1..K50-6（该文件 docstring 首行即"k50：k49 审查登记的 6 条残留收口"，
且样例串与本任务书的描述逐字相同：`我不是腊月廿六生的，是正月初一生的` /
`我1991年7月8日在酒泉市人民医院出生` / `5月13日，印象中是` / `1991年7月8日 个人 男` /
`我出生在贵阳，1991年7月8日在长春市结的婚` / 工具路径"缺省北京"）。
k50 已把 6 条**全部收口**并锁定（`test_k50_residuals.py` 本批实测 201/201 绿）。

本批（k84）逐条**重新复现**后的结论 —— 三条**真的还在**、三条确已收口：

  | # | 主题 | k84 复现结论 |
  |---|------|--------------|
  | ① | 中文数字月日 × 否定裁决 | **已收口**（K50-1），本文件反向钉住 |
  | ② | 城市库外地名（酒泉/日照/日喀则）| **已收口**（K50-2），本文件反向钉住 |
  | ③ | F2 会话式应答丢月日 | **仍有漏网**：`天刚要黑` 一类**两个前缀字**的时段词 |
  | ④ | `个人`/`次数` 被单位黑名单拦 | **仍有漏网**：**无空格**紧贴单字量词时 |
  | ⑤ | 婚期句里的城市被采纳 | **仍有漏网**：城市独占小句时**隔句借**出生语境 |
  | ⑥ | 工具路径 `city=="北京"` 缺省豁免 | **仍有漏网**：原文城市对守卫**不可见** |

三条漏网的根因是同一个形状：**判据读的是"位置/形状的近似"而不是它自己写下的口径**
（③ 副词位只吃一个字；④ 判"紧贴候选"而非"紧贴数字"；⑤ 判"消息级有语境"而非
"邻句有语境"；⑥ 只读模型参数、不读用户原文）。修法一律**结构化**（不加词表、不改
`_NON_DATE_UNIT_RE` 的条目），并逐条给出**双向**（修的 + 不许回退的）。

隔离：纯提取层 + tmp_path 真实 SQLite；零网络零 LLM；不开生产库；不跑全量。
"""
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import pytest  # noqa: E402

from src.bot.handler import (  # noqa: E402
    MessageHandler, _NON_DATE_UNIT_RE, _non_date_unit_blocks,
)

REPO = Path(__file__).resolve().parent.parent


def _k50():
    """k50 门禁模块（**复用**它的 harness 与 6 主题的权威样例，不复制一份）。"""
    spec = importlib.util.spec_from_file_location(
        "k50mod_for_k84", REPO / "tests" / "test_k50_residuals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _p(msg, **kw):
    return object.__new__(MessageHandler)._extract_partial_birth(msg, **kw)


def _md(msg):
    g = _p(msg)
    return (g.get("month"), g.get("day"))


# ══════════════════════════════════════════════════════════════════════
# ① / ② 反向钉住：k50 已收口的两条不得回退（权威样例原串）
# ══════════════════════════════════════════════════════════════════════

class TestAlreadyClosedByK50AreStillClosed:
    @pytest.mark.parametrize("msg,expect", [
        # K50-1（k50 门禁里的原串；本批逐条实测复现，原始输出见报告）
        ("我不是腊月廿六生的，是正月初一生的", (1, 1)),
        ("我是正月初一生的，不是腊月廿六生的", (1, 1)),
        ("不是正月初一，是腊月廿六生的", (12, 26)),
    ])
    def test_cn_month_day_enters_the_same_negation_ruling(self, msg, expect):
        assert _md(msg) == expect, (msg, _md(msg))

    def test_all_negated_cn_month_day_is_still_not_taken(self):
        assert _md("我不是腊月廿六生的") == (None, None)

    @pytest.mark.parametrize("msg,expect", [
        ("我1991年7月8日在酒泉市人民医院出生", "酒泉市"),
        ("我1991年7月8日在日照市人民医院出生", "日照市"),
        ("我1991年7月8日在日喀则市人民医院出生", "日喀则市"),
        ("我出生在酒泉市", "酒泉市"),
        ("我出生在日照市", "日照市"),
        ("我出生在日喀则市", "日喀则市"),
        # 库外、形态合法的更远地名（形态判据，不靠词表）
        ("我1991年7月8日在克拉玛依市出生", "克拉玛依市"),
        ("我1991年7月8日在鄂尔多斯市出生", "鄂尔多斯市"),
    ])
    def test_out_of_db_city_has_no_rizai_zhi_residue(self, msg, expect):
        """值必须**逐字等于**真地名 —— 这本身就排除了 `日在X市` 脏值。

        （不另加"不得以 `日` 开头"的断言：`日照市`/`日喀则市` 本来就以 `日`
         开头，那是真首字，不是日期残尾。）
        """
        got = _p(msg).get("city")
        assert got == expect, f"{msg} → {got!r}（不得是 `日在X市` 形态）"
        assert got == expect and "在" not in str(got), f"城市名里还夹着语境字：{got!r}"

    def test_dropped_date_remnant_still_cleaned(self):
        from src.bot.handler import _clean_city_name
        assert _clean_city_name("日在日照市") == "日照市"
        assert _clean_city_name("日在酒泉市") == "酒泉市"
        assert _clean_city_name("日在日喀则市") == "日喀则市"


# ══════════════════════════════════════════════════════════════════════
# ③ F2 会话式应答：时段词的前缀必须**可叠加**（k84 修）
# ══════════════════════════════════════════════════════════════════════

class TestSessionReplyKeepsMonthDay:
    """③：`5月13日，<会话式应答>` 的月日不得丢。

    改前的洞：口语化时段 token 是 `天(?:快|刚|才|已|都|就要|要)?(?:黑|亮)` ——
    副词位只吃**一个**字符或整词 `就要`，`刚要`（副 + 助动两个）吃不到 ⇒ 残留
    "刚要黑" ⇒ 整条不再是"只有日期" ⇒ **月日被丢**。改为可叠加的闭类
    `天(?:快|刚|才|已|都|就|要)*(?:黑|亮)`（结构判据，零词表新增）。
    """

    @pytest.mark.parametrize("reply", [
        # k50-3 原有的三条（不许回退）
        "印象中是", "可能是", "天快黑的时候",
        # k50-3 同族（k50 门禁 SESSION_PREV 里的）
        "说不定", "应该是", "保不齐", "也许是", "记不清了",
        # k84 新增覆盖：两个前缀字的时段词
        "天刚要黑", "天快要黑", "天就要黑", "天要黑了", "天都黑了",
        "太阳落山的时候", "天亮了", "天刚亮",
    ])
    def test_bare_month_day_survives_the_reply(self, reply):
        assert _md(f"5月13日，{reply}") == (5, 13), (
            f"`5月13日，{reply}` 丢了月日 —— 时段/应答 token 类没盖住它")

    def test_year_month_day_form_survives(self):
        for reply in ("印象中是", "天刚要黑", "天快要黑"):
            g = _p(f"我1991年7月8日生的，{reply}")
            assert (g.get("year"), g.get("month"), g.get("day")) == (1991, 7, 8), (
                f"带年形态 `{reply}` 丢月日：{g}")

    def test_reply_token_class_does_not_swallow_real_content(self):
        """反向：时段/应答词**不得**把"另有一段叙述"的消息变成"只有日期"。

        `5月13日见客户` 这类（k50-3 声明的反例）必须仍然**不采纳**。
        """
        for msg in ("5月13日见客户", "5月13日开会", "5月13日要出差"):
            assert _md(msg) == (None, None), f"{msg} 被当成了生辰月日"


# ══════════════════════════════════════════════════════════════════════
# ④ 单位黑名单：判据必须是「紧贴**数字**」（k84 修）
# ══════════════════════════════════════════════════════════════════════

class TestUnitBlacklistIsTightToTheDigit:
    """④：`个人`/`次数`/`人次`/`个别` **无空格**紧贴候选时不得丢月日。

    改前的洞：k50-4 的明文口径是"单位词必须紧跟在**数字**之后"，但实现判的是
    "紧贴**候选**"；候选正则把 `日` 吞进候选 ⇒ `…7月8日个人` 的 `个` 被当量词
    ⇒ 月日整条丢弃。而 `…7月8日 个人`（有空格）是好的 —— 差异只在空格，
    说明判据读的是位置而不是"数字后面紧跟着什么"。
    修法：候选以 `日`/`号` 收尾 ⇒ 数字已被日期读走 ⇒ 不做金额判定（零词表新增）。
    """

    @pytest.mark.parametrize("msg", [
        "1991年7月8日个人", "1991年7月8日次数", "1991年7月8日人次 男",
        "1991年7月8日个别 男", "1991年7月8日个人 男", "1991年7月8日次数 男",
        "我1991年7月8日生的 个人 男", "1991年7月8日，个人",
    ])
    def test_no_space_single_char_noun_keeps_month_day(self, msg):
        assert _md(msg) == (7, 8), f"`{msg}` 丢月日（单字量词误判）"

    def test_helper_judges_the_digit_not_the_candidate(self):
        """判据本体：`_non_date_unit_blocks(候选, 尾串)` 看的是候选**最后一个字符**。"""
        assert _non_date_unit_blocks("7月8日", "个人") is False
        assert _non_date_unit_blocks("7月8日", "次数") is False
        assert _non_date_unit_blocks("7月8号", "个人") is False
        # 候选以数字收尾（=单位紧贴数字）→ 照旧拦
        assert _non_date_unit_blocks("4.5", "%") is True
        assert _non_date_unit_blocks("20000", "元") is True
        assert _non_date_unit_blocks("4.5", "k") is True
        assert _non_date_unit_blocks("4.5", "个小时") is True

    @pytest.mark.parametrize("msg", [
        # k48/k50 的主力拦截面：一条都不许放开（含 k50 门禁的原串）
        "房贷利率4.9%，我1990年5月20日出生",
        "我1999年生的，offer给4.5k",
        "我1999年生的，房租4.5千",
        "我1999年生的，月薪20000元",
        "1991年7月8日，个人企业年金各缴纳4.5%",
    ])
    def test_money_ratio_and_measure_are_still_not_dates(self, msg):
        """`4.5%`/`20000元`/`4.5k`/`4.5千` 仍被拦（且不误伤同句真生辰）。"""
        g = _p(msg)
        assert (g.get("month"), g.get("day")) != (4, 5), (
            f"`{msg}` 把 `4.5` 当成了 4月5日（金额/比例拦截面被本批修法放开）")

    def test_real_birth_date_in_the_same_sentence_is_still_taken(self):
        """同句混排：金额被拦、**真生辰照取**（k48 的双向要求）。"""
        g = _p("房贷利率4.9%，我1990年5月20日出生")
        assert (g.get("year"), g.get("month"), g.get("day")) == (1990, 5, 20), g

    def test_unit_regex_entries_unchanged(self):
        """**结构性锁**：本批**没有**改动 `_NON_DATE_UNIT_RE` 的条目（零词表新增）。"""
        assert _NON_DATE_UNIT_RE.pattern == (
            r'^(?:%|％|‰|元|块|万|亿|折|成|倍|薪|元/|/月|每月|个月|小时|分钟|'
            r'个|人|次|件|份|斤|公斤|公里|米|平|岁|年化|利率|k|K|w|W)'), (
            "单位黑名单的条目被改动了 —— 本批的修法是**结构化**（判据收窄到"
            "紧贴数字），不是删/加词条；改了条目必须重跑 k48/k50 双向用例")

    def test_k50_authoritative_cases_still_green(self):
        """k50 门禁里 K50-4 的权威原串（不许回退）。"""
        for msg in ("1991年7月8日 个人 男", "1991年7月8日 次数 男"):
            assert _md(msg) == (7, 8), msg


# ══════════════════════════════════════════════════════════════════════
# ⑤ 婚期句城市：借出生语境只认**邻句**（k84 修）
# ══════════════════════════════════════════════════════════════════════

class TestWeddingClauseCityNotAdopted:
    @pytest.mark.parametrize("msg,expect", [
        # k50-5 权威原串（不许回退）
        ("我出生在贵阳，1991年7月8日在长春市结的婚", "贵阳"),
        ("我出生在贵阳，1990年5月20日，长春市，结的婚", "贵阳"),
        # k84 漏网形态：城市**独占小句**、中间隔着**婚礼小句**
        ("我出生在贵阳，1990年5月20日办的婚礼，在长春市", "贵阳"),
        ("我出生在贵阳，2000年在长春市结的婚，1990年5月20日", "贵阳"),
        ("我生在贵阳市，1990年5月20日在长春市结婚", "贵阳市"),
    ])
    def test_wedding_clause_city_is_not_the_birthplace(self, msg, expect):
        got = _p(msg).get("city")
        assert got == expect, f"{msg} → {got!r}（期望 {expect!r}）"
        assert got != "长春市", f"婚期小句里的城市被当成了出生地：{msg}"

    @pytest.mark.parametrize("msg,expect", [
        # 邻句**确有**出生语境 → 城市照旧可借（不许因本批修法而丢城市）
        ("我来自吉林，长春市", "长春市"),
        ("我是长春市人，1990年5月20日生", "长春市"),
        ("三月初三，吉林省长春市榆树市出生，男", "榆树市"),
    ])
    def test_neighbor_birth_ctx_still_lets_the_city_through(self, msg, expect):
        assert _p(msg).get("city") == expect, msg

    def test_neighbor_helper_reads_only_adjacent_clauses(self):
        """判据本体：`_neighbor_clause_has_birth_ctx` 只认紧邻的前/后小句。"""
        from src.bot.handler import _neighbor_clause_has_birth_ctx
        text = "我出生在贵阳，1990年5月20日办的婚礼，在长春市"
        pos = text.index("在长春市")
        end = pos + len("在长春市")
        from src.storage.person_dao import _clause_span
        a, b = _clause_span(text, pos, end)
        assert _neighbor_clause_has_birth_ctx(text, a, b) is False, (
            "隔着一整个婚礼小句的 `出生` 仍被当成邻句语境")
        # 邻句确实有出生语境 → True
        t2 = "我来自吉林，长春市"
        p2 = t2.index("长春市")
        a2, b2 = _clause_span(t2, p2, p2 + len("长春市"))
        assert _neighbor_clause_has_birth_ctx(t2, a2, b2) is True


# ══════════════════════════════════════════════════════════════════════
# ⑥ 工具路径：原文的显式城市主张必须被听见（k84 修）
# ══════════════════════════════════════════════════════════════════════

class TestToolPathHearsTheCityTheUserActuallySaid:
    """⑥：`city == "北京"` 的缺省豁免 + `_city_said` 只读参数 ⇒ 原文城市不可见。

    实测（改前）：档案北京 + 参数 `1999年3月28日10点55分 北京 男` +
    原文"我是1999年3月28日10点55分在长春出生的 男"
      ⇒ **不问、不写**，档案仍 `(1999,3,28,北京)`，盘面按**北京经度**算
        （真太阳时差 → **时柱不同**：己巳 vs 庚午）。
    修法：盘面与写档的 `city` 一律先取**原文里的显式城市主张**
    （`_city_explicit_in` 判"确是地名主张"），参数只在原文没给城市时兜底
    —— 与 k49-A 的明文口径「语境 = 当轮用户原文，工具参数仅在其缺失时兜底」一致。
    """

    def _run(self, seed_city, params, user_q):
        import tempfile
        k50 = _k50()
        with tempfile.TemporaryDirectory() as td:
            seed = dict(k50.ARCHIVE)
            seed["city"] = seed_city
            h, db = k50._h(Path(td), seed=seed)
            r = h._tool_bazi(params, "u1", user_q, "s1")
            return k50._snap(db), str(getattr(r, "text", r))

    def test_text_city_wins_over_the_engine_default(self):
        snap, txt = self._run(
            "北京", "1999年3月28日10点55分 北京 男",
            "我是1999年3月28日10点55分在长春出生的 男")
        assert snap is not None and snap[3] == "长春", (
            f"原文说了长春、系统却仍按 {snap[3] if snap else None!r} 落档")
        # 盘面与档案必须**同一个城市**（数据一致性红线）
        assert "长春" in txt, f"盘面没按长春算（城市与档案分裂）：{txt[:80]!r}"
        assert "北京" not in txt, f"盘面里仍出现北京：{txt[:80]!r}"

    def test_chart_and_archive_share_one_city(self):
        """同一城市两个来源（盘面 / 档案）不得分裂。"""
        snap, txt = self._run(
            "北京", "1999年3月28日10点55分 北京 男",
            "我是1999年3月28日10点55分在长春出生的 男")
        assert snap[3] == "长春" and "长春" in txt

    def test_no_text_city_keeps_the_old_behaviour(self):
        """反向：原文**没有**城市主张 → 一切照旧（不得凭空造一个城市）。"""
        snap, _txt = self._run("北京", "1999年3月28日10点55分 北京 男", None)
        assert snap == (1999, 3, 28, "北京"), snap
        snap2, _ = self._run("长春", "1999年3月28日10点55分 长春 男", None)
        assert snap2 == (1999, 3, 28, "长春"), snap2

    def test_k50_6_baseline_is_not_loosened(self):
        """k50-6 基线（档案长春 + 参数北京 + 原文无北京）→ 仍必须**问句 + 不改档**。"""
        snap, txt = self._run("长春", "1999年3月28日10点55分 北京 男", None)
        assert snap[3] == "长春", "缺省北京静默改档（k50-6 的洞又开了）"
        assert "确认" in txt, f"没走确认问句：{txt[:80]!r}"

    def test_non_placename_metaphor_is_not_a_city_claim(self):
        """`北京烤鸭` 式修饰语不是地名主张（k50-r2-3），不得被当成原文城市。"""
        snap, _ = self._run("北京", "1999年3月28日10点55分 北京 男", "北京烤鸭真好吃")
        assert snap[3] == "北京", snap
