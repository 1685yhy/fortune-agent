"""k11 内容事实纪律修复批测试（分支 k11-fact-discipline）。

覆盖（对应 plan docs/superpowers/plans/2026-09-07-k11-fact-discipline.md）：
A 事实包：引擎 current_stage 派生（固定 now 注入）/fact pack 文本/主链与 advisor 双注入
B 性别称谓：advisor 口吻统一豆包式（k63，不再随性别分支）+ 称谓仍按性别分流
  （男/unknown=中性称谓硬规则、女=不注入）+ fact_guard 称谓去词
C 神煞一致性：排盘卡显示全量 == 引擎全集；白名单去词校验器；词典单一事实源
D 建议卡基线：prompt 含引擎方向要点 + 防反转硬约束条款
E JSON 泄漏：ToolJsonChunkFilter 任意切块无残留；流式 wrap 协议保持
F 评测派生断言：l2_eval derived 五类型纯函数 + validate_tasks schema + T101-T108 行

运行：cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k11_fact_discipline.py -q -p no:cacheprovider
"""
import dataclasses
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_EVAL = _REPO / "scripts" / "eval_agent"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from src.engines.bazi import BaziEngine, current_stage_facts  # noqa: E402
from src.engines.bazi_formatter import (format_compact_card,  # noqa: E402
                                        format_detailed_chart,
                                        format_fact_pack_block)
from src.engines.advisor_v2 import AdaptiveAdvisor, STYLE_INSTRUCTION  # noqa: E402
from src.engines.shensha import SHENSHA_LUCK  # noqa: E402
from src.utils.fact_guard import (FEMALE_ADDRESS_TERMS,  # noqa: E402
                                  gender_label, guard_gender_terms,
                                  guard_shensha_refs, scrub_turn,
                                  shensha_lexicon)
from src.bot.stream_guard import ToolJsonChunkFilter, wrap_chunk_filter  # noqa: E402


# ============================================================
# fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    return BaziEngine()


@pytest.fixture(scope="module")
def golden(engine):
    """2026-09-06 行 48 事故同款命盘：1999-05-13 9:00 长春 男。"""
    return engine.calculate(1999, 5, 13, 9, 0, "长春", "男")


# ---- k63 辅助：口吻 / 称谓分离断言（按 prompt 结构锚点定位，不依赖具体文案）----

_ADDR_HEAD = "\n## 称谓硬规则（必须遵守）"
# prompt 拼接顺序中「称谓段」之后的各结构锚点（见 advisor_v2._build_prompt）
_SECTION_ANCHORS = (_ADDR_HEAD, "\n【确定性事实包", "\n## 引擎方向要点",
                    "\n## 输出格式要求")


def _next_anchor(prompt: str, start: int) -> int:
    """start 之后最近的结构锚点位置（无则串尾）。"""
    idxs = [prompt.index(a, start) for a in _SECTION_ANCHORS
            if a in prompt[start:]]
    return min(idxs) if idxs else len(prompt)


_STYLE_HEAD = "## 说话风格要求"


def _style_section(prompt: str) -> str:
    """「说话风格要求」段的正文（不含标题行）。"""
    i = prompt.index(_STYLE_HEAD) + len(_STYLE_HEAD)
    return prompt[i:_next_anchor(prompt, i)].strip()


def _strip_addr_block(prompt: str) -> str:
    """移除「称谓硬规则」段（用于证明"除称谓外 prompt 不随性别变化"）。"""
    i = prompt.find(_ADDR_HEAD)
    if i == -1:
        return prompt
    return prompt[:i] + "\n" + prompt[_next_anchor(prompt, i + len(_ADDR_HEAD)):]


def _strip_gender_line(prompt: str) -> str:
    """性别行归一（男/女/未知 → 占位符），其余逐字保留。"""
    return re.sub(r"^性别：.*$", "性别：<G>", prompt, flags=re.M)


# ============================================================
# A：事实包（引擎 current_stage + 两链注入）
# ============================================================

class TestFactPack:
    def test_current_stage_facts_pure(self):
        """固定 now 注入：1999-05-13 生，2026-09-07 = 周岁27/虚岁28/丙寅段/下步乙丑。"""
        dayun = [(3, "戊辰"), (13, "丁卯"), (23, "丙寅"), (33, "乙丑"), (43, "甲子")]
        f = current_stage_facts(
            1999, 5, 13, dayun,
            jiaoyun={"years": [{"sui": 3, "year": 2001}, {"sui": 13, "year": 2011},
                               {"sui": 23, "year": 2021}, {"sui": 33, "year": 2031}]},
            liunian_rel={"year": 2026, "ganzhi": "丙午"},
            now=datetime(2026, 9, 7, 12, 0))
        assert f["year"] == 2026
        assert f["age_zhousui"] == 27
        assert f["age_xusui"] == 28
        assert f["dayun_ganzhi"] == "丙寅"
        assert f["dayun_sui_start"] == 23 and f["dayun_sui_end"] == 32
        assert f["dayun_year_start"] == 2021 and f["dayun_year_end"] == 2030
        assert f["next_ganzhi"] == "乙丑" and f["next_sui"] == 33
        assert f["next_year"] == 2031
        assert f["liunian_ganzhi"] == "丙午"

    def test_current_stage_birthday_not_reached(self):
        """生日未到的年初：周岁比虚岁小 2（岁差窗口边界，禁一刀切 虚岁-1）。"""
        f = current_stage_facts(
            1999, 5, 13, [(23, "丙寅"), (33, "乙丑")],
            now=datetime(2026, 1, 10))
        assert f["age_xusui"] == 28
        assert f["age_zhousui"] == 26  # 5/13 生日未到
        assert f["dayun_ganzhi"] == "丙寅"

    def test_calculate_attaches_current_stage(self, golden):
        cs = golden.current_stage
        assert cs is not None
        assert cs["age_zhousui"] == 27 and cs["age_xusui"] == 28
        assert cs["dayun_ganzhi"] == "丙寅"
        # 出生档案 = 用户提供原时刻（修正前快照），排盘口径另注
        assert cs["birth_solar"][:5] == (1999, 5, 13, 9, 0)
        assert cs["chart_hhmm"]  # 真太阳时修正后时刻（与 corrected_time 同源）
        assert cs["birth_city"] == "长春"

    def test_fact_pack_block_contains_anchor_values(self, golden):
        block = format_fact_pack_block(golden)
        assert "周岁 27 岁（虚岁 28）" in block
        assert "当前大运：丙寅" in block
        assert "乙丑（虚岁 33 起" in block
        assert "只许引用" in block and "禁止自行推算或编造" in block
        assert "出生档案（公历）：1999年5月13日 09:00 长春" in block
        assert "神煞纪律" in block and "共 14 个" in block
        assert "禁止自造或引用名单外的任何神煞名" in block

    def test_fact_pack_fallback_without_current_stage(self):
        """老对象/手工构造无 current_stage → 防编造提示，绝不抛错。"""
        r = SimpleNamespace(bazi=["己卯", "己巳", "乙丑", "辛巳"],
                            shensha=["太极贵人"], liunian_rel=None)
        block = format_fact_pack_block(r)
        assert "不得自行推算编造" in block or "禁止自行推算或编造" in block

    # ---- k11-r1（审查 Important #1）：晚子时/真太阳时归日不得污染年龄事实 ----

    def _exp(self, by, bm, bd):
        """测试运行日口径期望（与 current_stage_facts 同式，跨年自洽）。"""
        from datetime import date
        today = date.today()
        xusui = today.year - by + 1
        zhousui = today.year - by - (
            1 if (today.month, today.day) < (bm, bd) else 0)
        return xusui, zhousui

    def test_engine_boundary_dec31_late_child_snapshot(self, engine):
        """1999-12-31 23:30（晚子时归日为 2000-01-01）：年龄按原始快照 12-31。"""
        r = engine.calculate(1999, 12, 31, 23, 30, "北京", "男")
        xusui, zhousui = self._exp(1999, 12, 31)
        cs = r.current_stage
        assert cs["age_xusui"] == xusui      # 2026 年 = 28（归日口径曾误 27）
        assert cs["age_zhousui"] == zhousui
        assert cs["birth_solar"][:5] == (1999, 12, 31, 23, 30)
        assert cs["lunar_date_shifted"] is True  # 农历/四柱按排盘口径次日
        block = format_fact_pack_block(r)
        assert "周岁 %s 岁（虚岁 %s）" % (zhousui, xusui) in block
        assert "（按排盘口径日期）" in block

    def test_engine_boundary_birthday_day_late_child(self, engine):
        """生日当天 23:30（归日次日）：周岁按生日已到计（不因归日 -1）。"""
        r = engine.calculate(1999, 9, 7, 23, 30, "北京", "男")
        _x, zhousui = self._exp(1999, 9, 7)
        assert r.current_stage["age_zhousui"] == zhousui
        assert r.current_stage["age_xusui"] == _x

    def test_pure_boundary_now_fixed(self):
        """纯函数（固定 now）：跨年 23:xx 虚岁与生日当天周岁边界。"""
        dayun = [(3, "戊辰"), (13, "丁卯"), (23, "丙寅"), (33, "乙丑")]
        f = current_stage_facts(1999, 12, 31, dayun,
                                now=datetime(2026, 9, 7, 12, 0))
        assert f["age_xusui"] == 28 and f["age_zhousui"] == 26
        f2 = current_stage_facts(1999, 9, 7, dayun,
                                 now=datetime(2026, 9, 7, 12, 0))
        assert f2["age_xusui"] == 28 and f2["age_zhousui"] == 27  # 当天生日
        # 非跨日时段不受影响（原单测不变式）
        f3 = current_stage_facts(1999, 5, 13, dayun,
                                 now=datetime(2026, 9, 7, 12, 0))
        assert f3["age_xusui"] == 28 and f3["age_zhousui"] == 27

    def test_main_chain_format_chart_injects_fact_pack(self, golden):
        """主链 _format_chart 注入事实包（A）+ 性别行（B 主链先例保持）。"""
        from src.llm.client import FortuneLLM
        llm = object.__new__(FortuneLLM)
        chart = llm._format_chart(golden)
        assert "性别：男" in chart
        assert "周岁 27 岁（虚岁 28）" in chart
        assert "当前大运：丙寅" in chart
        assert "禁止自造或引用名单外的任何神煞名" in chart

    def test_advisor_prompt_injects_fact_pack_and_baseline(self, golden):
        # k63：_build_prompt 的 persona 参数已删（口吻统一为 STYLE_INSTRUCTION，
        # 不再按性别分支）→ 调用签名 3 参改 2 参；本用例其余断言逐条未动。
        p = AdaptiveAdvisor()._build_prompt(golden, "国企还是金融")
        assert "性别：男" in p
        assert "确定性事实包" in p
        assert "周岁 27 岁（虚岁 28）" in p
        # D：引擎方向要点（行业五行映射表同 career_dir 同源）+ 防反转硬约束
        assert "引擎方向要点（确定性基线" in p
        assert "行业五行映射" in p
        assert "方位映射" in p
        assert "硬约束条款" in p
        assert "不得反转" in p or "不得与基线相悖" in p
        assert "禁止自行推算或编造" in p


# ============================================================
# B：性别与称谓
# ============================================================

class TestGenderPersona:
    """k63：口吻统一（不再按性别分支）；称谓（≠口吻）仍分流。"""

    def test_style_instruction_uniform_across_genders(self, golden):
        """风格段与性别无关（k63 核心不变式）。

        原断言（k11-B）：男 →「当前模式：理性分析师」、女 →「当前模式：毒舌闺蜜」
        —— 证明的是"按性别分叉"这一旧行为本身。新断言证明相反面且更强：
        三性别 prompt 的「说话风格要求」段**逐字相同**、恒等于单一常量
        STYLE_INSTRUCTION，且不含任何 persona 词与旧口癖。等价性：旧断言只覆盖
        分叉的两端各自的内容，新断言同时覆盖"内容正确"与"与性别无关"。
        """
        adv = AdaptiveAdvisor()
        prompts = {g: adv._build_prompt(dataclasses.replace(golden, gender=g), "建议")
                   for g in ("男", "女", "unknown")}
        styles = {g: _style_section(p) for g, p in prompts.items()}
        assert len(set(styles.values())) == 1, styles   # 不随性别变化
        s = styles["男"]
        assert s == STYLE_INSTRUCTION                   # 单一来源，无分支
        # 主链豆包口径的关键短语（与 handler.py system prompt 同源词句）
        assert "说话像豆包" in s
        assert "把专业术语（五行、十神、神煞、大运等）讲成大白话" in s
        assert "禁止油滑/套近乎开场白" in s
        assert "不挖苦、不嘲讽、不贬低用户" in s
        for w in ("毒舌", "闺蜜", "理性分析师", "温柔陪伴者", "该怼就怼",
                  "麦肯锡", "心理咨询师", "当前模式"):
            assert w not in s, w
        # 全 prompt 面：旧断言（`"毒舌闺蜜" not in p_male` /
        # `"像闺蜜一样说实话" not in p_male`）原样保留并推广到三性别
        # （男/未知 prompt 的「称谓硬规则」段合法含"闺蜜"二字，故此处只锁
        # 人设词与旧口癖，不含裸"闺蜜"）
        for g, p in prompts.items():
            for w in ("毒舌闺蜜", "像闺蜜一样说实话", "理性分析师",
                      "温柔陪伴者", "当前模式", "麦肯锡顾问", "该怼就怼"):
                assert w not in p, f"{g}: {w}"

    def test_prompt_differs_by_gender_only_in_address(self, golden):
        """除「性别行」与「称谓硬规则」段外，三性别 prompt 逐字相同。

        命盘固定（dataclasses.replace 只改 gender）→ 性别是唯一自变量，
        证明口吻不再随性别变化（不只是风格段，而是整段 prompt）。
        """
        adv = AdaptiveAdvisor()
        raw, norm = {}, {}
        for g in ("男", "女", "unknown"):
            p = adv._build_prompt(dataclasses.replace(golden, gender=g), "建议")
            raw[g] = p
            norm[g] = _strip_gender_line(_strip_addr_block(p))
        assert norm["男"] == norm["女"] == norm["unknown"]
        assert raw["男"] != raw["女"]      # 差异确实存在（性别行/称谓段），非空比

    def test_address_guard_kept_by_gender(self, golden):
        """称谓校验仍在（= k11-B 原断言逐条保留，只剥离与 persona 耦合的部分）。

        原用例断言：男 → 有称谓硬规则段 + 严禁女性向称谓；女 → 无该段。
        本用例原样保留上述四条，另补 unknown（同一 _gender_cn 归一档）。
        """
        adv = AdaptiveAdvisor()
        p_male = adv._build_prompt(dataclasses.replace(golden, gender="男"), "建议")
        p_unk = adv._build_prompt(dataclasses.replace(golden, gender="unknown"), "建议")
        p_female = adv._build_prompt(dataclasses.replace(golden, gender="女"), "建议")
        assert "称谓硬规则（必须遵守）" in p_male
        assert "严禁任何女性向称谓或闺蜜口吻" in p_male
        assert "性别：男" in p_male
        assert "称谓硬规则（必须遵守）" in p_unk
        assert "性别：未知（请用中性表述，勿假设性别）" in p_unk
        assert "性别：女" in p_female
        # 称谓硬规则段只在非女命时注入（k11-B 行为原样保留）
        assert "称谓硬规则（必须遵守）" not in p_female

    def test_generate_prompt_uniform_across_genders(self, golden, monkeypatch):
        """生产路径（generate → _build_prompt，handler.py:8692 消费）同样不分支。

        _build_prompt 直调面之外，锁 generate 全链路：捕获真实发给 LLM 的
        prompt，断言跨性别规范后逐字相同 —— 防未来在 generate 内重新引入
        persona 分支（那正是 k11-B 分叉的所在地）。
        """
        adv = AdaptiveAdvisor()
        holder = {}

        def _fake_call(prompt, api_key):
            holder["prompt"] = prompt
            return json.dumps({"actions": [], "serendipity": "", "daily_tip": "",
                               "style_notes": ""}, ensure_ascii=False)

        monkeypatch.setattr(adv, "_call_llm", _fake_call)
        captured = {}
        for g in ("男", "女", "unknown"):
            adv.generate(dataclasses.replace(golden, gender=g), "建议", api_key="k")
            captured[g] = holder["prompt"]
        assert captured["男"] != captured["女"]      # 性别行/称谓段差异仍在
        assert len({_style_section(p) for p in captured.values()}) == 1
        assert len({_strip_gender_line(_strip_addr_block(p))
                    for p in captured.values()}) == 1

    def test_gender_label_normalize(self):
        assert gender_label("男") == "男" and gender_label("male") == "男"
        assert gender_label("女") == "女" and gender_label("female") == "女"
        assert gender_label("") == "unknown" and gender_label(None) == "unknown"

    def test_guard_gender_terms_male_and_unknown(self):
        text = "姐妹，听姐一句劝。亲爱的别冲动，姑娘你听我说。"
        cleaned, hits = guard_gender_terms(text, "男")
        assert set(hits) <= set(FEMALE_ADDRESS_TERMS)
        for t in ("姐妹", "亲爱的", "姑娘"):
            assert t not in cleaned
        cleaned2, _ = guard_gender_terms(text, "unknown")
        assert "姐妹" not in cleaned2
        # 女命放行
        cleaned3, hits3 = guard_gender_terms(text, "女")
        assert cleaned3 == text and hits3 == []

    def test_generate_scrubs_advice_fields(self, golden, monkeypatch):
        """mock LLM 输出带 姐妹/文昌贵人 → generate 字段层去词（B/C 校验器）。"""
        advisor = AdaptiveAdvisor()
        fake = json.dumps({
            "actions": [{"category": "事业", "advice": "姐妹，金融更旺你，命带文昌贵人加持",
                         "timing": "2027年9月15日-10月15日", "confidence": "high"}],
            "serendipity": "亲爱的，注意健康",
            "daily_tip": "宜静不宜动",
            "style_notes": "",
        }, ensure_ascii=False)
        monkeypatch.setattr(advisor, "_call_llm", lambda prompt, api_key: fake)
        out = advisor.generate(golden, user_context="国企还是金融", api_key="k")
        joined = json.dumps(out, ensure_ascii=False)
        assert "姐妹" not in joined and "亲爱的" not in joined
        assert "文昌贵人" not in joined
        assert "金融" in joined  # 正文语义保留，只去称谓/白名单外神煞词
        assert out["actions"][0]["category"] == "事业"
        assert "健康" in out["serendipity"]

    def test_generate_scrub_keeps_female_terms_for_female_user(self, engine,
                                                               monkeypatch):
        """fact_guard 女命放行（k11-B 行为原样保留；仅改名去 persona 误导）。

        k63 前用例名 test_generate_keeps_girlfriend_persona_for_female 易读成
        "引擎仍在维持闺蜜人设"——k63 后 prompt 已无该人设，本用例真实锁的是
        **输出后校验器**对女命文本不去词（B 校验器语义），断言逐条未动。
        """
        advisor = AdaptiveAdvisor()
        female = engine.calculate(1999, 5, 13, 9, 0, "长春", "女")
        fake = json.dumps({"actions": [{"category": "事业", "advice": "姐妹别怕",
                                        "timing": "X", "confidence": "low"}],
                           "serendipity": "", "daily_tip": "", "style_notes": ""},
                          ensure_ascii=False)
        monkeypatch.setattr(advisor, "_call_llm", lambda prompt, api_key: fake)
        out = advisor.generate(female, user_context="建议", api_key="k")
        assert "姐妹" in out["actions"][0]["advice"]  # 女命闺蜜口吻保留


# ============================================================
# C：神煞一致性（显示全量 + 白名单）
# ============================================================

class TestShenshaConsistency:
    def test_cards_show_full_shensha(self, golden):
        """显示端集合 = 引擎全集（截断裂缝修复：卡引用任何神煞用户都看得到）。"""
        engine_all = list(golden.shensha)
        assert len(engine_all) == 14
        compact = format_compact_card(golden)
        detailed = format_detailed_chart(golden)
        for name in engine_all:
            assert name in compact, f"紧凑卡缺神煞 {name}"
            assert name in detailed, f"详细卡缺神煞 {name}"
        # 紧凑卡神煞行 = 全量（无"等N项"截断语义残留）
        line = next(l for l in compact.splitlines() if l.startswith("⭐ 神煞"))
        assert "等" not in line.split("：")[1] or "等" not in line

    def test_shensha_lexicon_single_source(self):
        """词典 = SHENSHA_LUCK 键集（59 型全集单一事实源），含事故幻觉词 文昌贵人。"""
        lex = set(shensha_lexicon())
        assert set(SHENSHA_LUCK) == lex
        for w in ("文昌贵人", "天乙贵人", "太极贵人", "孤辰", "寡宿", "童子煞"):
            assert w in lex

    def test_guard_shensha_allowlist(self):
        allow = ["太极贵人", "金舆"]
        # 白名单内引用保留
        cleaned, hits = guard_shensha_refs("命带太极贵人，金舆主财", allow)
        assert cleaned == "命带太极贵人，金舆主财" and hits == []
        # 白名单外（真幻觉文昌贵人）去词 + 命中
        cleaned2, hits2 = guard_shensha_refs("命带文昌贵人，学习利", allow)
        assert "文昌贵人" not in cleaned2 and hits2 == ["文昌贵人"]
        # allow 为空（无上下文）→ 跳过不误伤
        cleaned3, hits3 = guard_shensha_refs("命带文昌贵人", [])
        assert cleaned3 == "命带文昌贵人" and hits3 == []

    def test_scrub_turn_combo(self):
        cleaned = scrub_turn("姐妹，金融属金压力大，文昌贵人护你", "男",
                             ["太极贵人", "金融"])
        assert "姐妹" not in cleaned and "文昌贵人" not in cleaned
        assert "金融属金压力大" in cleaned


# ============================================================
# E：JSON 泄漏 chunk 级过滤
# ============================================================

class TestToolJsonChunkFilter:
    _SAMPLE = ("正文开头。<tool_calls>[{\"tool\": \"web_search\", \"params\": "
               "{\"query\": \"易宝支付 行业 前景\"}}]</tool_calls>然后继续。")

    def test_any_chunk_boundary_no_residue(self):
        for split in range(1, len(self._SAMPLE) + 1):
            f = ToolJsonChunkFilter()
            out = []
            i = 0
            while i < len(self._SAMPLE):
                j = min(i + split, len(self._SAMPLE))
                out.append(f.feed(self._SAMPLE[i:j]))
                i = j
            got = "".join(out) + f.finish()
            assert "tool_calls" not in got
            assert "web_search" not in got
            assert "查询" not in got and "params" not in got
            assert "正文开头。" in got and "然后继续。" in got
            assert got.index("正文开头。") < got.index("然后继续。")

    def test_plain_text_passthrough(self):
        f = ToolJsonChunkFilter()
        text = "普通回复没有工具标签，随便发什么都在。"
        got = "".join([f.feed(text[i:i + 4]) for i in range(0, len(text), 4)])
        assert got == text

    def test_unclosed_block_dropped_at_finish(self):
        f = ToolJsonChunkFilter()
        out = f.feed("<tool_calls>[{\"tool\": \"web_search\"") + f.finish()
        assert out == "" and f._buf == ""

    def test_mid_json_close_then_body(self):
        f = ToolJsonChunkFilter()
        got = f.feed("[{\"tool\": \"web_search\", \"params\": {\"query\": \"x\"}}]")
        assert got == ""
        got2 = f.feed("正文继续")
        assert got2 == "正文继续"

    def test_wrap_preserves_protocol(self):
        seen = []
        cb = wrap_chunk_filter(
            lambda evt, payload: seen.append((evt, dict(payload))))
        cb("tool", {"text": "正在搜索…"})
        cb("chunk", {"text": "A<tool_calls>[{\"tool\":\"web_search\"}]</tool_calls>B"})
        cb("thinking", {"text": "推演中"})
        assert seen[0] == ("tool", {"text": "正在搜索…"})
        assert seen[1][1]["text"] == "AB"
        assert seen[2] == ("thinking", {"text": "推演中"})
        assert wrap_chunk_filter(None) is None


# ============================================================
# F：评测派生断言 + schema（纯规则，零 LLM）
# ============================================================

_FACTS = {"gender": "男",
          "shensha": ["太极贵人", "金舆", "禄神"],
          "age_zhousui": 27, "age_xusui": 28, "dayun_ganzhi": "丙寅"}
_SPEC = {"contains": [], "neg_checks": ["{"], "regex": [], "min_len": 1,
         "derived": [{"type": "age_claim"}, {"type": "dayun_claim"},
                     {"type": "gender_addr"}, {"type": "shensha_refs"},
                     {"type": "tool_json"}]}


def _eval_derived(reply, spec=None):
    import l2_eval
    return l2_eval.eval_derived_checks(
        {"id": "T101", "reply_checks": spec or _SPEC}, reply, _FACTS)


class TestEvalDerived:
    def test_ok_reply_all_pass(self):
        r = "你今年27岁（虚岁28），现在正走丙寅大运。命带太极贵人。"
        res = {c["name"]: c["ok"] for c in _eval_derived(r)}
        assert all(res.values()), res

    def test_golden_bad_reply_all_catch(self):
        """行 48/52 事故同款坏回复（33岁/乙丑/姐妹/文昌贵人）——逐类拦截。"""
        r = "醒醒吧姐妹，你今年33岁刚进乙丑大运，命带文昌贵人。"
        res = {c["name"]: c for c in _eval_derived(r)}
        assert res["derived.age_claim"]["ok"] is False
        assert "33" in res["derived.age_claim"]["detail"]
        assert res["derived.dayun_claim"]["ok"] is False
        assert "乙丑" in res["derived.dayun_claim"]["detail"]
        assert res["derived.gender_addr"]["ok"] is False
        assert res["derived.shensha_refs"]["ok"] is False
        assert "文昌贵人" in res["derived.shensha_refs"]["detail"]

    def test_tool_json_leak_caught(self):
        r = "正文<tool_calls>[{\"tool\": \"web_search\"}]</tool_calls>后面"
        res = {c["name"]: c for c in _eval_derived(r)}
        assert res["derived.tool_json"]["ok"] is False

    def test_dayun_table_ages_do_not_false_positive(self):
        """排盘卡大运表（3岁戊辰→13岁丁卯→23岁丙寅→33岁乙丑）不得误判当前年龄。"""
        r = ("【排盘卡】大运：3岁戊辰 → 13岁丁卯 → 23岁丙寅 → 33岁乙丑。"
             "你现在27岁，正走丙寅运。")
        res = {c["name"]: c for c in _eval_derived(r)}
        assert res["derived.age_claim"]["ok"] is True
        assert res["derived.dayun_claim"]["ok"] is True

    # ---- k11-r1（审查 Important #2）：大运段端点岁数不得被裸"岁+句读"误报 ----

    def test_review_reproductions_dayun_endpoint_ages_pass(self):
        """审查 3 个真实复现（真实合格回复曾被判 FAIL）：全部放行。"""
        cases = [
            "丙寅大运走到32岁，然后2031年33岁换乙丑",
            "丙寅运从23岁到32岁，属于黄金十年",
            "当前大运是丙寅（23-32岁），明年交乙丑。",
        ]
        for rep in cases:
            res = {c["name"]: c for c in _eval_derived(rep)}
            assert res["derived.age_claim"]["ok"] is True, rep
            assert res["derived.dayun_claim"]["ok"] is True, rep

    def test_review_real_current_age_claims_still_caught(self):
        """收紧后真实当前年龄声明仍拦截（事故同款表述）。"""
        bad = "你今年33岁，刚进乙丑大运，命带文昌贵人。"
        res = {c["name"]: c for c in _eval_derived(bad)}
        assert res["derived.age_claim"]["ok"] is False  # 33 在窗 [26,29] 外
        assert res["derived.dayun_claim"]["ok"] is False  # 乙丑≠丙寅
        assert res["derived.shensha_refs"]["ok"] is False
        good = "你现在27岁（虚岁28），正走丙寅大运，明年换乙丑。"
        res2 = {c["name"]: c for c in _eval_derived(good)}
        assert res2["derived.age_claim"]["ok"] is True
        assert res2["derived.dayun_claim"]["ok"] is True

    # ---- k15（k11b review r1-2 Minor）：双单位变体（虚岁23岁到32岁）误报 ----

    def test_review_double_unit_range_variants_pass(self):
        """3 个复现样例（真实合格回复曾被判 FAIL）：双单位（单位前缀+段连接）变体
        全部放行——23/32 是大运段端点，不是当前年龄声明。"""
        cases = [
            "丙寅大运虚岁23岁到32岁。",
            "虚岁23岁至32岁，属于黄金十年，机会很多。",
            "虚岁23岁～32岁，是我人生的黄金十年。",
        ]
        for rep in cases:
            res = {c["name"]: c for c in _eval_derived(rep)}
            assert res["derived.age_claim"]["ok"] is True, rep
        # 反式前置连接（「从23岁到虚岁32岁」类端点同样不得误报）
        res = {c["name"]: c for c in
               _eval_derived("丙寅运从23岁到虚岁32岁。")}
        assert res["derived.age_claim"]["ok"] is True

    # ---- k35-A4 + k35-fix：pattern2 右侧前瞻放行「显式时点语境」 ----

    def test_a4_shi_variant_endpoint_phrases_pass(self):
        """「虚岁33岁时进入乙丑大运」类换运**时点**句（base 误拦，实测证据见
        k17 文件 test_known_misfire_families_documented 记录）→ 放行。

        k35-fix（复审 Important-1）：放行面 = `岁时` + 显式换运动词白名单，
        不再是裸字符「时」全放行。"""
        cases = [
            "虚岁33岁时进入乙丑大运",
            "虚岁33岁时，你已换入乙丑大运。",
            "到虚岁33时，乙丑大运开始。",
            "虚岁23岁时走丙寅大运。",
            "虚岁33岁时转入乙丑大运。",
            "虚岁33岁时起运。",
            "虚岁33岁时，我步入乙丑大运。",
        ]
        for rep in cases:
            res = {c["name"]: c for c in _eval_derived(rep)}
            assert res["derived.age_claim"]["ok"] is True, rep

    def test_a4_real_age_claims_still_blocked(self):
        """判别力（未放宽）：带/不带「时」的真实当前年龄声明仍拦。"""
        bad = [
            "你今年虚岁33岁时运不济。",   # 前缀型命中：时点词不豁免前缀声明
            "命主今年33岁时来运转。",
            "命主虚岁33岁，正走乙丑运。",
            "今年我虚岁33岁，正走乙丑大运。",
            "虚岁33了",
            "我现在33岁了。",
            # k35-fix（复审 Important-1）：裸「时」全放行时的过宽面 → 已拦回
            "虚岁33岁时运不济。",
            "虚岁33岁时，我事业起飞。",
            "虚岁33岁时已经结婚。",
            "今年我虚岁33岁时运不济。",
        ]
        for rep in bad:
            res = {c["name"]: c for c in _eval_derived(rep)}
            assert res["derived.age_claim"]["ok"] is False, rep

    def test_double_unit_fix_real_claims_still_caught(self):
        """双单位收紧后真实事故句仍拦截（既有真实断言回归锁定）。"""
        bad = [
            "你今年33岁，刚进乙丑大运，命带文昌贵人。",
            "我现在33岁了。",
            "命主今年33岁，正走乙丑运。",
            "命主虚岁33岁，正走乙丑运。",
        ]
        for rep in bad:
            res = {c["name"]: c for c in _eval_derived(rep)}
            assert res["derived.age_claim"]["ok"] is False, rep
        # 窗口内合法当前年龄表述不受收紧影响（边界: 句末/句读后接）
        good = ["你今年虚岁28岁。", "虚岁28岁，现在走丙寅大运。",
                "你今年28虚岁，正走丙寅大运。"]
        for rep in good:
            res = {c["name"]: c for c in _eval_derived(rep)}
            assert res["derived.age_claim"]["ok"] is True, rep

    def test_require_mention(self):
        spec = {"contains": [], "neg_checks": ["{"], "regex": [], "min_len": 1,
                "derived": [{"type": "age_claim",
                             "params": {"require_mention": True}}]}
        res = _eval_derived("你正走丙寅大运，建议深耕。", spec)
        assert res[0]["ok"] is False and "27" in res[0]["detail"]
        res2 = _eval_derived("你今年28虚岁，正走丙寅大运。", spec)
        assert res2[0]["ok"] is True

    def test_derive_facts_from_task_row(self):
        """T101 行 setup.persons → 引擎复算派生 = 报告锚点（27/28/丙寅/14 神煞）。"""
        import l2_eval
        rows = [json.loads(l) for l in
                (_REPO / "data/eval/agent_tasks.jsonl").read_text(encoding="utf-8")
                .splitlines() if l.strip().startswith('{"id": "T10')]
        facts = l2_eval.derive_facts(next(t for t in rows if t["id"] == "T101"))
        assert facts["gender"] == "男"
        assert facts["age_zhousui"] == 27 and facts["age_xusui"] == 28
        assert facts["dayun_ganzhi"] == "丙寅"
        assert len(facts["shensha"]) == 14
        assert "文昌贵人" not in facts["shensha"]


class TestEvalSchema:
    def test_eval_set_valid_and_total_108(self):
        """评估集全绿且总数 = 108（100 基线 + k11-F T101-T103 + k15 T104-T108）。"""
        import validate_tasks as vt
        errs, tasks = vt.validate_file(str(_REPO / "data/eval/agent_tasks.jsonl"))
        assert not errs, errs[:5]
        cerrs, stats = vt.coverage_errors(tasks)
        assert not cerrs, cerrs
        assert stats["total"] == 108
        ids = [t["id"] for t in tasks]
        for tid in ("T101", "T102", "T103", "T104", "T105",
                    "T106", "T107", "T108"):
            assert tid in ids

    def test_derived_schema_validation(self):
        import validate_tasks as vt
        base = {"id": "T101", "title": "x", "category": "fortune",
                "severity": "P1", "pass_k": 1, "source": "bug-k11",
                "turns": [{"role": "user", "text": "q"}],
                "expected_tools": [], "no_tool": False,
                "reply_checks": {"contains": [], "neg_checks":
                                 ["{", "undefined", "NaN", "null"],
                                 "regex": [], "min_len": 1}}
        # 非法 type
        bad = json.loads(json.dumps(base))
        bad["reply_checks"]["derived"] = [{"type": "nonsense"}]
        errs = []
        vt.check_task(bad, errs)
        assert any("type" in e and "非法" in e for e in errs)
        # 需要 persons 却缺失
        bad2 = json.loads(json.dumps(base))
        bad2["reply_checks"]["derived"] = [{"type": "age_claim"}]
        errs2 = []
        vt.check_task(bad2, errs2)
        assert any("setup.persons" in e for e in errs2)
        # 合法（带 persons）
        good = json.loads(json.dumps(base))
        good["setup"] = {"persons": [{"name": "a", "gender": "male",
                                      "birth": "1999-05-13 09:00",
                                      "city": "长春"}]}
        good["reply_checks"]["derived"] = [{"type": "age_claim",
                                            "params": {"require_mention": True}},
                                           {"type": "tool_json"}]
        errs3 = []
        vt.check_task(good, errs3)
        assert not errs3, errs3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
