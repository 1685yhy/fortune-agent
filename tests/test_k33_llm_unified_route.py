# -*- coding: utf-8 -*-
"""k33/A11：三引擎直读 key / 直连端点 → 统一 LLM 层（src/llm/client.py）。

根因（R2-3 对 calendar 的同款）：评测/装配只 patch
`src.llm.client.deepseek_anthropic_completion`；引擎若
  ① 直读 os.getenv("DEEPSEEK_API_KEY")（自建第二套 key 解析），且/或
  ② httpx 直连硬编码端点（或顶层 from-import 绑定 patch 前函数对象），
则评测路由（glm-4-flash 降级链）与降级注入对本引擎永不生效。

覆盖：
- jian_quote._llm_verify：patch 统一层 → 命中（含调用参数契约）；mock 抛异常
  → 宽容通过（金句不丢）；无 key → 不调 LLM；
- night_soliloquy.build_soliloquy：patch 统一层 → 命中；抛异常 → 兜底灯语；
- zeri_checklist.customize_checklist：patch 统一层 → 命中并合并；抛异常 →
  模板原样；无 key → 不调 LLM；
- 结构护栏：三引擎模块不得有顶层 deepseek_anthropic_completion 绑定；
- key 解析单一事实源 resolve_llm_api_key（DEEPSEEK > ANTHROPIC，strip）。

运行：OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest \
      tests/test_k33_llm_unified_route.py -q
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import src.llm.client as llm_client  # noqa: E402


class _Capture:
    """记录统一层调用参数并回放结果（与评测装配同构）。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, api_key, messages, model="deepseek-flash",
                 max_tokens=1000, temperature=0.7, timeout=60.0, **kw):
        self.calls.append({
            "api_key": api_key, "messages": messages, "model": model,
            "max_tokens": max_tokens, "temperature": temperature,
            "timeout": timeout,
        })
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """默认无 key（各用例自行注入），避免宿主 .env 干扰。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


# ══════════════════════════════════════════════════════════════
# 0) key 解析单一事实源
# ══════════════════════════════════════════════════════════════

class TestResolveKey:
    def test_deepseek_preferred(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
        assert llm_client.resolve_llm_api_key() == "ds-key"

    def test_anthropic_fallback_and_strip(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  an-key  ")
        assert llm_client.resolve_llm_api_key() == "an-key"

    def test_anthropic_fallback_opt_out(self, monkeypatch):
        """零行为差异：jian_quote/night 迁移前不认 ANTHROPIC → 显式关闭回退。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
        assert llm_client.resolve_llm_api_key(allow_anthropic_fallback=False) == ""

    def test_deepseek_still_read_when_fallback_off(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
        assert llm_client.resolve_llm_api_key(allow_anthropic_fallback=False) == "ds-key"

    def test_empty(self):
        assert llm_client.resolve_llm_api_key() == ""

    def test_anthropic_only_env_skips_jian_quote_llm(self, monkeypatch):
        """只有 ANTHROPIC_API_KEY 时，jian_quote 不得外呼（迁移前语义）。"""
        from src.engines import jian_quote
        fake = _Capture("否")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)
        quote = "天行健，君子以自强不息"
        assert jian_quote._llm_verify(quote, "周易", "甲子") == quote
        assert fake.calls == []


# ══════════════════════════════════════════════════════════════
# 1) jian_quote._llm_verify
# ══════════════════════════════════════════════════════════════

class TestJianQuoteRoute:
    QUOTE, BOOK, GANZHI = "天行健，君子以自强不息", "周易", "甲子"

    def test_patch_takes_effect_and_params_contract(self, monkeypatch):
        """patch 统一层 → 命中；参数与旧直调逐项同值（模型/端点见模块注释）。"""
        from src.engines import jian_quote
        fake = _Capture("是")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)

        assert jian_quote._llm_verify(self.QUOTE, self.BOOK, self.GANZHI) == self.QUOTE

        assert fake.calls, "统一层函数必须被调用（不得再 httpx 直连）"
        call = fake.calls[0]
        assert call["api_key"] == "k1"
        assert call["max_tokens"] == 10
        assert call["temperature"] == 0.0
        assert call["timeout"] == 15.0
        assert len(call["messages"]) == 1
        assert call["messages"][0]["role"] == "user"
        assert self.GANZHI in call["messages"][0]["content"]
        assert self.QUOTE in call["messages"][0]["content"]

    def test_no_answer_returns_empty(self, monkeypatch):
        from src.engines import jian_quote
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _Capture("否"))
        assert jian_quote._llm_verify(self.QUOTE, self.BOOK, self.GANZHI) == ""

    def test_exception_fail_open(self, monkeypatch):
        """统一层异常（含 client 层空文本 ValueError）→ 宽容通过（金句不丢）。"""
        from src.engines import jian_quote
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _Capture(RuntimeError("HTTP 401")))
        assert jian_quote._llm_verify(self.QUOTE, self.BOOK, self.GANZHI) == self.QUOTE

    def test_no_key_skips_llm(self, monkeypatch):
        from src.engines import jian_quote
        fake = _Capture("否")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)
        assert jian_quote._llm_verify(self.QUOTE, self.BOOK, self.GANZHI) == self.QUOTE
        assert fake.calls == [], "无 key 不得外呼（与旧实现同语义）"

    def test_no_top_level_httpx_direct_call(self):
        """结构护栏：模块不得再有 httpx 直连点（回归防复发）。"""
        from src.engines import jian_quote
        assert not hasattr(jian_quote, "httpx")
        assert not hasattr(jian_quote, "deepseek_anthropic_completion")


# ══════════════════════════════════════════════════════════════
# 2) night_soliloquy.build_soliloquy
# ══════════════════════════════════════════════════════════════

_SOLILOQUY = (
    "灯还亮着。今天你说到面试的事，我都听进去了。"
    "路是一步一步走出来的，不必急着把明天的答案都定好，"
    "把该准备的准备好，剩下的交给时间，你会走得更稳。"
    "白天那些让你反复琢磨的话，放到夜里就轻一些了。"
    "夜深了，早点休息，明天醒来又是新的一程。晚安。灯下的人"
)
assert 100 <= len(_SOLILOQUY) <= 260, len(_SOLILOQUY)  # 夹具自检（校验红线）


def _bj_today(now=None) -> str:
    """北京「当日」——被测管线的基准日（has_today_chat 对 created_at 做 +8 换算）。

    夹具必须与产品同一时钟推导，不得写死日期：写死会随真实时钟腐化
    （跨过该日 00:00 后 in-suite / 单跑一律变红，反而掩盖真实回归）。
    """
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(hours=8)).strftime("%Y-%m-%d")


class _FakeSessionDAO:
    """够用的会话语义：当日有对话 + 摘要含锚点。

    「此刻」在构造时固定一次，get_history 的 created_at 与 .today 同源
    （UTC 时间戳 ↔ 其北京日期），故跑在零点前后也不会自相矛盾。
    """

    def __init__(self, summary="用户正在准备面试，聊了工作和加班", now=None):
        self.summary = summary
        self.now = now or datetime.now(timezone.utc)  # UTC（产品契约口径）
        self.today = _bj_today(self.now)              # 该时刻的北京当日

    def get_history(self, user_id, limit=50):
        # created_at 为 UTC；+8 换算后即落在 self.today 北京当日
        ca = self.now.strftime("%Y-%m-%d %H:%M:%S")
        return [{"role": "user", "content": "聊了面试", "created_at": ca}]

    def get_summary(self, user_id):
        return {"summary": self.summary}


class TestNightSoliloquyRoute:
    @pytest.fixture(autouse=True)
    def _stub_heavy_paths(self, monkeypatch):
        """隔离重路径：_tomorrow_content（日历/检索）与兜底文案（古籍金句 RAG）
        与 A11 的路由断言无关，且在本机环境不可用（缺向量库）。"""
        from src.engines import night_soliloquy as ns
        monkeypatch.setattr(ns, "_tomorrow_content", lambda *a, **k: {
            "day_ganzhi": "甲子", "suitable": ["出行"], "unsuitable": ["熬夜"]})
        monkeypatch.setattr(ns, "_fallback_soliloquy",
                            lambda *a, **k: "灯还亮着。兜底灯语。灯下的人")

    def test_patch_takes_effect(self, monkeypatch):
        from src.engines import night_soliloquy as ns
        fake = _Capture(_SOLILOQUY)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k2")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)

        dao = _FakeSessionDAO()
        out = ns.build_soliloquy("u1", dao.today, dao)

        assert fake.calls, "统一层函数必须被调用（patch/降级注入生效）"
        assert out["fallback"] is False
        assert out["text"] == _SOLILOQUY
        assert out["anchors"]  # 锚点来自摘要
        assert fake.calls[0]["api_key"] == "k2"

    def test_exception_falls_back(self, monkeypatch):
        from src.engines import night_soliloquy as ns
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k2")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _Capture(RuntimeError("boom")))
        dao = _FakeSessionDAO()
        out = ns.build_soliloquy("u2", dao.today, dao)
        assert out["fallback"] is True
        assert out["text"]  # 兜底灯语（绝不空）

    def test_no_key_falls_back_without_call(self, monkeypatch):
        from src.engines import night_soliloquy as ns
        fake = _Capture(_SOLILOQUY)
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)
        dao = _FakeSessionDAO()
        out = ns.build_soliloquy("u3", dao.today, dao)
        assert fake.calls == []
        assert out["fallback"] is True

    def test_no_top_level_client_binding(self):
        from src.engines import night_soliloquy as ns
        assert not hasattr(ns, "deepseek_anthropic_completion")


# ══════════════════════════════════════════════════════════════
# 3) zeri_checklist.customize_checklist
# ══════════════════════════════════════════════════════════════

class TestZeriChecklistRoute:
    def _tpl(self):
        from src.engines.zeri_checklist import CHECKLIST_TEMPLATES
        return CHECKLIST_TEMPLATES["搬家"]

    def test_patch_takes_effect(self, monkeypatch):
        """顶层 from-import 修复后：patch 统一层 → 命中并合并 LLM 条目。"""
        from src.engines import zeri_checklist as zc
        fake = _Capture('[{"stage": "提前1天", "text": "把冰箱清空并提前解冻"}]')
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k3")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)

        got = zc.customize_checklist("搬家", self._tpl(),
                                     context_text="用户说家里人多，注意老人小孩")

        assert fake.calls, "统一层函数必须被调用（patch/降级注入生效）"
        assert fake.calls[0]["api_key"] == "k3"
        assert fake.calls[0]["model"] == "deepseek-flash"
        assert any("把冰箱清空并提前解冻" in it["text"] for it in got)

    def test_exception_falls_back_to_template(self, monkeypatch):
        from src.engines import zeri_checklist as zc
        tpl = self._tpl()
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k3")
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                            _Capture(RuntimeError("boom")))
        got = zc.customize_checklist("搬家", tpl)
        assert [(i["stage"], i["text"]) for i in got] == \
            [(i["stage"], i["text"]) for i in tpl]

    def test_no_key_returns_template_without_call(self, monkeypatch):
        from src.engines import zeri_checklist as zc
        tpl = self._tpl()
        fake = _Capture('[{"stage": "提前1天", "text": "x"}]')
        monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake)
        got = zc.customize_checklist("搬家", tpl)
        assert fake.calls == []
        assert len(got) == len(tpl)

    def test_no_top_level_client_binding(self):
        from src.engines import zeri_checklist as zc
        assert not hasattr(zc, "deepseek_anthropic_completion")
