# -*- coding: utf-8 -*-
"""k33/A3：GLM 降级链格式漂移适配（【工具名】包裹 / ```json 围栏 / 散文粘连 /
裸引号载荷）—— `src/bot/tool_calls.py` 末道兜底解析 + 剥离。

背景（B3 smoke + k33 探针实录）：glm-4-flash 在降级链上不吐标准
`<tool_calls>[…]</tool_calls>` 工单，观察到 5 类漂移形态；旧解析只覆盖
`工具名\\n{JSON}` 一种（Task A1），其余形态解析落空 → 工具不执行 →
择日场景（D3 的 2 个已知失败）只能返回澄清话术。

红线：本批只做**加法**（新增末道分支 + 新增剥离层），既有各层行为不变——
本文件含回归用例锁住「既有层仍按原样命中」。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_glm_drift_format.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.bot.tool_calls import (  # noqa: E402
    parse_glm_bare_format,
    parse_glm_wrapped_format,
    parse_tool_calls,
    strip_tool_calls,
)

# ── 3 个验收形态（审计行口径：【web_search】 包裹 / ```json 围栏 / 两者叠加）──

FORM_BRACKET = '【web_search】\n{"query": "2026年经济走势"}'
FORM_FENCE = 'web_search\n```json\n{"query": "2026年经济走势"}\n```'
FORM_BRACKET_FENCE = '【web_search】\n```json\n{"query": "2026年经济走势"}\n```'


class TestThreeAcceptanceForms:
    """审计验收：3 形态解析测试（改回旧代码必失败）。"""

    @pytest.mark.parametrize("text", [FORM_BRACKET, FORM_FENCE, FORM_BRACKET_FENCE])
    def test_three_forms_parse_to_registered_tool(self, text):
        calls = parse_tool_calls(text)
        assert len(calls) == 1, f"未解析：{text!r}"
        assert calls[0].name == "搜索"
        assert calls[0].params_obj == {"query": "2026年经济走势"}

    @pytest.mark.parametrize("text", [FORM_BRACKET, FORM_FENCE, FORM_BRACKET_FENCE])
    def test_three_forms_stripped_from_visible_text(self, text):
        """载荷绝不落用户可见文本（防泄漏）。"""
        out = strip_tool_calls(text)
        assert out.strip() == ""
        assert "2026年经济走势" not in out

    def test_old_bare_form_still_parsed_by_existing_layer(self):
        """回归：Task A1 裸格式（既有层）行为不变。"""
        text = 'web_search\n{"query": "既有形态"}'
        assert [(c.name, c.params_obj) for c in parse_glm_bare_format(text)] == \
            [("搜索", {"query": "既有形态"})]

    def test_old_layers_win_when_present(self):
        """回归：既有层命中时新层不参与（加法不改既有结果）。"""
        text = ('<tool_calls>[{"tool": "zeri", "params": {"scene": "搬家"}}]'
                '</tool_calls>\n【web_search】\n{"query": "x"}')
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["择日"], "JSON 工单优先语义不得改变"


class TestProbeObservedForms:
    """k33 探针实测（2026-09-12，glm-4-flash 真实输出）额外形态。"""

    def test_prose_glued_name_same_line(self):
        """「稍等一下。web_search\\n{…}」——散文前缀与工具名同行粘连。"""
        text = '稍等一下。web_search\n{"关键词": "2026年新能源汽车行业前景"}'
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["搜索"]
        assert calls[0].params_obj == {"关键词": "2026年新能源汽车行业前景"}
        # 剥离只切「工具名 + 载荷」：散文前缀（模型开场白）留给用户可见文本
        assert strip_tool_calls(text).strip() == "稍等一下。"
        assert "关键词" not in strip_tool_calls(text)

    def test_prose_glued_sentence_prefix(self):
        """「我使用工具搜索…。搜索\\n{…}」——工具名在句末粘连（后缀匹配取最长）。"""
        text = '稍等，我用工具查一下。搜索\n{"搜索关键词": "黄金价格"}'
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["搜索"]

    def test_zeri_probe_raw_output_parses(self):
        """探针实录原文 → 择日工具可解析（D3 失败链路）。"""
        text = '择日\n{"场景": "搬家", "时间范围": "2026年9月15日"}'
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["择日"]
        assert calls[0].params_obj["场景"] == "搬家"

    def test_english_cap_id_in_brackets(self):
        assert [c.name for c in parse_tool_calls(
            '【zeri】\n{"scene": "开业"}')] == ["择日"]

    def test_bracketed_name_with_fenced_json(self):
        """【名】 + ```json 围栏 + JSON（标记叠加仍只算一条调用）。"""
        text = '【搜索】\n```json\n{"关键词": "黄金价格"}\n```'
        calls = parse_tool_calls(text)
        assert [(c.name, c.params_obj) for c in calls] == \
            [("搜索", {"关键词": "黄金价格"})]
        assert strip_tool_calls(text).strip() == ""


class TestAuditCounterExamples:
    """k33 审查 I1 反例集（**改回旧实现必失败**）。

    旧漂移层在既有各层零命中时把普通叙述误判成工具调用并**真实执行工具**
    （`handler.py:1711 parse_tool_calls(reply)` → `_execute_tool_call`），
    且把可见正文整块吞掉。本集合锁住「叙述文本一律不触发、不剥字」。
    """

    @pytest.mark.parametrize("text", [
        # ① 后缀匹配误判：叙述句里的工具名 + JSON（审查实跑：旧版 → ['排盘']）
        '我的建议：先看排盘\n{"年": "丙子", "月": "腊月"}',
        # ② 同族：真外呼 web_search（旧版 → ['搜索']）
        '想查实时信息就用一下搜索\n{"q": "今天天气"}',
        # ②b 同族变体：工具名前的字同样不是句末符
        '我建议你直接用排盘\n{"性别": "男"}',
        '先调用搜索\n{"q": "x"}',
        '这个功能叫搜索\n{"q": "x"}',
    ])
    def test_glued_name_in_prose_not_tool_call(self, text):
        assert parse_tool_calls(text) == [], f"叙述文本不得解析成工具调用：{text!r}"
        assert strip_tool_calls(text) == text.strip(), "可见正文不得被吞"

    @pytest.mark.parametrize("text", [
        # ③ 裸引号串载荷（旧版 → ['搜索']）——与「怎么用搜索」的解释文本不可区分
        '搜索\n"2026年运势"',
        '搜索\n"关键词"',
        '排盘\n"1990年3月5日 午时"',
    ])
    def test_bare_quoted_payload_not_tool_call(self, text):
        assert parse_tool_calls(text) == []
        assert strip_tool_calls(text) == text.strip()

    @pytest.mark.parametrize("text", [
        # ④ 散文参数行（旧版 → ['搜索']/['排盘']）——与表单式正文不可区分
        '搜索\n关键词: 黄金价格\n以上。',
        '排盘\n姓名,1990年3月5日',
        '排盘\n姓名,1990年3月5日\n性别,男',
        '择日\n搬家,2026年9月15日',
        '好的，我先看看。\n\n择日\n开业,2026年8月20日',
    ])
    def test_prose_param_line_not_tool_call(self, text):
        """裸名 + 自然语言参数行与正文结构不可区分 → 整体取消该形态。

        取舍（已请控制方裁定方向）：宁可漏判（模型该调工具时走澄清话术），
        不可误判（叙述文本被真实执行 + 正文被吞）。
        """
        assert parse_tool_calls(text) == []
        assert strip_tool_calls(text) == text.strip()

    @pytest.mark.parametrize("text", [
        # ⑤ 非 JSON 载荷 / 未注册名 / 解释工具格式的文本
        '【提示】\n请描述你想问的问题',
        '【搜索】\n你可以输入想查的内容',
        '【搜索】\n"关键词"的格式就是这样',
        '在下面输入【搜索】\n"关键词"',
        '工具名写成【排盘】\n{"年": "丙子"}',
    ])
    def test_no_marker_no_parse(self, text):
        assert parse_tool_calls(text) == []
        assert strip_tool_calls(text) == text.strip()

    def test_block_start_required_even_with_brackets(self):
        """【】也在叙述句中（非行首/句末）→ 不认（块起点判据覆盖全部形态）。"""
        text = '工具名写成【web_search】\n{"query": "x"} 这样就能触发'
        assert parse_tool_calls(text) == []
        assert strip_tool_calls(text) == text.strip()


class TestNoFalsePositive:
    """防误伤：纯正文不得被判成工具调用、不得被剥（宁可漏判不可误判）。"""

    @pytest.mark.parametrize("text", [
        "你说的搜索功能很好用",
        "我建议你先搜索\n然后我们再讨论",
        "正文段落\n{不是工具}",
        "我通常用 web_search 这个词",
        '请回复「搜索」\n"你好"',
        "【提示】web_search 是搜索引擎的名字",
        '这个工具叫【搜索】\n它很好用',
    ])
    def test_plain_text_untouched(self, text):
        assert parse_tool_calls(text) == []
        assert strip_tool_calls(text) == text.strip()

    def test_unregistered_tool_not_parsed(self):
        assert parse_tool_calls('【hack_tool】\n{"cmd": "rm -rf /"}') == []
        # 未知工具不剥（剥离层与解析层同源：未命中名原文保留）
        assert "hack_tool" in strip_tool_calls('【hack_tool】\n{"cmd": "x"}')

    def test_prose_glued_name_with_quoted_payload_not_parsed(self):
        """散文粘连名只认 JSON 对象载荷（防正文引号串误判）。"""
        assert parse_tool_calls('我说的是搜索\n"关键词"') == []

    def test_incomplete_payload_not_parsed(self):
        assert parse_tool_calls('【web_search】\n{不完整') == []
        assert parse_glm_wrapped_format('【web_search】\n{不完整') == []


class TestStripParityWithParse:
    """解析/剥离同源：凡解析到的块，必从可见文本剥净（载荷不漏）。"""

    @pytest.mark.parametrize("text", [
        '开场白。\n\n【web_search】\n{"query": "x"}\n\n正文继续。',
        '开场白。\n\nweb_search\n```json\n{"query": "x"}\n```\n\n正文继续。',
        '开场白。搜索\n{"q": "x"} 正文继续。',
    ])
    def test_parse_then_strip_no_payload_leak(self, text):
        calls = parse_tool_calls(text)
        assert calls, "本用例前提：可解析"
        out = strip_tool_calls(text)
        assert "query" not in out and '"q"' not in out
        assert "web_search" not in out
        assert "开场白" in out


# ── 真实冒烟（glm-4-flash 免费模型；无 key 自动跳过）──────────────────

_LIVE_PROMPT = (
    "你是易理明灯。需要工具时按 JSON 工单调用。\n\n[可用工具清单]\n"
)
_LIVE_REQ = "2026年9月15日搬家 帮我选个日子"


@pytest.mark.skipif(not os.environ.get("ZHIPU_API_KEY"),
                    reason="需要 ZHIPU_API_KEY（glm-4-flash 免费模型）")
def test_live_glm_zeri_output_parses():
    """真实冒烟：glm-4-flash 对择日请求的原始输出必须可解析为工具调用。

    探针实测（2026-09-12）4/4 采样可解析（`择日\n{…}`）；本用例最多采样 3 次，
    全不可解析即失败（= D3 已知失败形态回归）。
    """
    from src.bot.capability_registry import build_tool_description
    from src.llm.client import GLM_DEFAULT_MODEL, glm_openai_completion

    raws = []
    for _ in range(3):
        raw = glm_openai_completion(
            os.environ["ZHIPU_API_KEY"],
            [{"role": "system", "content": _LIVE_PROMPT + build_tool_description()},
             {"role": "user", "content": _LIVE_REQ}],
            model=GLM_DEFAULT_MODEL, max_tokens=200, temperature=0.7, timeout=45.0)
        raws.append(raw)
        if parse_tool_calls(raw):
            assert parse_tool_calls(raw)[0].name == "择日"
            return
    pytest.fail(f"3 次采样均不可解析（D3 已知失败形态）：{raws!r}")
