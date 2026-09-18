# -*- coding: utf-8 -*-
"""k56：归属层重构——"这段信息是谁的"提到**候选选择层**。

背景：k50 四轮都在同一处"消息级闸门 + 各种豁免"上加条件，每加一条就冒一个新族。
本批把归属判定做成**独立一层**：每个日期候选（年 / 数字月日 / 中文月日）先打标签
（self 本人 / other 他人 / unknown 未知），采纳面只在「本人 + 未知」候选里做单/多
日期裁决；中文候选与数字候选走**同一套**归属与裁决（这是残留 1/2 的根因）。

单一实现（全部复用既有谓词，不新起第二套）：
- `_subject_owner` = `person_dao._clause_span` + 既有 `_TP_SUBJECT_FILLER_RE` +
  既有第三方尾锚 `_tp_subject_tail_re()`（k50-r4 同一套机器）+ 自述代词尾锚
- `_drop_other_candidates` 是两个提取器唯一的归属入口
- `_first_by_position` 统一候选选择（数字/中文不再分两条路径）
- 第三方**排盘请求**（B3-1 `_is_third_party_birth_request`）整体豁免（那是给对方提取）

隔离：零网络零 LLM（纯提取层）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import pytest  # noqa: E402

from src.bot.handler import (  # noqa: E402
    ATT_OTHER, ATT_SELF, ATT_UNKNOWN, MessageHandler, _cand_owner,
    _first_by_position, _subject_owner, _year_of_match,
)


def _p(msg):
    return object.__new__(MessageHandler)._extract_partial_birth(msg)


def _b(msg):
    return object.__new__(MessageHandler)._extract_bazi_info(msg)


def _md(msg):
    g = _p(msg)
    return (g.get("month"), g.get("day")) if g.get("month") else None


# ════════════════════════════════════════════════════════════════
# ① 归属层直证（标签本身）
# ════════════════════════════════════════════════════════════════
class TestOwnerLabels:
    @pytest.mark.parametrize("msg,owner", [
        ("我老公是1999年5月20日生的", ATT_OTHER),
        ("我妻子是1991年7月8日的", ATT_OTHER),
        ("他1999年5月13日出生", ATT_OTHER),
        ("我朋友1990年5月20日出生的女生，帮我看看", ATT_OTHER),
        ("我1995年3月8日生的", ATT_SELF),
        ("我出生于1990年5月20日", ATT_SELF),
        ("我是1999年3月28日出生的", ATT_SELF),
        ("1999年5月13日 10:55 长春 男", ATT_UNKNOWN),   # 无主语线索 → 未知（按本人）
        ("5月13日，早上6点", ATT_UNKNOWN),
    ])
    def test_label(self, msg, owner):
        import re
        m = re.search(r'\d{4}\s*年|\d{1,2}\s*[月\-/.]\s*\d{1,2}', msg)
        assert _subject_owner(msg, m.start(), m.end()) == owner, msg

    def test_self_predicate_not_third_party(self):
        """k50-r4 反向夹具不回归：出处句（我妈说我…）主语段末尾是"我" → 非他人。"""
        import re
        for msg in ["我妈说我1995年3月8日生的", "我老公说我是1995年3月8日生的"]:
            m = re.search(r'\d{1,2}\s*月\s*\d{1,2}\s*日', msg)
            assert _subject_owner(msg, m.start(), m.end()) != ATT_OTHER, msg

    def test_first_by_position_is_document_order(self):
        """数字/中文**同一套**候选选择：按位置取首个，不再"中文优先"。"""
        import re
        msg = "5月13日生的，不是说腊月廿六"
        a = re.search(r'5\s*月\s*13\s*日', msg)
        b = re.search(r'腊月廿六', msg)
        assert _first_by_position([b, a]).span() == a.span()


# ════════════════════════════════════════════════════════════════
# ② 三条残留
# ════════════════════════════════════════════════════════════════
class TestResidual1CnNegation:
    """残留 1：中文侧不参与否定裁决。

    审查给的例句（`我不是腊月廿六生的，是正月初一生的`）在**改前已通过**
    （k50-1 已接中文月日）——本批实测**真正仍破损**的是同族的**中文数字年**：
    `我不是二〇〇一年生的，是二〇〇三年生的` 改前取被否定的 2001 ✗。
    """

    @pytest.mark.parametrize("msg,expect", [
        ("我不是腊月廿六生的，是正月初一生的", (1, 1)),   # k50-1 已修（本批锁定）
        ("我是正月初一生的，不是腊月廿六生的", (1, 1)),
        ("不是正月初一，是腊月廿六生的", (12, 26)),
    ])
    def test_cn_month_day_locked(self, msg, expect):
        assert _md(msg) == expect, msg

    @pytest.mark.parametrize("msg,year", [
        ("我不是二〇〇一年生的，是二〇〇三年生的", 2003),   # 改前 2001 ✗
        ("我是二〇〇三年生的，不是二〇〇一年生的", 2003),
        ("我不是一九九五年生的，是一九九九年生的", 1999),
    ])
    def test_cn_year_joins_negation(self, msg, year):
        assert _p(msg).get("year") == year, msg

    def test_cn_year_fallback_kept(self):
        """既有 2 位中文年世纪推断不回退（`七六` → 1976）。"""
        assert _p("我七六年生的").get("year") == 1976
        assert _p("我一九九九年5月13日生的").get("year") == 1999

    def test_year_of_match_single_helper(self):
        import re
        assert _year_of_match(re.search(r'\d{4}\s*年', "1999年")) == 1999
        assert _year_of_match(re.search(r'[〇零一二三四五六七八九]{4}\s*年',
                                        "二〇〇三年")) == 2003


class TestResidual2CnThirdParty:
    """残留 2：中文路径的第三方豁免不生效（数字侧 k50-r4 已有）。"""

    @pytest.mark.parametrize("msg", [
        "我老公是腊月廿六生的",       # 改前 (12, 26) ✗
        "我妻子是腊月廿六的",
        "他正月初一生的",
        "我朋友腊月廿六生日",
    ])
    def test_third_party_cn_not_adopted(self, msg):
        assert _md(msg) is None, (msg, _p(msg))

    @pytest.mark.parametrize("msg,expect", [
        ("我是农历腊月廿六出生的", (12, 26)),
        ("我腊月廿六生日", (12, 26)),
        ("我的生日是农历腊月廿六", (12, 26)),
        ("我正月初一生的", (1, 1)),
    ])
    def test_self_cn_still_adopted(self, msg, expect):
        assert _md(msg) == expect, msg

    def test_mixed_self_and_third_party(self):
        """本人 + 他人同句：取本人的中文月日。"""
        assert _md("我正月初一生的，我老公是腊月廿六生的") == (1, 1)
        assert _md("我老公是腊月廿六生的，我正月初一生的") == (1, 1)


class TestResidual3CrossPersonMixing:
    """残留 3：跨人混搭（year 取本人、md 取对方）。"""

    def test_year_only_from_self(self):
        got = _p("我1995年生的，我老公是1999年5月20日生的")   # 改前 y=1995 + md=5/20 ✗
        assert got.get("year") == 1995 and "month" not in got, got

    def test_bazi_extractor_not_mixed(self):
        assert _b("我1995年生的，我老公是1999年5月20日生的") is None

    def test_self_full_date_unaffected(self):
        got = _p("我1995年3月8日生的，我老公是1999年5月20日生的")
        assert (got.get("year"), got.get("month"), got.get("day")) == (1995, 3, 8), got

    def test_third_party_request_still_extracted(self):
        """B3-1 第三方**排盘请求**整体豁免：给对方提取不受归属层影响。"""
        got = _p("帮我朋友排个盘，他1976年5月13日生的")
        assert (got.get("year"), got.get("month"), got.get("day")) == (1976, 5, 13), got
        assert _b("帮我朋友排个盘，他1976年5月13日生的")[:3] == (1976, 5, 13)


# ════════════════════════════════════════════════════════════════
# ③ k50 拍板口径重锁（归属层不许回退既有战果）
# ════════════════════════════════════════════════════════════════
class TestK50RulingsRelocked:
    @pytest.mark.parametrize("msg", [
        "5月13日 1999年3月28日", "1999年3月28日 5月13日",
        "8.15 5月13日", "11.20 5月13日",
    ])
    def test_ruled_not_adopted(self, msg):
        """拍板：两个完整日期同现 → 不取（逐条保持）。"""
        assert _md(msg) is None, (msg, _p(msg))

    @pytest.mark.parametrize("msg,ymd", [
        ("我1995年3月8日生的，我老公是1999年5月20日生的", (1995, 3, 8)),
        ("我是1999年3月28日出生的，我老公1995年3月8日生的", (1999, 3, 28)),
        ("我出生于1990年5月20日，我妻子是1991年7月8日的", (1990, 5, 20)),
        ("我老公是1999年5月20日生的，我1995年3月8日生的", (1995, 3, 8)),
    ])
    def test_family_kept(self, msg, ymd):
        got = _p(msg)
        assert (got.get("year"), got.get("month"), got.get("day")) == ymd, (msg, got)

    @pytest.mark.parametrize("msg", [
        "5月13日，10点", "5月13日，10点55分", "5月13日 10:55", "5月13日，8点",
        "5月13日，12点30分", "5月13日 8时", "5月13日，下午3点半",
        "5月13日，早上6点", "5月13日 10点 榆树市 男", "5月13日10点出生",
        "5月13日 23:00", "5月13日，凌晨3点", "5月13日 0点", "5月13日，19点05分",
        "5月13日，晚上11点", "5月13日 5:30 男",
    ])
    def test_single_date_matrix_kept(self, msg):
        assert _md(msg) == (5, 13), (msg, _p(msg))

    def test_time_like_candidate_kept(self):
        assert _md("我5月13日8.15分生的") == (5, 13)
        assert _md("5月13日，8.15出生") == (5, 13)

    def test_correction_exception_kept(self):
        assert _md("不是腊月廿六生的，是5月20日生的") == (5, 20)
        assert _md("我1999年3月28日生的，1995年3月8日是错的") == (3, 28)


# ════════════════════════════════════════════════════════════════
# ④ R3-1（Critical）：并列主语"我和我X都是…"→ 本人也在其中（不得整条丢）
# ════════════════════════════════════════════════════════════════
COORD_FAMILY = [
    ("我和我老婆都是1990年生的", 1990),
    ("我和我老公都是1990年生的", 1990),
    ("我、我老婆都是1990年生的", 1990),
    ("我跟我老婆都是1990年生的", 1990),
    ("我和我老婆都是1990年的", 1990),
    ("我和我老婆都1990年生的", 1990),
    ("我和我老婆都是1990年5月20日生的", 1990),
    ("我和我老婆都是1995年3月8日生的", 1995),
]
# 真他人对照集（**必须仍不取**）：称谓 × 句式
TRUE_OTHER = [
    "我老公是1999年5月20日生的", "我妻子是1991年7月8日的", "我同事1988年5月20日生的",
    "我朋友1990年5月20日出生的女生，帮我看看", "我妹妹1990年出生的女孩子，我们合不合",
    "他的女儿1990年5月20日出生，我们合不合", "我是女的，我男朋友1990年生的",
    "我丈夫是1990年生的", "我男朋友1999年5月20日出生", "我对象1991年7月8日的",
    "我亲戚1990年生的", "我邻居1999年5月20日生的", "我老板1991年7月8日的",
    "我客户1990年5月20日出生的女生", "我女儿是1999年5月20日生的",
]
# 与基线**一致**的边界（本批不动，防"顺手扩大胜利面"）
BASELINE_SAME = [
    "我媳妇1990年生的", "我爱人1990年生的", "我先生1990年生的", "我俩都是1990年生的",
    "我老婆和我都是1990年生的", "我和我老婆同年，都是1990年生的",
]


class TestCoordinatedSubject:
    """R3-1：`我和我老婆都是1990年生的` 的主语是**并列的"我和我老婆"**（含本人）
    —— 只看主语段末尾会把"我老婆"当主语 → 用户自己的出生信息整条丢（改前 E2E
    什么都不认、回固定引导）。判据：并列连词左侧含自述代词 → self。
    修完必须：8 条并列族恢复取值 + 15 条真他人仍不取（双向重锁）。"""

    @pytest.mark.parametrize("msg,year", COORD_FAMILY)
    def test_family_takes_self(self, msg, year):
        got = _p(msg)
        assert got.get("year") == year, (msg, got)

    def test_family_month_day(self):
        assert _md("我和我老婆都是1990年5月20日生的") == (5, 20)
        assert _md("我和我老婆都是1995年3月8日生的") == (3, 8)

    def test_family_bazi_extractor(self):
        assert _b("我和我老婆都是1995年3月8日生的")[:3] == (1995, 3, 8)

    @pytest.mark.parametrize("msg", TRUE_OTHER)
    def test_true_other_still_not_taken(self, msg):
        """双向重锁：真他人（末尾即指代、无并列）一律仍不取。"""
        assert _p(msg).get("year") is None, (msg, _p(msg))
        assert _b(msg) is None, (msg, _b(msg))

    @pytest.mark.parametrize("msg", BASELINE_SAME)
    def test_baseline_consistent_kept(self, msg):
        """与基线一致的边界**不动**（不因本批修 R3-1 而扩大采纳面）。"""
        assert _p(msg).get("year") == 1990, (msg, _p(msg))

    def test_owner_label_direct(self):
        """结构直证：并列含本人 → self；末尾是他人且无并列 → other。"""
        import re
        for msg in ["我和我老婆都是1990年生的", "我、我老婆都是1990年生的"]:
            m = re.search(r'\d{4}\s*年', msg)
            assert _subject_owner(msg, m.start(), m.end()) == ATT_SELF, msg
        for msg in ["我老公是1999年5月20日生的", "我同事1988年5月20日生的"]:
            m = re.search(r'\d{4}\s*年', msg)
            assert _subject_owner(msg, m.start(), m.end()) == ATT_OTHER, msg

    def test_coordinator_not_self_without_self_pronoun(self):
        """连词左侧无自述代词时**不**放行（防"和/与"泛化）：`他和我老婆都是…`。"""
        import re
        msg = "他和我老婆都是1990年生的"
        m = re.search(r'\d{4}\s*年', msg)
        assert _subject_owner(msg, m.start(), m.end()) != ATT_SELF, msg
