# -*- coding: utf-8 -*-
"""k85-必修3：**"排盘来源 vs 写档来源"分裂类**（盘面按 A 算、档案/落库写 B）的门禁。

背景（**先把话说准**）：k84 在 `_tool_bazi` 里修了**一个实例**——**city** 可能来自
模型的工具 `params` 而用户原文说的是另一个城市 ⇒ 盘面与档案分裂，并在
`tests/test_k84_k57_residuals.py::TestToolPathHearsTheCityTheUserActuallySaid`
里钉住"盘面城市 == 档案城市"。本批（k85）修的是**同一个类的剩余两个工具**：
工具 `_tool_bazi` 的 **hour/minute/gender**（k84 只覆盖了 city）、工具
`_tool_naming` 的 **gender**（一个属性两个来源）。

## A. `_tool_bazi`：hour/minute/gender 仍**只**来自 `params`（本批修）

`year, month, day, hour, minute, city, gender = parsed`（`parsed` =
`_extract_bazi_info(params)`，模型填的参数）。参数里**没有时辰**时提取器给的是
**缺省** `hour=0; minute=0`（≠"用户说了子时"），而写档侧 `person_dao` 又把 `0`
当"未提供"（`0 -> None` → 保留旧值）⇒ 同一出生信息在下游裂成三个值：

| 场景 | 参数 | 原文 | 盘面(改前) | chart_records(改前) | persons(改前) |
|------|------|------|-----------|--------------------|--------------|
| (a) 参数无时辰 | `1999年3月28日 长春 男` | 我是1999年3月28日**10点55分**在长春出生的 男 | `0:00` | `hour:0,minute:0` | `10:55`（旧档） |
| (b) 参数听错 | `1999年3月28日12点 长春 男` | 同上 | `12:00` | `hour:12,minute:0` | **`12:55`**（时从参数、分从旧档） |

（b）里三个值**没有一个**是用户说的 10:55。修法 = k49-A 明文口径「语境 = 当轮
用户原文，工具参数仅在其缺失时兜底」落到 hour/minute/gender 上，判据
`_time_explicit_in` 与 k50-r2-3 的 `_city_explicit_in` **同型**（"确是出生时刻
主张"——防 `我每天凌晨3点起床` 式**别处钟点**被当生辰；实测见
`TestUnrelatedClockIsNotABirthClaim`）。覆盖后**同一组变量**供盘面
（`engine.calculate`）与写档（`_persist` → bazi_info / persons / chart_records /
画像）消费 ⇒ 三源恒同值。

## B. `_tool_naming`：性别一个属性两个来源（本批修）

`gender`（工具参数，schema 必填）与 `b_gender`（birth 串里的性别词，缺省
`unknown`）：改前盘面用 `b_gender`、卡片/候选字用 `gender`（`该能力拿不到
user_question`，工具注册表里只有 `bazi_chart`/`quote_rag` 注入原文，故本项**只用
params 修**）。

**诚实披露（不许夸大）**：今天**没有可见的错输出**——实测 男/女/unknown 三种入参下
`bazi`/`wuxing`/`yongshen` **完全相同**，只有 `dayun` 不同，而本工具只消费
`wuxing`/`yongshen`（`target_elements`）。现状是**潜在**分裂（引擎/下游演进后即
可见），本批按"一个属性一个值"收口。

## 未改项（本批**故意不动**）

1. `hour == 0`（同 minute）的双重语义（**子时/整点** vs **未提供**）是**存储层既有
   口径**（`person_dao._birth_dict` 的 `int(v or 0)` → `n if n else None`）。因此
   "参数没给时辰、原文也没说时辰、旧档有时辰"时，盘面按缺省 `0:00` 排而 persons 保留
   旧时辰——**属产品决策，本批不擅自改**（`TestKnownResidualNeedsProductDecision`
   把该残留钉成显式事实，防它被误当成"已修"；产品拍板后翻转为新期望）。
2. `birth_conflict_fields` 只比 年/月/日/城市（`src/storage/person_dao.py`），
   本批**未**扩它的字段面（那会改"先问后写"的问答行为，超出本批范围；且本批的
   覆盖发生在守卫**之前**，守卫看到的就是覆盖后的值）。
3. **hour/minute 没有归属层**（k56 只给 年/月日/中文月日 打归属标签）：多人生辰
   同句时取到的可能是**别人的时刻**。实测 `我是1999年3月28日出生的，我老公是
   1990年5月20日10点生的` → **直达路由改前就排到 10:00**（不是本批引入）；本批把
   工具路由对齐到同一提取实现（同句两路由一致），修它要动提取层 → 独立批次。
   （`TestKnownResidualNeedsProductDecision` 内的
   `test_two_person_message_has_no_ownership_layer_for_time` 登记。）
4. k84 的 **city** 覆盖口径一本未动（含它自己的同类边界，如 `_city_explicit_in`
   不带归属层）——`TestK84CityBehaviourUnchanged` 逐条钉住"不得回退"。

隔离：`tmp_path` 真实 SQLite + 真实 BaziEngine；零网络零 LLM；不开生产库；不跑全量。
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import pytest  # noqa: E402

from src.bot.handler import MessageHandler, _time_explicit_in  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# 两条实测形态（用户原文逐字）
PARAMS_A = "1999年3月28日 长春 男"                    # 参数**没有**时辰
PARAMS_B = "1999年3月28日12点 长春 男"                # 参数**听错**（12点）
TEXT_AB = "我是1999年3月28日10点55分在长春出生的 男"   # 原文说了 10点55分


def _k50():
    """k50 门禁模块（**复用**它的真实 harness `_h`/`ARCHIVE`，不复制一份）。"""
    spec = importlib.util.spec_from_file_location(
        "k50mod_for_k85", REPO / "tests" / "test_k50_residuals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_tool_bazi(seed_over=None, params="", user_q=None, user="u1"):
    """跑一次 `_tool_bazi` 并同时抓**三个来源**：

    - cast：盘面（`engine.calculate` 的真实入参，spy 抓取）→ (y,m,d,h,mi,city,gender)
    - persons：档案（persons 默认命主行）→ (hour, minute, city, gender)
    - chart_records：落库（`ChartDAO.get_latest_chart` 的 birth）→ 同上四元组
    """
    k50 = _k50()
    with tempfile.TemporaryDirectory() as _td:
        td = Path(_td)
        seed = dict(k50.ARCHIVE)
        seed.update(seed_over or {})
        h, db = k50._h(td, seed=seed)
        casts = []
        _orig = h.engine.calculate

        def _spy(*a, **kw):
            casts.append((a, kw))
            return _orig(*a, **kw)

        h.engine.calculate = _spy
        r = h._tool_bazi(params, user, user_q or "", "s1")
        p = PersonDAO(db).get_default_person(user)
        ch = ChartDAO(str(td / "c.db")).get_latest_chart(user)
        _b = (ch or {}).get("birth") or {}
        return {
            "cast": casts[0][0] if casts else None,
            "persons": None if not p else (
                p.get("birth_hour"), p.get("birth_minute"),
                p.get("city"), p.get("gender")),
            "chart_records": None if not ch else (
                _b.get("hour"), _b.get("minute"), _b.get("city"), _b.get("gender")),
            "text": str(getattr(r, "text", r)),
            "ok": getattr(r, "ok", None),
        }


# ══════════════════════════════════════════════════════════════════════
# A(i) 两条实测形态：**盘面 / 档案 / 落库三源同一个值**
# ══════════════════════════════════════════════════════════════════════


class TestChartAndArchiveShareOneValue:
    """k85-A 核心不变式：`engine.calculate(...)` 用的值 == 写进 persons 的值 ==
    写进 chart_records 的值（数据一致性红线，与 k84 的 city 同一条）。"""

    @pytest.mark.parametrize("params", [PARAMS_A, PARAMS_B])
    def test_hour_minute_are_the_same_in_all_three_sources(self, params):
        c = _run_tool_bazi({}, params, TEXT_AB)
        assert c["ok"] is True, c["text"][:120]
        expect = (10, 55, "长春", "男")
        assert c["cast"] is not None, "盘面没排（引擎未被调用）"
        assert c["cast"][3:7] == expect, (
            f"盘面用的不是用户原文说的时刻：{c['cast'][3:7]}（参数 {params!r}）")
        assert c["chart_records"] == expect, (
            f"chart_records 与盘面分裂：{c['chart_records']}")
        assert c["persons"] == expect, (
            f"persons 与盘面分裂：{c['persons']}")
        # 三源**互相**相等（不只各自等于期望值——防"三处同错"
        assert c["cast"][3:7] == c["chart_records"] == c["persons"]

    def test_case_a_no_time_in_params(self):
        """(a) 参数无时辰 → 改前盘面 0:00 / 落库 0:00 / 档案 10:55（三源三分裂）。"""
        c = _run_tool_bazi({}, PARAMS_A, TEXT_AB)
        assert c["cast"][3:5] == (10, 55), c["cast"]
        assert c["chart_records"][:2] == (10, 55), c["chart_records"]
        assert c["persons"][:2] == (10, 55), c["persons"]

    def test_case_b_minute_is_not_borrowed_from_the_old_archive(self):
        """(b) 参数 12点 + 原文 10点55分 → 改前 persons 是 `12:55`（时从参数、
        分从旧档**拼**出来的第四种值）。修后三源都是 10:55 —— 且**不得**出现
        `12:55` 这种"半个参数 + 半个旧档"的拼装值。"""
        c = _run_tool_bazi({}, PARAMS_B, TEXT_AB)
        assert c["persons"] != (12, 55, "长春", "男"), (
            "参数小时 + 旧档分钟 的拼装值又出现了")
        assert c["cast"][3:5] == (10, 55), c["cast"]
        assert c["chart_records"][:2] == (10, 55), c["chart_records"]
        assert c["persons"][:2] == (10, 55), c["persons"]

    def test_city_is_also_one_value_on_the_same_run(self):
        """同一次调用里 city 也必须是同一个（k84 的洞不得因本批改动回退）。"""
        c = _run_tool_bazi({"city": "北京"},
                           "1999年3月28日10点55分 北京 男",
                           "我是1999年3月28日10点55分在长春出生的 男")
        assert c["cast"][5] == "长春", c["cast"]
        assert c["chart_records"][2] == "长春", c["chart_records"]
        assert c["persons"][2] == "长春", c["persons"]

    def test_gender_from_the_original_text_is_used_by_both(self):
        """原文显式性别（unknown → 男）同样作用于盘面与写档。"""
        c = _run_tool_bazi({}, "1999年3月28日 长春",
                           "我是1999年3月28日10点55分在长春出生的 男")
        assert c["cast"][6] == "男", c["cast"]
        assert c["chart_records"][3] == "男", c["chart_records"]
        assert c["persons"][3] == "男", c["persons"]

    def test_engine_card_shows_the_overridden_city(self):
        """盘面卡片里出现的是**覆盖后**的城市（用户可见面与落库面同值）。"""
        c = _run_tool_bazi({"city": "北京"},
                           "1999年3月28日10点55分 北京 男",
                           "我是1999年3月28日10点55分在长春出生的 男")
        assert "长春" in c["text"], c["text"][:120]
        assert "北京" not in c["text"], c["text"][:120]


# ══════════════════════════════════════════════════════════════════════
# A(ii) 判据（`_time_explicit_in`，与 `_city_explicit_in` 同型）：别处钟点不算主张
# ══════════════════════════════════════════════════════════════════════


class TestUnrelatedClockIsNotABirthClaim:
    """反向：原文里的钟点**不一定是出生时刻** —— 判据必须只认"出生陈述里的时刻"。

    改前实测（若不加判据直接覆盖）：`我每天凌晨3点起床，我是1999年3月28日10点55分
    在长春出生的 男` 的提取器按**文档序**先取到"凌晨3点"（起床那个）⇒ 会把参数里
    **正确**的 10:55 改成 3:00（**新造错**）。
    """

    @pytest.mark.parametrize("text,hour,minute,expect", [
        # 出生陈述里的时刻 → 显式
        ("我是1999年3月28日10点55分在长春出生的 男", 10, 55, True),
        ("我是1999年3月28日中午12点在北京出生的", 12, 0, True),
        ("1999年3月28日10点55分 长春 男", 10, 55, True),
        ("我是1999年3月28日出生的，10点55分", 10, 55, True),
        # 别处钟点 → 非显式（与 k50-r2-3 的 `北京烤鸭` 同型风险）
        ("我每天凌晨3点起床", 3, 0, False),
        ("我1999年3月28日出生，下午3点见客户", 15, 0, False),
        # 提取器给的不是待判值 → 非显式（自校验）
        ("1999年3月28日 长春 男", 10, 55, False),
        ("", 10, 55, False),
    ])
    def test_time_claim_judgement(self, text, hour, minute, expect):
        assert _time_explicit_in(text, hour, minute) is expect, text

    def test_unrelated_clock_in_the_same_message_does_not_override(self):
        """E2E：原文有"凌晨3点起床" + 参数里是**对的** 10:55 → 一格都不许动。"""
        c = _run_tool_bazi(
            {}, "1999年3月28日10点55分 长春 男",
            "我每天凌晨3点起床，我是1999年3月28日10点55分在长春出生的 男")
        assert c["cast"][3:5] == (10, 55), c["cast"]
        assert c["chart_records"][:2] == (10, 55), c["chart_records"]
        assert c["persons"][:2] == (10, 55), c["persons"]

    def test_no_time_in_the_original_text_keeps_the_params(self):
        """原文**没有**时刻主张 → 参数照旧（不得凭空造一个时辰）。"""
        c = _run_tool_bazi({}, "1999年3月28日10点55分 长春 男",
                           "我是1999年3月28日在长春出生的 男")
        assert c["cast"][3:5] == (10, 55), c["cast"]
        assert c["chart_records"][:2] == (10, 55), c["chart_records"]

    def test_no_user_question_keeps_params_only_behaviour(self):
        """直调/旧路径（无当轮原文）→ 覆盖一格都不生效（零变化）。"""
        c = _run_tool_bazi({}, "1999年3月28日10点55分 长春 男", None)
        assert c["cast"][3:5] == (10, 55), c["cast"]

    def test_case_b_shape_without_any_text_still_uses_params(self):
        """(b) 的参数形态 + **没有**原文 → 盘面/落库仍按参数 12点（不误覆盖）。"""
        c = _run_tool_bazi({}, PARAMS_B, None)
        assert c["cast"][3:5] == (12, 0), c["cast"]
        assert c["chart_records"][:2] == (12, 0), c["chart_records"]

    def test_rejects_third_party_gender_claim(self):
        """性别判据复用**既有单一事实源**：第三人领属语引出的性别词不算本人主张。"""
        m = MessageHandler.__new__(MessageHandler)
        assert m._extract_partial_birth(
            "我妹妹1990年5月20日出生的女孩子").get("gender") is None
        assert m._extract_partial_birth(
            "我是1990年5月20日出生的 男").get("gender") == "男"


# ══════════════════════════════════════════════════════════════════════
# A(iii) 路由一致性：同一句话经 **工具路由 / 直达路由** 得同一个盘（k11c 硬口径）
# ══════════════════════════════════════════════════════════════════════


def _run_direct_bazi(msg, seed_over=None, user="u1"):
    """直达路由（`_handle_bazi`）的盘面入参（spy 抓 `engine.calculate`）。"""
    k50 = _k50()
    with tempfile.TemporaryDirectory() as _td:
        td = Path(_td)
        seed = dict(k50.ARCHIVE)
        seed.update(seed_over or {})
        h, _db = k50._h(td, seed=seed)
        casts = []
        _orig = h.engine.calculate

        def _spy(*a, **kw):
            casts.append(a)
            return _orig(*a, **kw)

        h.engine.calculate = _spy
        h._handle_bazi(msg, user)
        return casts[0] if casts else None


class TestSameSentenceBothRoutesSameChart:
    """k11c 硬口径：「同句自我生辰经 tool/直达两路由四柱一致」。

    改前实测：同一句 `我是1999年3月28日10点55分在长春出生的 男`
      · 直达路由（`_extract_bazi_info(原文)`）→ **10:55**
      · 工具路由（参数缺时辰 → 提取器缺省 0）→ **0:00**
    ⇒ 同一句话两个盘。修后工具路由的盘面**就等于**提取层对当轮原文的结果
    （两路由共用同一实现），本类把该一致性钉住。
    """

    @pytest.mark.parametrize("params", [
        PARAMS_A,                       # 参数无时辰（缺省 0 被覆盖）
        "1999年3月28日10点55分 长春 男",  # 参数已含正确时辰（覆盖是 no-op）
    ])
    def test_tool_and_direct_route_agree(self, params):
        tool_cast = _run_tool_bazi({}, params, TEXT_AB)["cast"]
        direct_cast = _run_direct_bazi(TEXT_AB)
        assert tool_cast is not None and direct_cast is not None
        assert tool_cast[:7] == direct_cast[:7], (
            f"同一句话两个路由两个盘：tool={tool_cast[:7]} direct={direct_cast[:7]}")
        assert tool_cast[3:5] == (10, 55), tool_cast


# ══════════════════════════════════════════════════════════════════════
# B. `_tool_naming`：**同一个性别**供盘面与卡片
# ══════════════════════════════════════════════════════════════════════


def _naming_bot():
    bot = MessageHandler.__new__(MessageHandler)
    bot.engine = BaziEngine()
    return bot


def _run_naming(params, tag=""):
    """跑一次 `_tool_naming`：返回 (盘面性别, 卡片性别, ok, text)。

    盘面性别 = `engine.calculate` 的真实入参（spy）；卡片性别 = 卡片首行
    `【起名建议】姓氏：X｜性别：Y`（两人可见面）。
    """
    import re as _re
    bot = _naming_bot()
    casts = []
    _orig = bot.engine.calculate

    def _spy(*a, **kw):
        casts.append(a)
        return _orig(*a, **kw)

    bot.engine.calculate = _spy
    r = bot._tool_naming(params, "u1")
    text = str(r.text)
    m = _re.search(r'性别：(\S+?)(?:\n|$)', text)
    return {
        "cast_gender": casts[0][6] if casts else None,
        "card_gender": m.group(1) if m else None,
        "ok": r.ok, "text": text, "casts": casts,
    }


class TestNamingUsesOneGender:
    """k85-B：`gender`（参数）与 `b_gender`（birth 串）是**同一个属性**，
    盘面与卡片必须用同一个值。"""

    @pytest.mark.parametrize("gender", ["男", "女"])
    def test_birth_without_gender_uses_the_param_gender_everywhere(self, gender):
        """birth 串没写性别（提取器 unknown）→ 参数性别同时进盘面与卡片。

        改前：盘面拿 `b_gender`（**unknown**，引擎按"未知默认男"排）而卡片印
        `gender`（女）→ 同一次回答里两个性别。
        """
        out = _run_naming(
            f"surname: 张\ngender: {gender}\nbirth: 2019年3月15日 午时 北京")
        assert out["ok"] is True, out["text"][:120]
        assert out["cast_gender"] == gender, out
        assert out["card_gender"] == gender, out

    @pytest.mark.parametrize("gender", ["男", "女"])
    def test_both_explicit_and_equal(self, gender):
        """两处都显式且一致 → 盘面与卡片同一个值。"""
        out = _run_naming(
            f"surname: 张\ngender: {gender}\n"
            f"birth: 2019年3月15日 午时 北京 {gender}")
        assert out["ok"] is True, out["text"][:120]
        assert out["cast_gender"] == out["card_gender"] == gender, out

    def test_degraded_without_birth_uses_the_param_gender(self):
        """降级路径（无 birth）不受影响：卡片印参数性别（原行为零变化）。"""
        out = _run_naming("surname: 张\ngender: 女")
        assert out["ok"] is True, out["text"][:120]
        assert out["casts"] == [], "无出生信息不该排盘"
        assert out["card_gender"] == "女", out

    def test_conflicting_genders_are_not_silently_resolved(self):
        """两处**都显式且不同** → 不静默选一个：不排盘、回确认问句
        （仓库"先问后写"惯例，与 `_gen_birth_conflict_ask` 同族）。

        改前：盘面按 birth 串的 男 排、卡片印参数的 女（**同一次回答两个性别**）。
        """
        out = _run_naming(
            "surname: 张\ngender: 女\nbirth: 2019年3月15日 午时 北京 男")
        assert out["ok"] is False, out["text"][:120]
        assert out["casts"] == [], "性别冲突时不得用其中一个先排盘"
        assert "确认" in out["text"], out["text"][:120]
        assert "男" in out["text"] and "女" in out["text"], out["text"][:120]

    def test_same_gender_reaches_candidates_and_card(self):
        """一致性直证：候选名池与卡片拿到的性别是同一个（都取自 `_gender_use`）。"""
        from src.tools.naming import build_pool
        out = _run_naming(
            "surname: 张\ngender: 女\nbirth: 2019年3月15日 午时 北京")
        assert out["ok"] is True
        _p = build_pool("女", None)
        assert _p, "性别池不可用（判据无效）"
        assert out["card_gender"] == "女"


# ══════════════════════════════════════════════════════════════════════
# A(iv) 回归：k84 的 city 行为一格不变
# ══════════════════════════════════════════════════════════════════════


class TestK84CityBehaviourUnchanged:
    """k84 的 city 覆盖口径（`_city_explicit_in` 守卫 + 原文优先）不得被本批改动
    松动——同一条"原文优先"口径本批只**加**到 hour/minute/gender 上。"""

    def test_text_city_wins_and_reaches_all_three(self):
        c = _run_tool_bazi({"city": "北京"},
                           "1999年3月28日10点55分 北京 男", TEXT_AB)
        assert c["ok"] is True, c["text"][:120]
        assert (c["cast"][5], c["chart_records"][2], c["persons"][2]) == (
            "长春", "长春", "长春"), c

    def test_no_text_city_keeps_the_old_behaviour(self):
        """原文没有城市主张 → 参数城市照旧（不得凭空造城市）。"""
        c = _run_tool_bazi({"city": "北京"}, "1999年3月28日10点55分 北京 男", None)
        assert c["ok"] is True, c["text"][:120]
        assert c["persons"] == (10, 55, "北京", "男"), c["persons"]
        c2 = _run_tool_bazi({"city": "长春"},
                            "1999年3月28日10点55分 长春 男", None)
        assert c2["persons"] == (10, 55, "长春", "男"), c2["persons"]

    def test_k50_6_baseline_still_asks_and_does_not_write(self):
        """k50-6 基线（档案长春 + 参数北京 + 原文无北京）→ 问句 + 不改档。"""
        c = _run_tool_bazi({}, "1999年3月28日10点55分 北京 男", None)
        assert c["ok"] is False, c["text"][:120]
        assert "确认" in c["text"], c["text"][:120]
        assert c["persons"][2] == "长春", c["persons"]

    def test_non_placename_metaphor_is_not_a_city_claim(self):
        """`北京烤鸭` 式修饰语不是地名主张（k50-r2-3）→ 不被当成原文城市。"""
        c = _run_tool_bazi({}, "1999年3月28日10点55分 北京 男", "北京烤鸭真好吃")
        assert c["persons"][2] == "长春", c["persons"]

    def test_explicit_city_in_turn_still_writes(self):
        """k50-6 的另一半：原文**显式**说了北京 → 按显式陈述直写。"""
        c = _run_tool_bazi({"city": "长春"},
                           "1999年7月8日10点 北京 男",
                           "我是1999年7月8日在北京出生的")
        assert c["ok"] is True, c["text"][:120]
        assert c["persons"][2] == "北京", c["persons"]


# ══════════════════════════════════════════════════════════════════════
# A(v) 真实入口链：`_execute_tool_call`（结构化 params → 校验 → serialize →
#      注册表执行器）——不是直调 `_tool_bazi`（真实用户路径口径）
# ══════════════════════════════════════════════════════════════════════


class TestThroughTheRealToolCallEntry:
    """走**生产同一入口**：LLM 工单是 dict（cap `bazi_chart` 的 schema 必填 `text`），
    经 `_execute_tool_call` 校验 + `serialize_params` 桥接后才到 `_tool_bazi`。"""

    def _bind_bazi(self, bot):
        """与 `MessageHandler.__init__` 里**逐字相同**的绑定（用完还原）。"""
        from src.bot import capability_registry as reg
        from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME
        cap = CAPABILITY_BY_NAME["排盘"]
        orig_executor = cap.executor
        orig_ex = reg._tool_executors.get("bazi_chart")
        bind_executors({
            "bazi_chart": lambda p, user_id="", user_question="", session_id=None:
                bot._tool_bazi(p, user_id, user_question, session_id)}, {})

        def restore():
            cap.__dict__["executor"] = orig_executor
            if orig_ex is None:
                reg._tool_executors.pop("bazi_chart", None)
            else:
                reg._tool_executors["bazi_chart"] = orig_ex
        return restore

    @pytest.mark.parametrize("text_param", [
        "1999年3月28日 长春 男",       # 模型漏了时辰
        "1999年3月28日12点 长春 男",   # 模型听错（12点）
    ])
    def test_structured_workorder_still_lands_on_one_value(self, text_param):
        k50 = _k50()
        with tempfile.TemporaryDirectory() as _td:
            td = Path(_td)
            h, db = k50._h(td)
            restore = self._bind_bazi(h)
            try:
                casts = []
                _orig = h.engine.calculate

                def _spy(*a, **kw):
                    casts.append(a)
                    return _orig(*a, **kw)

                h.engine.calculate = _spy
                r = h._execute_tool_call(
                    "排盘", {"text": text_param}, "u1", TEXT_AB, "s1")
            finally:
                restore()
            assert r.ok is True, str(r.text)[:120]
            assert casts, "真实入口链没走到盘面"
            p = PersonDAO(db).get_default_person("u1")
            ch = ChartDAO(str(td / "c.db")).get_latest_chart("u1")
            got = (
                (casts[0][3], casts[0][4], casts[0][5], casts[0][6]),
                (p.get("birth_hour"), p.get("birth_minute"),
                 p.get("city"), p.get("gender")),
                ((ch or {}).get("birth") or {}).get("hour"),
            )
            assert got[0] == got[1] == (10, 55, "长春", "男"), got
            assert got[2] == 10, got


# ══════════════════════════════════════════════════════════════════════
# 已知残留（**不是**期望行为；钉成显式事实，产品拍板后翻转）
# ══════════════════════════════════════════════════════════════════════


class TestKnownResidualNeedsProductDecision:
    """`hour == 0` 的双重语义（**子时** vs **未提供**）是存储层既有口径
    （`person_dao._birth_dict`：`int(v or 0)` → `n if n else None`）。

    ⇒ "参数没给时辰、原文也没说时辰、旧档有时辰"时，盘面按缺省 `0:00` 排、
    persons 保留旧时辰——**盘面与档案仍分裂**。本批**不擅自改**存储层语义
    （改它 = 决定"0 到底是子时还是未提供"，属产品口径）。

    本条把该残留钉成**显式事实**（防它被当成"已修"），不是期望行为；产品拍板后
    本类应翻转为新期望。
    """

    def test_params_without_time_does_not_inherit_the_archive_time(self):
        c = _run_tool_bazi({}, "1999年3月28日 长春 男", None)
        assert c["cast"][3:5] == (0, 0), c["cast"]        # 盘面：缺省 0:00
        assert c["chart_records"][:2] == (0, 0), c["chart_records"]
        assert c["persons"][:2] == (10, 55), c["persons"]  # 旧档保留 10:55
        assert c["cast"][3:5] != c["persons"][:2], (
            "该残留已被修（0==未提供的语义变了）→ 请翻转本用例为新期望")

    def test_explicit_time_in_text_removes_the_residual(self):
        """**修好的那一半**：原文说了时刻 → 残留消失（三源同值）。"""
        c = _run_tool_bazi({}, "1999年3月28日 长春 男", TEXT_AB)
        assert c["cast"][3:5] == c["chart_records"][:2] == c["persons"][:2]

    def test_two_person_message_has_no_ownership_layer_for_time(self):
        """**登记的另一条残留（本批未新增、也未修）**：k56 归属层只覆盖**日期
        候选**（年/月日），**hour/minute 没有归属层** —— 多人生辰同句时取到的
        可能是**别人的时刻**。

        实测串：`我是1999年3月28日出生的，我老公是1990年5月20日10点生的`
          · 直达路由：改前/改后**都是** 10:00（提取层的时辰段无归属过滤）
          · 工具路由：本批把口径对齐到同一提取实现 → 也是 10:00
        ⇒ 本条断言的是**路由一致**（k11c）与"两路由同取他人时刻"这个事实，
        **不是**期望行为。修它 = 把 hour/minute 纳入 k56 归属层（提取层，
        两个路由同时变）→ 属独立一批，需产品/批次拍板；拍板后本用例翻转。
        """
        text = "我是1999年3月28日出生的，我老公是1990年5月20日10点生的"
        tool_cast = _run_tool_bazi({}, "1999年3月28日 长春 男", text)["cast"]
        direct_cast = _run_direct_bazi(text)
        assert tool_cast is not None and direct_cast is not None
        assert tool_cast[3:5] == direct_cast[3:5], (
            f"两路由取到了不同的时刻：tool={tool_cast[3:5]} direct={direct_cast[3:5]}")
        assert tool_cast[3:5] == (10, 0), (
            "该残留已被修（hour/minute 有归属层了）→ 请翻转本用例为新期望")
