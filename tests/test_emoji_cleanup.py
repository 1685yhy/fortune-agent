"""emoji 强收敛补漏验证（v2026-08-17）。

背景：多个引擎绕过统一模型层（src/llm/client.py）直接调 DeepSeek API，
输出未清理导致用户可见回复仍带 emoji。本测试对每个直调点注入含 emoji 的
mock LLM 响应（monkeypatch httpx.post / client 函数），验证最终输出已被
strip_emoji 清理；另附 strip_emoji 单元测试（中文/全角标点不受影响）。

k62：`TestEmotionSoother` / `TestMoodDetector` 两例随死模块（emotion_soother.py
/ mood_detector.py，src/ 下 0 引用）删除 —— 其余 emoji 收敛用例（活功能）
一行未动。

运行：cd 项目根目录 && python3 -m pytest tests/test_emoji_cleanup.py -q

k61 复核（用户红线「测试涉及 LLM 一律用免费 glm-4-flash，不许用 DeepSeek」）：
本文件**全部用例零外呼** —— patch 目标要么是统一层模块函数
（`src.llm.client.deepseek_anthropic_completion`），要么是
`httpx.AsyncClient.post`；其中 `mock.patch.dict("os.environ",
{"DEEPSEEK_API_KEY": "test-key"})` 注入的是**假 key**，只为让统一层
`resolve_llm_api_key()` 非空从而走到被 mock 的调用点，**不产生任何请求**。
k61 探针实测：本文件 0 次出站（对比 test_adaptive_advisor/test_mood_detector
的 27 次）。若将来有人把某个 patch 拆掉，k61 的进程级守卫
（`tests/conftest.py::_k61_deepseek_egress_guard`）会立刻红。
"""
import asyncio
import json
import re
import unittest.mock as mock

import httpx
import pytest

from src.utils.text_clean import strip_emoji

# 与 text_clean 相同覆盖块的独立断言（避免"用被测函数测自己"）
_EMOJI_PATTERN = re.compile(
    "[\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "️‍⃣\uD800-\uDFFF]"
)


def assert_no_emoji(text: str):
    m = _EMOJI_PATTERN.search(text or "")
    assert m is None, f"emoji 残留: {m.group()!r} in {text!r}"


def _httpx_mock(content: str, text_key: str = "content") -> mock.Mock:
    """构造 httpx.post 返回值：choices[0].message.content = content。"""
    resp = mock.Mock()
    resp.json.return_value = {"choices": [{"message": {text_key: content}}]}
    return resp


# ====================================================================
# strip_emoji 单元测试
# ====================================================================

class TestStripEmoji:
    def test_removes_all_emoji_blocks(self):
        assert strip_emoji("你好👋世界🌍") == "你好世界"
        assert strip_emoji("☀️晴天☕咖啡") == "晴天咖啡"
        assert strip_emoji("✂️剪刀✈️飞机✓勾") == "剪刀飞机勾"
        assert strip_emoji("⭕圆⭐星") == "圆星"
        assert strip_emoji("1️⃣2️⃣3️⃣") == "123"

    def test_preserves_chinese_and_fullwidth_punct(self):
        text = "（今日运势：宜出行，忌争执。2027年9月15日，八成把握）abc 123"
        assert strip_emoji(text) == text

    def test_removes_lone_surrogates(self):
        # 流式分片切断代理对的残缺半对（\uD83D 是 emoji 高代理位）
        assert strip_emoji("前半\uD83D后半") == "前半后半"

    def test_zwj_sequences(self):
        assert strip_emoji("👨‍👩‍👧‍👦家庭") == "家庭"

    def test_none_empty(self):
        assert strip_emoji("") == ""
        assert strip_emoji(None) is None


# ====================================================================
# 直调点验证：mock LLM 响应注入 emoji，确认最终输出已清理
# ====================================================================

EMOJI_LLM_TEXT = "你的脸型很好👍✨，气色红润❤️，保持自信😊🚀"


class TestFaceReader:
    def test_generate_report_strips_emoji(self):
        from src.engines.face_reader import FaceMetrics, generate_report
        metrics = FaceMetrics(best_features=["圆润"], improvement_areas=["肤色"])
        # 注意：face_reader 在函数内 `import httpx`，无模块级属性，须 patch 全局 httpx.post
        with mock.patch("httpx.post", return_value=_httpx_mock(EMOJI_LLM_TEXT)) as m:
            report = generate_report(metrics, retriever=None, api_key="test-key")
            m.assert_called_once()
        assert "👍" not in report and "✨" not in report
        assert_no_emoji(report)  # 含模板内 📷✅⚠️📖 一并剔除


class TestPalmReader:
    def test_generate_palm_report_strips_emoji(self):
        from src.engines.palm_reader import PalmMetrics, generate_palm_report
        metrics = PalmMetrics(life_line={"detected": True, "length": "长"},
                              wisdom_line={"detected": True},
                              feeling_line={"detected": False},
                              fate_line={"detected": False},
                              palm_shape="掌方", palm_color="红润",
                              finger_type="修长")
        with mock.patch("httpx.post",
                        return_value=_httpx_mock("掌纹清晰👍，生命力旺盛🌱")) as m:
            report = generate_palm_report(metrics, retriever=None, api_key="test-key")
            m.assert_called_once()
        assert_no_emoji(report)  # 含模板内 ✋✅❌📖 一并剔除


class TestAdvisorV2:
    def _bazi(self):
        from src.engines.bazi import BaziResult
        return BaziResult(
            bazi=["庚午", "辛巳", "乙酉", "甲申"],
            day_master="乙木",
            wuxing={"金": 3, "木": 2, "水": 2, "火": 1, "土": 0},
            shishen=["正官", "七杀", "日主", "劫财"],
            dayun=[(5, "壬午")],
            liunian={"2026": "丙午"},
            geju="正官格",
            yongshen="水木",
            shensha=["天乙贵人"],
            nayin=["路旁土"],
        )

    def test_generate_advice_strips_emoji(self):
        from src.engines.advisor_v2 import AdaptiveAdvisor
        llm_json = json.dumps({
            "actions": [{"category": "事业", "advice": "抓住机遇🎯",
                         "timing": "2027年9月15日-10月15日", "confidence": "high"}],
            "serendipity": "顺便说一句：注意健康💪",
            "daily_tip": "宜静心🍵",
            "style_notes": "命格独特✨",
        }, ensure_ascii=False)
        with mock.patch("src.llm.client.deepseek_anthropic_completion",
                        return_value=llm_json) as m:
            result = AdaptiveAdvisor().generate(self._bazi(), user_context="想问事业", api_key="test-key")
            m.assert_called_once()
        assert_no_emoji(result["serendipity"])
        assert_no_emoji(result["daily_tip"])
        assert_no_emoji(result["style_notes"])
        for a in result["actions"]:
            assert_no_emoji(a["advice"])
            assert_no_emoji(a["timing"])


class TestIntentClassifier:
    def test_emoji_in_value_does_not_break_parse(self):
        from src.engines.intent_classifier import IntentClassifier
        # emoji 混入 intent 值内：未清理会判 invalid → None；清理后应为 "bazi"
        content = '{"intent": "bazi😀", "confidence": 0.9}'
        with mock.patch("src.engines.intent_classifier.httpx.post",
                        return_value=_httpx_mock(content)) as m:
            intent = IntentClassifier(api_key="test-key").classify("帮我看看我的八字")
            m.assert_called_once()
        assert intent == "bazi"


class TestCalendar:
    def test_daily_fields_stripped(self):
        from src.engines.calendar import LuckyCalendar
        llm_text = json.dumps({
            "overall_mood": "今日大吉🎉",
            "yi": [{"action": "出行🚗", "time": "巳时9-11点", "reason": "顺"}],
            "ji": [{"action": "熬夜❌", "time": "子时", "reason": "耗神"}],
            "lucky_color": "红色🔴",
            "lucky_direction": "东南",
            "lucky_number": "7",
            "is_special": True,
            "special_note": "冲煞日⚠️，宜静不宜动",
            "fortune4": {"career": {"score": 7.2, "desc": "事业顺利😊"},
                         "wealth": {"score": 6.8, "desc": "财运好💰"},
                         "love": {"score": 5.5, "desc": "感情稳"},
                         "health": {"score": 7.0, "desc": "健康佳🌿"}},
        }, ensure_ascii=False)
        # R2-3：calendar 直调已改走统一路由（src.llm.client.deepseek_anthropic_completion，
        # 函数内 import 调用期解析模块属性）——mock 目标随之切换；mock 返回未经 client
        # 层 strip 的文本，验证日历侧幂等 strip 兜底仍生效。
        with mock.patch("src.llm.client.deepseek_anthropic_completion",
                        return_value=llm_text) as m:
            day = LuckyCalendar(api_key="test-key").daily(
                {"bazi": ["庚午", "辛巳", "乙酉", "甲申"], "day_master": "乙木",
                 "wuxing": {"金": 3}, "current_dayun": "壬午"},
                date_str="2026-08-17")
            m.assert_called_once()
        assert_no_emoji(day.overall_mood)
        assert_no_emoji(day.special_note)
        assert_no_emoji(day.lucky_color)
        for item in day.yi + day.ji:
            assert_no_emoji(item.get("action", ""))
            assert_no_emoji(item.get("reason", ""))
        for dim, info in day.fortune4.items():
            assert_no_emoji(info["desc"])


class TestJianQuote:
    # k33/A11：调用点由 httpx 直连改为统一 LLM 层（src.llm.client），
    # patch 目标同步改为模块级函数（顶层 httpx 属性已不存在）。
    def test_llm_verify_answer_stripped(self):
        from src.engines import jian_quote
        import src.llm.client as llm_client
        quote = "天行健，君子以自强不息"
        with mock.patch.object(llm_client, "deepseek_anthropic_completion",
                               return_value="是🙏") as m, \
             mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            assert jian_quote._llm_verify(quote, "周易", "甲子") == quote
            m.assert_called_once()
        # "否" 判定不受 emoji 影响
        with mock.patch.object(llm_client, "deepseek_anthropic_completion",
                               return_value="否❌"), \
             mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            assert jian_quote._llm_verify(quote, "周易", "甲子") == ""


class TestQueryEnhancer:
    def test_enhance_rewritten_stripped(self):
        from src.rag.query_enhancer import QueryEnhancer
        content = json.dumps({
            "rewritten": "近期运势低迷，询问流年气运变化🚀",
            "sub_queries": ["流年气运✨"],
            "category": "bazi",
            "keywords": ["运势🔮"],
        }, ensure_ascii=False)
        resp = mock.Mock()
        resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        with mock.patch.object(httpx.AsyncClient, "post",
                               return_value=resp) as m:
            enh = QueryEnhancer(api_key="test-key")
            try:
                result = asyncio.run(enh.enhance("我最近运气不好"))
            finally:
                asyncio.run(enh.close())
            m.assert_called_once()
        assert_no_emoji(result.rewritten)
        for sq in result.sub_queries:
            assert_no_emoji(sq)
        for kw in result.keywords:
            assert_no_emoji(kw)
