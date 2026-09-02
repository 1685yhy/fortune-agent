# -*- coding: utf-8 -*-
"""R2-3：LuckyCalendar.daily() LLM 直调 → 统一路由层（src/llm/client）回归测试。

根因：calendar.py 原 httpx 直连硬编码 deepseek 端点，绕过统一 LLM 路由层
（评测装配 l1_eval.py 只 patch src.llm.client 模块级 deepseek_anthropic_completion
到免费 glm-4-flash，且 deepseek 槽为占位 key）→ 评测 401 → 静默降级规则模板。

覆盖（验证体系 §1）：
- mock 统一层函数返回固定 JSON → daily() 产出含 LLM 个性化内容（宜忌+fortune4
  来自 mock 值），且调用参数与旧直调同值（model/max_tokens/temperature/
  messages 结构）；
- mock 抛异常（含 client 层空文本语义 ValueError）→ 走 _fallback_calendar 兜底
  （降级路径不回归）；
- mock 返回不可解析文本 → 兜底；
- LLM 内容含 emoji → 日历侧幂等 strip 兜底生效；
- fortune4 缺失/非法 → _sanitize_fortune4 规则兜底（yi/ji 仍用 LLM 值）；
- 同值性静态护栏：client 层 ANTHROPIC_MESSAGES_URL 常量 == 旧硬编码 URL、
  _anthropic_model_name("deepseek-v4-flash") == "deepseek-v4-flash[1m]"；
- 结构护栏：calendar 模块不得有顶层 deepseek_anthropic_completion 属性
  （顶层 from-import 会绑定 patch 前函数对象，评测 patch 不生效 → 复发）。
"""
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import src.llm.client as llm_client  # noqa: E402
import src.engines.calendar as calendar_mod  # noqa: E402

OLD_URL = "https://api.deepseek.com/anthropic/v1/messages"
OLD_MODEL = "deepseek-v4-flash[1m]"

USER_BAZI = {
    "bazi": ["庚午", "辛巳", "乙酉", "癸未"],
    "day_master": "乙木",
    "wuxing": {"金": 3, "木": 1, "水": 1, "火": 2, "土": 1},
    "dayun": "6岁壬午 → 16岁癸未",
}
DATE = "2026-09-02"

MOCK_JSON = (
    '{"overall_mood": "今日气韵上扬",'
    '"yi": [{"action": "约见贵人", "time": "巳时9-11点", "reason": "印星生身"}],'
    '"ji": [{"action": "冲动投资", "time": "全天", "reason": "财星受克"}],'
    '"lucky_color": "青色", "lucky_direction": "东", "lucky_number": 3,'
    '"is_special": false, "special_note": "",'
    '"fortune4": {'
    '"career": {"score": 7.5, "desc": "事业上升期，宜主动出击。"},'
    '"wealth": {"score": 6.2, "desc": "正财平稳，偏财谨慎。"},'
    '"love": {"score": 8.0, "desc": "感情升温，适合约会。"},'
    '"health": {"score": 7.0, "desc": "精力充沛，注意休息。"}}}'
)


class _Capture:
    """记录 deepseek_anthropic_completion 调用参数并回放固定 JSON。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, api_key, messages, model="deepseek-v4-flash",
                 max_tokens=1000, temperature=0.7, timeout=60.0, **kw):
        self.calls.append({
            "api_key": api_key, "messages": messages, "model": model,
            "max_tokens": max_tokens, "temperature": temperature,
            "timeout": timeout,
        })
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def cal():
    return calendar_mod.LuckyCalendar(api_key="test-key-no-network")


def _patch_client(monkeypatch, fake):
    # 与评测装配同构：patch src.llm.client 模块属性（函数内 import 调用期解析 → 生效）
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)


# ── 1. LLM 生效路径：mock 固定 JSON → 个性化内容入 CalendarDay ────────────

def test_daily_mock_json_uses_llm_personalized_content(cal, monkeypatch):
    fake = _Capture(MOCK_JSON)
    _patch_client(monkeypatch, fake)

    day = cal.daily(USER_BAZI, DATE, preferences="性格内向")

    assert fake.calls, "统一层函数必须被调用（不得再 httpx 直连）"
    # 调用参数与旧直调逐项同值（等价性契约）
    call = fake.calls[0]
    assert call["model"] == "deepseek-v4-flash", call  # 映射后 == [1m]，见静态护栏
    assert call["max_tokens"] == 2000
    assert call["temperature"] == 1.0
    assert call["timeout"] == 60.0
    assert call["api_key"] == "test-key-no-network"
    # 消息结构与旧直调等价：单 user 消息，CALENDAR_PROMPT 原样内联
    assert len(call["messages"]) == 1 and call["messages"][0]["role"] == "user"
    content = call["messages"][0]["content"]
    assert "今日流日" in content and "庚午 辛巳 乙酉 癸未" in content
    assert "性格内向" in content  # preferences 透传

    # LLM 个性化内容落地（非规则模板兜底）
    assert day.yi[0]["action"] == "约见贵人"
    assert day.ji[0]["action"] == "冲动投资"
    assert day.lucky_color == "青色"
    assert day.overall_mood == "今日气韵上扬"
    assert day.fortune4["career"]["score"] == 7.5
    assert day.fortune4["love"]["desc"] == "感情升温，适合约会。"


def test_daily_route_constants_match_old_literals():
    """同值性静态护栏：client 层常量/映射产出 == 旧硬编码字面量。"""
    assert llm_client.ANTHROPIC_MESSAGES_URL == OLD_URL
    assert llm_client._anthropic_model_name("deepseek-v4-flash") == OLD_MODEL


def test_calendar_module_has_no_top_level_client_binding():
    """结构护栏：顶层 from-import 会绑定 patch 前函数对象 → 评测 patch 不生效。"""
    assert not hasattr(calendar_mod, "deepseek_anthropic_completion")


# ── 2. 降级路径：异常 → _fallback_calendar（不回归）─────────────────────

def test_daily_client_raises_falls_back(cal, monkeypatch):
    _patch_client(monkeypatch, _Capture(RuntimeError("LLM HTTP 401 auth")))

    day = cal.daily(USER_BAZI, DATE)

    # 规则兜底确定性内容（与 _fallback_calendar 一致）
    assert day.yi and day.yi[0]["action"] == "静心思考"
    assert day.ji and day.ji[0]["action"] == "冲动决策"
    assert day.overall_mood  # 按当日干支五行派生
    assert day.fortune4  # derive_fortune4 规则兜底
    for k in ("career", "wealth", "love", "health"):
        assert k in day.fortune4
        assert 0 <= day.fortune4[k]["score"] <= 10


def test_daily_client_empty_content_semantics_falls_back(cal, monkeypatch):
    """client 层空文本语义（ValueError）→ 与旧实现一致落入兜底。"""
    _patch_client(monkeypatch, _Capture(ValueError("Empty content from LLM")))

    day = cal.daily(USER_BAZI, DATE)

    assert day.yi and day.yi[0]["action"] == "静心思考"
    assert day.fortune4


def test_daily_unparseable_json_falls_back(cal, monkeypatch):
    _patch_client(monkeypatch, _Capture("完全不是 JSON 的文本，哈哈"))

    day = cal.daily(USER_BAZI, DATE)

    assert day.yi and day.yi[0]["action"] == "静心思考"


# ── 3. 后处理保持：emoji 兜底 strip + fortune4 校验兜底 ──────────────────

def test_daily_mock_emoji_stripped(cal, monkeypatch):
    """评测 mock 未经 client 层 strip 时，日历侧幂等 strip 兜底生效。"""
    mock = ('{"overall_mood": "今日气韵上扬🎉",'
            '"yi": [{"action": "约见贵人✨", "time": "巳时", "reason": "印星生身"}],'
            '"ji": [], "lucky_color": "青色", "lucky_direction": "东",'
            '"lucky_number": 3, "is_special": false, "special_note": ""}')
    _patch_client(monkeypatch, _Capture(mock))

    day = cal.daily(USER_BAZI, DATE)

    assert "🎉" not in day.overall_mood
    assert "✨" not in day.yi[0]["action"]
    assert day.yi[0]["action"] == "约见贵人"


def test_daily_mock_invalid_fortune4_rules_fallback_keeps_yi(cal, monkeypatch):
    """LLM 返回 fortune4 非法 → 规则兜底 fortune4；yi/ji 仍用 LLM 值。"""
    mock = MOCK_JSON.replace(
        '"career": {"score": 7.5, "desc": "事业上升期，宜主动出击。"}',
        '"career": {"score": 99.9, "desc": "越界分数"}')
    _patch_client(monkeypatch, _Capture(mock))

    day = cal.daily(USER_BAZI, DATE)

    assert day.yi[0]["action"] == "约见贵人"  # LLM 值保留
    assert 0 <= day.fortune4["career"]["score"] <= 10  # 规则兜底合法化
