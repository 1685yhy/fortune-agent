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

    def test_bare_quoted_payload(self):
        """「搜索\\n"关键词"」——载荷为裸引号串（无大括号）。"""
        text = '搜索\n"2026年新能源汽车行业前景分析"'
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["搜索"]
        assert calls[0].params_obj == {"text": "2026年新能源汽车行业前景分析"}
        assert strip_tool_calls(text).strip() == ""

    def test_zeri_probe_raw_output_parses(self):
        """探针实录原文（4/4 采样形态）→ 择日工具可解析（D3 失败链路）。"""
        text = '择日\n{"场景": "搬家", "时间范围": "2026年9月15日"}'
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["择日"]
        assert calls[0].params_obj["场景"] == "搬家"

    def test_english_cap_id_in_brackets(self):
        assert [c.name for c in parse_tool_calls(
            '【zeri】\n{"scene": "开业"}')] == ["择日"]

    # ── 形态 6：散文参数（探针实录最常形态，D3 zeri 失败直接形态）──────

    def test_prose_param_form_zeri(self):
        """「择日\\n搬家,2026年9月15日」——工具名独占一行 + 自然语言参数行。

        探针实录（2026-09-12）对「2026年9月15日搬家 帮我选个日子」3/3 采样
        为此形态（D3 已知失败根因：旧解析只认 {JSON} 载荷 → 工具不执行）。
        """
        text = "择日\n搬家,2026年9月15日"
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] == ["择日"]
        assert calls[0].params_obj == {"text": "搬家,2026年9月15日"}
        # 载荷绝不落可见文本
        assert strip_tool_calls(text).strip() == ""

    def test_prose_param_form_keeps_leading_prose(self):
        """块前的模型开场白保留给用户（只切工具名 + 参数行）。"""
        text = "好的，我先看看。\n\n择日\n开业,2026年8月20日"
        assert [c.name for c in parse_tool_calls(text)] == ["择日"]
        assert strip_tool_calls(text).strip() == "好的，我先看看。"

    def test_prose_param_form_iso_date(self):
        calls = parse_tool_calls("择日\n搬家, 2026-09-15\n")
        assert [(c.name, c.params_obj) for c in calls] == \
            [("择日", {"text": "搬家, 2026-09-15"})]

    @pytest.mark.parametrize("text", [
        # 参数行带句末标点 = 散文句，不触发
        "好的，我给你说说。\n\n搜索\n就是你输入想查的东西。",
        # 工具名不在块起点（正文段落最后一行恰是工具名）→ 不触发
        "正文段落\n择日\n搬家,2026年9月15日",
        # 名字与散文同行粘连（非独占一行）→ 不触发
        "我说的择日\n搬家",
    ])
    def test_prose_param_form_no_false_positive(self, text):
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
