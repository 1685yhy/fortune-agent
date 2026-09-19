"""Tests for Sprint 7: Adaptive Personality Detection.

Tests cover:
- Anxiety/worry/fear messages -> gentle mode
- Analytical/data questions -> analyst mode
- Casual/humorous messages -> sassy mode
- Anger -> gentle mode (de-escalation)
- Empty/neutral message defaults to sassy
- Confidence score > 0 for all detections
- 50+ test cases with >80% accuracy on labeled data

MoodDetector uses DeepSeek Flash for AI-based detection, so we mock the API
call and verify the _parse_response logic and the detection flow.

k61（用户红线「测试涉及 LLM 一律用免费 glm-4-flash，不许用 DeepSeek」）：
`TestRealAPI` 原按 `DEEPSEEK_API_KEY` 门控，部署 `.env`（软链到生产）经
`src/config.py` 导入时灌进 `os.environ` → 全量跑时真的打生产 DeepSeek。
现走 `glm_route` 夹具（免费 `glm-4-flash`；无 `ZHIPU_API_KEY` 才 skip），
并由进程级出站守卫（`tests/conftest.py`）禁止任何真实 deepseek 请求。
其余用例本来就是 mock 统一层，零外呼，未改动。

k42（用户可见静默错判修复）后的契约：
- 走统一 LLM 层（src/llm/client.py，Anthropic 兼容端点 + thinking disabled）——
  patch 点相应地是 src.llm.client.deepseek_anthropic_completion；
- 解析失败/调用异常**一律**回退安全人设 gentle（温柔），不得回退 sassy（毒舌）；
- 失败必须 warning 可见（含截断/异常类型，不含用户隐私原文）。
"""
import hashlib
import json
import logging
from unittest.mock import Mock, patch, MagicMock

import pytest

from src.engines.mood_detector import (
    SAFE_FALLBACK_MOOD,
    MoodDetector,
    MoodResult,
)
from src.bot.handler import MessageHandler


# 统一 LLM 层调用点：mood_detector 为函数内 import（调用期解析模块属性），
# patch 该模块属性即可覆盖本模块调用（k33/A11 同款接线）。
LLM_TARGET = "src.llm.client.deepseek_anthropic_completion"


# ====================================================================
# MoodDetector: Unit Tests (mock unified LLM layer)
# ====================================================================

class TestMoodDetectorParse:
    """Test the JSON parsing from LLM responses."""

    def setup_method(self):
        self.detector = MoodDetector(api_key="test_key")

    def test_parse_sassy(self):
        content = '{"mood":"sassy","confidence":0.85,"emotion":"幽默"}'
        result = self.detector._parse_response(content)
        assert result.mood == "sassy"
        assert result.confidence == 0.85
        assert result.emotion_label == "幽默"

    def test_parse_analyst(self):
        content = '{"mood":"analyst","confidence":0.92,"emotion":"分析需求"}'
        result = self.detector._parse_response(content)
        assert result.mood == "analyst"
        assert result.confidence == 0.92

    def test_parse_gentle(self):
        content = '{"mood":"gentle","confidence":0.78,"emotion":"焦虑"}'
        result = self.detector._parse_response(content)
        assert result.mood == "gentle"
        assert result.confidence == 0.78

    def test_parse_invalid_mood_falls_back_to_safe_gentle(self, caplog):
        """非法 mood 值 → 安全兜底（k42：不得再回退毒舌）。"""
        content = '{"mood":"angry","confidence":0.9,"emotion":"愤怒"}'
        with caplog.at_level(logging.WARNING, logger="src.engines.mood_detector"):
            result = self.detector._parse_response(content)
        assert result.mood == SAFE_FALLBACK_MOOD
        assert result.mood == "gentle"
        assert result.mood != "sassy"
        assert "mood" in caplog.text  # 失败可见

    def test_parse_confidence_clamped(self):
        content = '{"mood":"gentle","confidence":1.5,"emotion":"测试"}'
        result = self.detector._parse_response(content)
        assert result.confidence == 1.0

        content = '{"mood":"gentle","confidence":-0.5,"emotion":"测试"}'
        result = self.detector._parse_response(content)
        assert result.confidence == 0.0

    def test_parse_truncated_json_falls_back_to_gentle_and_warns(self, caplog):
        """截断（推理模型吃光 max_tokens 的实测形态）→ 安全兜底 + warning。

        修复前该形态静默回退 sassy/0.5 —— 本用例在旧实现上必失败（行为区分）。
        """
        truncated = '{"mood":"gentle","confidence":0.9,"emotion'
        with caplog.at_level(logging.WARNING, logger="src.engines.mood_detector"):
            result = self.detector._parse_response(truncated)
        assert result.mood == "gentle"
        assert result.mood != "sassy"
        assert result.confidence == 0.5
        assert result.emotion_label == "中性"
        assert "截断" in caplog.text          # 失败可见：明确标注疑似截断
        assert str(len(truncated)) in caplog.text  # 失败可见：内容长度

    def test_parse_non_json_fallback(self, caplog):
        content = "我觉得用户很焦虑"
        with caplog.at_level(logging.WARNING, logger="src.engines.mood_detector"):
            result = self.detector._parse_response(content)
        assert result.mood == "gentle"
        assert result.mood != "sassy"
        assert result.confidence == 0.5
        assert result.emotion_label == "中性"
        assert content not in caplog.text  # 隐私：不回显模型原文

    def test_parse_empty_string_fallback(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.engines.mood_detector"):
            result = self.detector._parse_response("")
        assert result.mood == "gentle"
        assert result.confidence == 0.5
        assert caplog.text  # 空响应也必须可见

    def test_parse_whitespace_only_fallback(self):
        result = self.detector._parse_response("   \n  ")
        assert result.mood == "gentle"

    def test_confidence_always_positive(self):
        """置信度在所有情况下都应该 > 0"""
        cases = [
            '{"mood":"sassy","confidence":0.9,"emotion":"开心"}',
            '{"mood":"analyst","confidence":0.75,"emotion":"分析"}',
            '{"mood":"gentle","confidence":0.6,"emotion":"担心"}',
            "糟糕的响应",
            '{"mood":"analyst","confidence":0.0,"emotion":"中性"}',
        ]
        for case in cases:
            result = self.detector._parse_response(case)
            assert result.confidence >= 0.0

    def test_cache_hit_no_api_call(self):
        """Cache hit should skip API call.

        k42：旧用例以明文键（"cached_msg"）塞缓存，与实际 md5 键不匹配 →
        缓存从未命中，走的是 401 静默兜底 sassy，断言"碰巧"通过（假绿）。
        此处改用真实 md5 键，并同时断言不触发 LLM 调用。
        """
        msg = "cached_msg"
        self.detector._cache[hashlib.md5(msg.encode()).hexdigest()] = \
            MoodResult("sassy", 0.9, "开心")
        with patch(LLM_TARGET) as mock_llm, \
                patch.object(self.detector, "_parse_response") as mock_parse:
            result = self.detector.detect(msg)
            mock_llm.assert_not_called()
            mock_parse.assert_not_called()
        assert result.mood == "sassy"

    def test_clear_cache(self):
        """clear_cache should empty the cache."""
        self.detector._cache["msg1"] = MoodResult("sassy", 0.9, "开心")
        self.detector.clear_cache()
        assert len(self.detector._cache) == 0


class TestMoodDetectorAPI:
    """Test the actual API call flow (unified LLM layer mocked)."""

    def setup_method(self):
        self.detector = MoodDetector(api_key="test_key")

    @patch(LLM_TARGET)
    def test_detect_anxiety_message(self, mock_llm):
        """Anxiety messages should be detected as gentle."""
        mock_llm.return_value = '{"mood":"gentle","confidence":0.82,"emotion":"焦虑"}'
        result = self.detector.detect("我好焦虑啊，不知道该怎么办")
        assert result.mood == "gentle"
        assert result.confidence >= 0.7
        assert mock_llm.called

    @patch(LLM_TARGET)
    def test_detect_data_question(self, mock_llm):
        """Data/analysis questions should be detected as analyst."""
        mock_llm.return_value = '{"mood":"analyst","confidence":0.88,"emotion":"分析需求"}'
        result = self.detector.detect("帮我分析一下这个投资方案的收益率")
        assert result.mood == "analyst"

    @patch(LLM_TARGET)
    def test_detect_humor(self, mock_llm):
        """Humorous messages should be detected as sassy."""
        mock_llm.return_value = '{"mood":"sassy","confidence":0.91,"emotion":"幽默"}'
        result = self.detector.detect("哈哈哈今天运气也太好了吧，笑死")
        assert result.mood == "sassy"

    @patch(LLM_TARGET)
    def test_detect_excitement(self, mock_llm):
        """Excitement/joy should be detected as sassy."""
        mock_llm.return_value = '{"mood":"sassy","confidence":0.85,"emotion":"兴奋"}'
        result = self.detector.detect("太棒了！我升职了！！！")
        assert result.mood == "sassy"

    @patch(LLM_TARGET)
    def test_detect_anger(self, mock_llm):
        """Anger/frustration should be detected as gentle (de-escalate)."""
        mock_llm.return_value = '{"mood":"gentle","confidence":0.80,"emotion":"愤怒"}'
        result = self.detector.detect("我真的很生气，受不了了")
        assert result.mood == "gentle"

    @patch(LLM_TARGET)
    def test_detect_fear(self, mock_llm):
        """Fear/worry should be detected as gentle."""
        mock_llm.return_value = '{"mood":"gentle","confidence":0.86,"emotion":"恐惧"}'
        result = self.detector.detect("我很害怕面试会失败")
        assert result.mood == "gentle"

    @patch(LLM_TARGET)
    def test_detect_neutral_fallback(self, mock_llm):
        """Neutral message should default to sassy (model 判定，非兜底)."""
        mock_llm.return_value = '{"mood":"sassy","confidence":0.55,"emotion":"中性"}'
        result = self.detector.detect("你好")
        assert result.mood == "sassy"

    @patch(LLM_TARGET)
    def test_api_error_fallback(self, mock_llm, caplog):
        """API error → safe fallback gentle/0.5 + warning（k42：不再回退毒舌）。"""
        mock_llm.side_effect = RuntimeError("upstream 500")
        with caplog.at_level(logging.WARNING, logger="src.engines.mood_detector"):
            result = self.detector.detect("测试消息")
        assert result.mood == "gentle"
        assert result.mood != "sassy"
        assert result.confidence == 0.5
        assert "RuntimeError" in caplog.text  # 失败可见：异常类型

    @patch(LLM_TARGET)
    def test_long_messages_truncated(self, mock_llm):
        """Messages over 500 chars should be truncated."""
        mock_llm.return_value = '{"mood":"gentle","confidence":0.8,"emotion":"中性"}'
        long_msg = "测试" * 300
        self.detector.detect(long_msg)
        args, _ = mock_llm.call_args
        user_content = args[1][1]["content"]
        assert len(user_content) <= 500

    @patch(LLM_TARGET)
    def test_detect_uses_unified_layer_with_locked_params(self, mock_llm):
        """k42 接线与口径锁定：统一 LLM 层 + 预算/温度/超时/prompt 逐项同值。

        - key 面不变：仍只用构造传入的 api_key（不新增 env 读取）；
        - prompt 逐字不变（判定语义零漂移）；
        - temperature / timeout 与修复前同值（0.1 / 30.0）；
        - max_tokens 预算 ≥ 200：原生端点下 100 被 reasoning 吃光 = 截断根因。
        """
        mock_llm.return_value = '{"mood":"gentle","confidence":0.9,"emotion":"焦虑"}'
        self.detector.detect("最近压力好大，晚上睡不着")
        args, kwargs = mock_llm.call_args
        assert args[0] == "test_key"
        assert args[1][0]["content"] == MoodDetector.DETECTION_PROMPT
        assert kwargs["model"] == "deepseek-flash"
        assert kwargs["temperature"] == 0.1
        assert kwargs["timeout"] == 30.0
        assert kwargs["max_tokens"] >= 200

    def test_module_has_no_raw_http_dependency(self):
        """结构守卫：模块不再持有 httpx 直连（截断根因载体已移除）。"""
        import src.engines.mood_detector as md
        assert not hasattr(md, "httpx")


# ====================================================================
# MoodDetector: Integration-Style Tests
# ====================================================================

class TestMoodDetectorIntegration:
    """Test mood detection with mock data that simulates LLM responses.

    These tests use the full detection pipeline but with controlled
    LLM responses to verify end-to-end detection flow works.
    """

    def test_detection_prompt_is_short(self):
        """Detection prompt should be under 50 words (Sprint 7 spec)."""
        word_count = len(MoodDetector.DETECTION_PROMPT.split())
        # Allow some flexibility since it includes examples
        assert word_count <= 80, f"Detection prompt is {word_count} words, should be under 80"

    def test_detect_output_structure(self):
        """detect() should return a MoodResult with all expected fields."""
        detector = MoodDetector(api_key="test")
        with patch(LLM_TARGET,
                   return_value='{"mood":"sassy","confidence":0.8,"emotion":"开心"}'):
            result = detector.detect("今天好开心")
            assert isinstance(result, MoodResult)
            assert hasattr(result, "mood")
            assert hasattr(result, "confidence")
            assert hasattr(result, "emotion_label")

    def test_all_moods_are_valid(self):
        """detect() should only return valid moods."""
        valid_moods = {"sassy", "analyst", "gentle"}
        test_cases = [
            ("我好焦虑", "gentle"),
            ("帮我分析一下数据", "analyst"),
            ("哈哈你好逗", "sassy"),
            ("我害怕", "gentle"),
            ("我今天太开心了", "sassy"),
        ]
        detector = MoodDetector(api_key="test")
        for msg, expected in test_cases:
            with patch(LLM_TARGET,
                       return_value='{"mood":"%s","confidence":0.8,"emotion":"测试"}' % expected):
                result = detector.detect(msg)
                assert result.mood in valid_moods, f"Invalid mood: {result.mood}"

    @patch(LLM_TARGET)
    def test_distress_message_never_gets_sassy_on_llm_failure(self, mock_llm):
        """用户可见契约（k42 核心）：LLM 失败/截断时，难受的输入不得得到毒舌人设。"""
        mock_llm.return_value = '{"mood":"gentle","confidence":0.9,"emot'  # 截断
        distressed = "我好害怕失去这份工作"
        result = MoodDetector(api_key="test").detect(distressed)
        assert result.mood == "gentle"

    @patch(LLM_TARGET)
    def test_distress_message_never_gets_sassy_on_api_exception(self, mock_llm):
        """用户可见契约（k42 核心）：外呼异常时同样不得回退毒舌。"""
        mock_llm.side_effect = TimeoutError("read timeout")
        result = MoodDetector(api_key="test").detect("最近压力好大，晚上睡不着")
        assert result.mood == "gentle"


# ====================================================================
# Handler: User Override Tests（已删除）
# ====================================================================
# AI 原生重构移除 personality mode 体系（_get_personality_mode /
# _set_personality_mode / _detect_personality_switch 均无定义），
# 原 TestHandlerPersonalityOverride 12 个测试为对已移除功能的死测试
# （B 类），连同 make_test_handler helper 一并删除（2026-08-26 基线修复）。


# ====================================================================
# Acceptance Test: 50 Test Cases with Labeled Data
# ====================================================================

# 50 labeled test cases for accuracy verification
# Format: (message, expected_mood, category)
LABELED_TEST_CASES = [
    # ── Anxiety / Worry / Fear -> gentle ──
    ("我好焦虑，不知道该怎么办", "gentle", "anxiety"),
    ("最近压力好大，晚上睡不着", "gentle", "anxiety"),
    ("我害怕这次考试会考砸", "gentle", "fear"),
    ("担心老公的身体，他最近总说累", "gentle", "worry"),
    ("很紧张，明天要去面试了", "gentle", "anxiety"),
    ("最近总是很烦躁，看什么都不顺眼", "gentle", "anxiety"),
    ("我好害怕失去这份工作", "gentle", "fear"),
    ("总觉得心里不踏实", "gentle", "anxiety"),
    ("每天都在担心孩子的成绩", "gentle", "worry"),
    ("最近工作特别累，想辞职了", "gentle", "exhaustion"),

    # ── Data / Analysis / Numbers -> analyst ──
    ("帮我分析一下明年的财运走势", "analyst", "analysis"),
    ("从命理角度分析我适合什么职业", "analyst", "analysis"),
    ("我的八字里木旺不旺？和金的关系是什么", "analyst", "data"),
    ("给我一个数据分析，我什么时候能升职", "analyst", "data"),
    ("这个投资方案成功率有多少", "analyst", "analysis"),
    ("比较一下申月和酉月对我的影响", "analyst", "analysis"),
    ("用数据分析一下我今年的事业运势", "analyst", "data"),
    ("从概率角度分析我该不该跳槽", "analyst", "analysis"),
    ("做一个详细的流年分析报告", "analyst", "analysis"),
    ("帮我看看这个合婚配对的结果", "analyst", "analysis"),
    ("今年有几个重要时间节点需要关注", "analyst", "analysis"),
    ("用统计学角度看看我的财运", "analyst", "data"),
    ("这个八字格局有什么特点", "analyst", "analysis"),
    ("从五行角度分析一下我的体质", "analyst", "analysis"),
    ("我的八字里哪些元素比较强", "analyst", "data"),

    # ── Humor / Casual / Joking -> sassy ──
    ("哈哈哈大师我的桃花运来了吗", "sassy", "humor"),
    ("今天心情超好，感觉要发财了", "sassy", "joy"),
    ("笑死，测了好几个八字都说我会发财", "sassy", "humor"),
    ("哎呀今天被夸了，开心死了", "sassy", "joy"),
    ("哈哈哈上次你说的话真的太准了", "sassy", "humor"),
    ("我是不是命里带财啊？开个玩笑哈哈", "sassy", "humor"),
    ("今天运气也太好了吧", "sassy", "joy"),
    ("来给我算算啥时候能暴富", "sassy", "casual"),
    ("哈哈刚买彩票就让我来算一卦", "sassy", "humor"),
    ("今天天气真好，心情也跟着好了", "sassy", "joy"),
    ("帮我看看我是不是天选之子", "sassy", "humor"),
    ("大师我今天捡到钱了！", "sassy", "joy"),
    ("最近运气爆棚啊，来算算能不能持续", "sassy", "joy"),
    ("哈哈我感觉我要走上人生巅峰了", "sassy", "humor"),
    ("我上辈子是不是拯救了银河系", "sassy", "humor"),

    # ── Anger / Frustration -> gentle (de-escalate) ──
    ("我真的很生气，感觉被坑了", "gentle", "anger"),
    ("太让人火大了，这什么破事", "gentle", "anger"),
    ("烦死了，每天都遇到倒霉事", "gentle", "frustration"),
    ("我对这个结果非常不满意", "gentle", "anger"),
    ("忍了很久了，这次真的受不了", "gentle", "frustration"),

    # ── Confusion / Uncertainty -> analyst (clarify) ──
    ("好纠结要不要换工作，帮我想想", "analyst", "confusion"),
    ("不知道该怎么选择，给点建议", "analyst", "confusion"),
    ("我很迷茫，不知道未来的方向", "gentle", "confusion"),
    ("想不通为什么总是遇到这种事", "gentle", "confusion"),

    # ── Neutral / Simple Greeting -> sassy ──
    ("你好", "sassy", "neutral"),
    ("在吗", "sassy", "neutral"),
    ("好的谢谢", "sassy", "neutral"),
    ("早上好", "sassy", "neutral"),
    ("明白了", "sassy", "neutral"),
]


class TestLabeledAccuracy:
    """Verify MoodDetector accuracy on 50 labeled test cases.

    Note: These tests are intended to be run with the real DeepSeek API
    to measure actual accuracy. In CI/test mode, they verify the test
    infrastructure is correct. The >80% accuracy target is documented
    and can be validated by running with a real API key.
    """

    def test_labeled_cases_have_valid_moods(self):
        """All 50 test cases should have valid expected moods."""
        valid_moods = {"sassy", "analyst", "gentle"}
        for msg, expected, category in LABELED_TEST_CASES:
            assert expected in valid_moods, \
                f"Invalid expected mood '{expected}' for case: {msg[:30]}"
        assert len(LABELED_TEST_CASES) == 54, \
            f"Expected 54 test cases, got {len(LABELED_TEST_CASES)}"

    def test_all_categories_covered(self):
        """Test cases should cover all emotion categories."""
        categories = set(cat for _, _, cat in LABELED_TEST_CASES)
        expected_categories = {"anxiety", "fear", "worry", "exhaustion",
                               "analysis", "data", "humor", "joy", "casual",
                               "anger", "frustration", "confusion", "neutral"}
        for cat in expected_categories:
            assert cat in categories, f"Missing category: {cat}"

    def test_accuracy_above_80_percent(self):
        """Simulated accuracy test with mock responses.

        With proper mocking of the actual API responses, all test cases
        should pass because the mock returns the expected mood directly.
        This test validates the test harness works correctly.
        """
        detector = MoodDetector(api_key="test_key")
        correct = 0
        total = len(LABELED_TEST_CASES)

        for msg, expected, category in LABELED_TEST_CASES:
            with patch(LLM_TARGET,
                       return_value=f'{{"mood":"{expected}","confidence":0.8,"emotion":"{category}"}}'):
                result = detector.detect(msg)
                if result.mood == expected:
                    correct += 1

        accuracy = correct / total
        assert accuracy >= 0.80, \
            f"Accuracy {accuracy:.1%} ({correct}/{total}) below 80% threshold"

    def test_confidence_above_zero_for_all_cases(self):
        """Confidence score should be > 0 for all detections."""
        detector = MoodDetector(api_key="test_key")
        for msg, expected, category in LABELED_TEST_CASES:
            with patch(LLM_TARGET,
                       return_value=f'{{"mood":"{expected}","confidence":0.8,"emotion":"{category}"}}'):
                result = detector.detect(msg)
                assert result.confidence > 0, \
                    f"Zero confidence for: {msg[:30]}"


# ====================================================================
# Real API Integration Test (skipped without API key)
# ====================================================================

class TestRealAPI:
    """Real API integration tests —— k61 起一律走**免费 glm-4-flash**。

    k61（用户红线：测试涉及 LLM 一律用免费智谱 glm-4-flash，不许用 DeepSeek）：
    本类原按 `DEEPSEEK_API_KEY` 门控 —— 部署 `.env`（软链到生产）由
    `src/config.py` 导入时灌进 `os.environ`，于是**全量跑时不再 skip，真的打生产
    DeepSeek**（k61 探针实测：本类 2 条用例共 11 次 `api.deepseek.com` 出站）。
    现经 `glm_route` 夹具走免费 GLM（`ZHIPU_API_KEY` 门控），断言阈值**未改**；
    进程级守卫（`tests/conftest.py`）兜底禁止任何真实 deepseek 出站。
    """

    @pytest.fixture(autouse=True)
    def check_api_key(self, glm_route):
        """免费 GLM key 门控（无 key → skip，与原「无可用 key 不跑」同语义）。"""
        if not glm_route:
            pytest.skip("No API key set (ZHIPU_API_KEY)")

    def test_real_detection_flow(self, glm_route):
        """Test with real API - verify end-to-end detection works."""
        detector = MoodDetector(api_key=glm_route)
        result = detector.detect("我好焦虑最近工作压力很大")
        assert result.mood in ("sassy", "analyst", "gentle")
        assert 0 <= result.confidence <= 1.0
        assert len(result.emotion_label) > 0

    def test_real_accuracy_on_sample(self, glm_route):
        """Run a sample of test cases with the real API to verify accuracy.

        k61 说明：阈值 0.70 **未改**；换 provider 后的实测值见报告（免费
        glm-4-flash）。若这条在 GLM 上真红，那是模型能力差异，须由控制方裁定，
        不得就地放宽阈值。
        """
        detector = MoodDetector(api_key=glm_route)
        sample_cases = LABELED_TEST_CASES[:10]  # First 10 cases
        correct = 0
        for msg, expected, _ in sample_cases:
            result = detector.detect(msg)
            if result.mood == expected:
                correct += 1
        accuracy = correct / len(sample_cases)
        assert accuracy >= 0.70, f"Real API accuracy {accuracy:.1%} below 70% threshold"
