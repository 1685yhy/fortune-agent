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


class TestProseParamProductionForm:
    """形态 D（真实生产形态）：裸名独占一行 + **末行**散文参数。

    k33 复审回归修复：真实 glm-4-flash 冒烟（`test_live_glm_zeri_output_parses`
    的原始输出）**5/5 采样**均为该形态（`择日\n搬家,2026年9月15日` 系）——
    I1 一度整体取消形态 D → 真实冒烟红、D3 zeri 场景继续失败。
    复审裁定「生产正确性优先」：以硬护栏放行（名字精确命中注册表 + 参数可解析 +
    块在消息末尾 + 结构唯一），不再靠"形态取消"。
    """

    @pytest.mark.parametrize("text,params", [
        # 真实冒烟实录（复审 3 次跑 + 探针 5 采样实测原文，逐字）
        ("择日\n搬家,2026年9月15日", "搬家,2026年9月15日"),
        ("择日\n搬家, 2026-09-15", "搬家, 2026-09-15"),
        ("择日\n搬家,2026-09-15", "搬家,2026-09-15"),
        # 尾部换行/空白不影响
        ("择日\n搬家,2026年9月15日\n", "搬家,2026年9月15日"),
    ])
    def test_live_observed_forms_parse(self, text, params):
        calls = parse_tool_calls(text)
        assert [(c.name, c.params_obj) for c in calls] == \
            [("择日", {"text": params})], f"生产形态必须可解析：{text!r}"
        # 载荷绝不落可见文本（工具块整体剥净）
        assert strip_tool_calls(text).strip() == ""

    def test_opener_prefix_kept_for_user(self):
        """块前的模型开场白保留给用户（只切「名字 + 参数行」）。"""
        text = "好的，我先看看。\n\n择日\n开业,2026年8月20日"
        assert [c.name for c in parse_tool_calls(text)] == ["择日"]
        assert strip_tool_calls(text).strip() == "好的，我先看看。"

    @pytest.mark.parametrize("text", [
        # 名字非注册工具（行首锚定 + 精确命中，不做后缀匹配）
        "天气\n2026年9月15日",
        "帮我看看\n搬家,2026年9月15日",
        # 【】包裹 + 散文参数：括号形态只配 JSON 载荷（形态 A/B）
        "【择日】\n搬家,2026年9月15日",
        # 参数带句末标点 = 散文句
        "择日\n搬家,2026年9月15日。",
        # 参数行不是消息末行（其后还有正文）
        "择日\n搬家,2026年9月15日\n以上，请确认。",
        "搜索\n关键词: 黄金价格\n以上。",
        "排盘\n姓名,1990年3月5日\n性别,男",
        # 参数为引号残块（JSON 残块由既有 A1 裸格式层负责，不在此列）
        '择日\n"搬家"',
        # 参数纯标点噪声（不可解析）
        "择日\n。。。",
        # 名字不在块起点（正文行首是别的字，名字在行中）
        "建议你用择日\n搬家,2026年9月15日",
    ])
    def test_prose_form_guardrails_block(self, text):
        """形态 D 的硬护栏：任一不满足 → 不解析、不剥字。"""
        assert parse_tool_calls(text) == [], f"护栏失效：{text!r}"
        assert strip_tool_calls(text) == text.strip()

    def test_only_one_block_at_message_end(self):
        """形态 D 结构唯一（只有一行能是"最后一行"）→ 不存在多块吞正文。"""
        text = "择日\n搬家,2026年9月15日\n搜索\n黄金价格"
        calls = parse_tool_calls(text)
        assert [c.name for c in calls] != ["择日", "搜索"]


class TestReleasedTradeoff:
    """复审裁定的**放行面**（前后对照矩阵里"被放行"的那一行，非静默回归）。

    `排盘\n姓名,1990年3月5日`（审查 I1 反例 d）与真实生产形态
    `择日\n搬家,2026年9月15日` **逐字节同构**（工具名独占行 + 逗号短语末行）：
    任何能拦前者而放行后者的规则都只能靠"标签词黑名单"这类语义猜测，
    会在真实流量换个工具/换个措辞时翻车。复审裁定：生产正确性优先
    （A3 的目的就是让真实 GLM 工具形态能执行），故本形态**放行**，
    同时用四条硬护栏把误判面压到最小（见 TestProseParamProductionForm）。
    """

    def test_structurally_identical_counterexample_released(self):
        text = "排盘\n姓名,1990年3月5日"
        assert [c.name for c in parse_tool_calls(text)] == ["排盘"]
        assert strip_tool_calls(text).strip() == ""

    def test_label_value_form_released(self):
        """同族放行：`搜索\\n关键词: 黄金价格`（末行、无后续正文）与生产形态同构。

        「词条: 值」这一行本身**不是**判别信号——真实参数行同样可能带冒号
        （如 `择日\\n场景: 搬家,2026年9月15日`），用冒号拦会误伤生产流量。
        """
        text = "搜索\n关键词: 黄金价格"
        assert [c.name for c in parse_tool_calls(text)] == ["搜索"]


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
        # ④ 表单式正文行（旧版 → ['搜索']/['排盘']）——其中「名字独占行 + 单行短句」
        #    与真实生产形态同构者由 TestReleasedTradeoff 显式放行；本组锁"仍拦"的面：
        #    参数行带后续正文（非消息末行）→ 拦（复审护栏）
        '搜索\n关键词: 黄金价格\n以上。',
        '排盘\n姓名,1990年3月5日\n性别,男',
        # 名字与参数同行粘连（非"名字独占一行"）→ 拦
        '择日 搬家,2026年9月15日',
    ])
    def test_prose_param_line_not_tool_call(self, text):
        """形态 D 之外/护栏不满足的散文行仍不触发（复审后仍拦的面）。"""
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
# 基础设施噪音特征（全量并发跑时免费模型会被限流/网关报错）：只有**整条回复
# 就是一句错误话术**（短文本命中特征）才算「不可用样本」，不参与判定。
_GLM_NOISE_MARKERS = (
    "请求过于频繁", "过于频繁", "系统繁忙", "服务繁忙", "请稍后重试", "稍后重试",
    "rate limit", "Rate limit", "too many requests", "Too Many Requests",
    "429", "timeout", "Timeout", "timed out", "超时", "网络异常", "服务异常",
    "服务不可用", "上游错误",
)
_GLM_NOISE_MAX_LEN = 60


def _glm_sample_is_noise(raw) -> bool:
    """采样是否为「不可用样本」（空 / 短错误话术）→ 不参与可解析性判定。"""
    if not isinstance(raw, str):
        return True
    text = raw.strip()
    if not text:
        return True
    return (len(text) <= _GLM_NOISE_MAX_LEN
            and any(m in text for m in _GLM_NOISE_MARKERS))


@pytest.mark.skipif(not os.environ.get("ZHIPU_API_KEY"),
                    reason="需要 ZHIPU_API_KEY（glm-4-flash 免费模型）")
def test_live_glm_zeri_output_parses():
    """真实冒烟：glm-4-flash 对择日请求的原始输出必须可解析为工具调用。

    稳健性（全量并发跑场景）：采样抛异常 / 空响应 / 短错误话术（限流特征）
    一律视为**不可用样本**，不参与判定；只有**拿到真实模型内容却解析不了**
    才失败；若全部样本不可用 → skip（「GLM 不可用」，不是形态回归）。
    判别力不变：真实内容解析失败必红（形态 D 回归时无法用 skip 掩盖）。
    """
    from src.bot.capability_registry import build_tool_description
    from src.llm.client import GLM_DEFAULT_MODEL, glm_openai_completion

    raws, usable = [], 0
    for _ in range(4):  # 最多 4 次采样（噪音样本可被后续采样替换）
        try:
            raw = glm_openai_completion(
                os.environ["ZHIPU_API_KEY"],
                [{"role": "system",
                  "content": _LIVE_PROMPT + build_tool_description()},
                 {"role": "user", "content": _LIVE_REQ}],
                model=GLM_DEFAULT_MODEL, max_tokens=200, temperature=0.7,
                timeout=45.0)
        except Exception as e:  # 限流 / 网络 / 上游错误 → 不可用样本
            raws.append(f"<error: {type(e).__name__}: {str(e)[:120]}>")
            continue
        raws.append(raw)
        if _glm_sample_is_noise(raw):
            continue
        usable += 1
        calls = parse_tool_calls(raw)
        if calls:
            assert calls[0].name == "择日", f"解析出的工具名不是择日：{raw!r}"
            return
    if not usable:
        pytest.skip(f"GLM 不可用（限流/网络），{len(raws)} 次采样均无真实内容：{raws!r}")
    pytest.fail(f"真实模型内容不可解析（D3 已知失败形态回归）：{raws!r}")


class TestLiveSmokeRobustness:
    """live 冒烟的稳健性契约（离线驱动，零网络）：

    - 限流/超时/空响应/短错误话术 = 不可用样本 → 不判失败（全不可用则 skip）；
    - **真实模型内容却解析不了 → 必须失败**（判别力不得退化成恒真）。
    """

    @staticmethod
    def _fake_client(monkeypatch, samples):
        """把 glm_openai_completion 换成按序返回/抛出的假实现（最后一项可复用）。"""
        import src.llm.client as client
        monkeypatch.setenv("ZHIPU_API_KEY", "test-key")  # 免 skipif/KeyError 干扰
        seq = list(samples)

        def _fake(*_a, **_k):
            item = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(item, BaseException):
                raise item
            return item

        monkeypatch.setattr(client, "glm_openai_completion", _fake)

    @pytest.mark.parametrize("noise", [
        TimeoutError("read timed out"),
        RuntimeError("GLM HTTP 429: 请求过于频繁"),
        "",
        "   请求过于频繁，请稍后重试   ",
    ])
    def test_all_noise_samples_skip_not_fail(self, monkeypatch, noise):
        self._fake_client(monkeypatch, [noise])
        with pytest.raises(pytest.skip.Exception):
            test_live_glm_zeri_output_parses()

    def test_noise_then_real_form_still_passes(self, monkeypatch):
        self._fake_client(monkeypatch, [
            RuntimeError("GLM HTTP 429: rate limit"),
            "择日\n搬家,2026年9月15日",
        ])
        test_live_glm_zeri_output_parses()  # 不抛 → 通过

    @pytest.mark.parametrize("garbage", [
        "今天天气不错，适合出门走走。",           # 真实内容但无工具形态
        "我可以帮你看看，你是想搬家吗？",         # 澄清话术（无工具调用）
    ])
    def test_real_content_unparseable_still_fails(self, monkeypatch, garbage):
        """判别力契约：真实内容解析不了 → fail（不得被 skip 掩盖）。"""
        self._fake_client(monkeypatch, [garbage])
        with pytest.raises(pytest.fail.Exception):
            test_live_glm_zeri_output_parses()

    def test_noise_classifier_keeps_long_real_text(self):
        """长真实回复里出现"超时/服务"等词不得误判为噪音。"""
        long_reply = ("关于你的择日问题，我先说明一下：系统在高峰期可能会有超时"
                      "或者服务繁忙的提示，但这不影响结论。下面说一下搬家吉日"
                      "的挑选方法，2026年9月适合搬家的日子有…")
        assert len(long_reply) > 60
        assert not _glm_sample_is_noise(long_reply)
