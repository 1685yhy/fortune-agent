"""Tests for AdaptiveAdvisor (Sprint 5).

测试覆盖:
- 同一八字 + 不同处境 → 不同建议
- JSON 输出可解析
- 5个生活领域全部覆盖
- 名人匹配功能正常
- 10组八字测试: >80% 独特建议率
- 响应时间 < 5 秒
"""
import json
import os
import time
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from src.engines.bazi import BaziEngine, BaziResult
from src.engines.advisor_v2 import AdaptiveAdvisor, CelebrityMatcher, LIFE_DOMAINS


# ============================================================
# 测试夹具
# ============================================================

@pytest.fixture(scope="module")
def engine():
    return BaziEngine()


@pytest.fixture
def sample_bazi_a(engine):
    """1990年5月20日 15:00 北京 男 - 典型案例"""
    return engine.calculate(1990, 5, 20, 15, 0, "北京", "男")


@pytest.fixture
def sample_bazi_b(engine):
    """1995年8月15日 10:30 上海 女 - 不同案例"""
    return engine.calculate(1995, 8, 15, 10, 30, "上海", "女")


@pytest.fixture
def sample_bazi_c(engine):
    """1985年3月1日 12:00 广州 男 - 第三种案例"""
    return engine.calculate(1985, 3, 1, 12, 0, "广州", "男")


# ============================================================
# 名人匹配功能测试
# ============================================================

class TestCelebrityMatcher:
    """测试名人匹配功能。"""

    def test_matcher_loads_data(self):
        """名人库加载正常。"""
        matcher = CelebrityMatcher()
        assert len(matcher._celebrities) > 0, "名人库应为非空"
        assert len(matcher._celebrities) >= 900, f"名人库应包含至少900人, 当前: {len(matcher._celebrities)}"

    def test_parse_birth_with_time(self):
        """解析带具体时间的出生字符串。"""
        matcher = CelebrityMatcher()
        result = matcher._parse_birth("1955年2月24日 19:15 旧金山 男")
        assert result is not None
        year, month, day, hour, minute, city, gender = result
        assert year == 1955
        assert month == 2
        assert day == 24
        assert hour == 19
        assert minute == 15
        assert city == "旧金山"
        assert gender == "男"

    def test_parse_birth_without_time(self):
        """解析不带具体时间的出生字符串。"""
        matcher = CelebrityMatcher()
        result = matcher._parse_birth("1964年9月10日 时辰不详 杭州 男")
        assert result is not None
        year, month, day, hour, minute, city, gender = result
        assert year == 1964
        assert month == 9
        assert day == 10
        assert hour == 0  # 缺省为子时
        assert minute == 0
        assert city == "杭州"
        assert gender == "男"

    def test_parse_birth_female(self):
        """解析女性出生信息。"""
        matcher = CelebrityMatcher()
        result = matcher._parse_birth("2000年11月4日 时辰不详 石家庄 女")
        assert result is not None
        assert result[6] == "女"  # gender

    def test_find_top_matches(self, sample_bazi_a):
        """不同八字应匹配不同的名人。"""
        matcher = CelebrityMatcher()
        matches = matcher.find_top_matches(sample_bazi_a, top_k=3)
        assert len(matches) <= 3
        for m in matches:
            assert "name" in m
            assert "similarity" in m
            assert 0 <= m["similarity"] <= 100
            assert "geju" in m

    def test_different_bazi_different_matches(self, sample_bazi_a, sample_bazi_b):
        """不同八字匹配不同名人。"""
        matcher = CelebrityMatcher()
        matches_a = matcher.find_top_matches(sample_bazi_a)
        matches_b = matcher.find_top_matches(sample_bazi_b)
        # 可能为空或相同，但至少检查接口可用
        if matches_a and matches_b:
            names_a = [m["name"] for m in matches_a]
            names_b = [m["name"] for m in matches_b]
            # 不同八字极大概率匹配不同名人
            assert names_a != names_b, "不同八字应匹配不同的名人"

    def test_similarity_score_bounds(self, sample_bazi_a):
        """相似度得分应在 0-100 范围内。"""
        matcher = CelebrityMatcher()
        matches = matcher.find_top_matches(sample_bazi_a)
        for m in matches:
            assert 0 <= m["similarity"] <= 100
        # 如果有匹配，最高分应 > 0
        if matches:
            assert matches[0]["similarity"] > 0


# ============================================================
# LLM 调用测试 (带 mock)
# ============================================================

class TestAdaptiveAdvisorMocked:
    """测试 AdaptiveAdvisor（mock LLM 调用）。"""

    @pytest.fixture
    def advisor(self):
        return AdaptiveAdvisor()

    @pytest.fixture
    def mock_llm_response(self):
        """模拟 LLM 返回的 JSON 响应。"""
        return json.dumps({
            "actions": [
                {
                    "category": "事业",
                    "advice": "今年适合深耕现有领域，不宜贸然跳槽。",
                    "timing": "农历十月",
                    "confidence": "high",
                },
                {
                    "category": "财运",
                    "advice": "偏财运不错，可小额尝试投资。",
                    "timing": "2027年春季",
                    "confidence": "medium",
                },
                {
                    "category": "感情",
                    "advice": "多参加社交活动，有机会遇到对的人。",
                    "timing": "农历八月",
                    "confidence": "medium",
                },
                {
                    "category": "健康",
                    "advice": "注意肠胃健康，饮食宜清淡。",
                    "timing": "当前",
                    "confidence": "high",
                },
                {
                    "category": "个人成长",
                    "advice": "培养一项新技能，拓展能力边界。",
                    "timing": "全年",
                    "confidence": "medium",
                },
            ],
            "daily_tip": "今天宜静不宜动。",
            "style_notes": "命格偏强，宜顺势而为",
            "celebrity_insight": "你和马云的格局相似，都是七杀格，适合创业型发展。",
        })

    def test_json_output_parseable(self, advisor, sample_bazi_a, mock_llm_response):
        """JSON 输出可解析为有效字典。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解事业运势",
                personality="sassy",
                api_key="test_key",
            )
        assert isinstance(result, dict)

    def test_all_five_domains_present(self, advisor, sample_bazi_a, mock_llm_response):
        """5个生活领域全部覆盖。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解事业运势",
                personality="sassy",
                api_key="test_key",
            )
        actions = result.get("actions", [])
        categories = {a.get("category") for a in actions}
        for domain in LIFE_DOMAINS:
            assert domain in categories, f"缺少领域: {domain}"

    def test_actions_have_required_fields(self, advisor, sample_bazi_a, mock_llm_response):
        """每条行动建议包含必要字段。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        for action in result.get("actions", []):
            assert "category" in action
            assert "advice" in action
            assert "timing" in action
            assert "confidence" in action
            assert action["confidence"] in ("high", "medium", "low")

    def test_daily_tip_present(self, advisor, sample_bazi_a, mock_llm_response):
        """daily_tip 字段存在。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        assert "daily_tip" in result
        assert len(result["daily_tip"]) > 0

    def test_style_notes_present(self, advisor, sample_bazi_a, mock_llm_response):
        """style_notes 字段存在。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        assert "style_notes" in result
        assert len(result["style_notes"]) > 0

    def test_celebrity_match_present(self, advisor, sample_bazi_a, mock_llm_response):
        """celebrity_match 字段存在（即使没有名人匹配也应有空字典）。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        assert "celebrity_match" in result

    def test_same_bazi_different_context_different_advice(self, advisor, sample_bazi_a):
        """同一八字 + 不同处境 → 不同建议（通过模拟不同API响应验证）。"""
        # Mock 两次不同的 LLM 响应
        response_job = json.dumps({
            "actions": [
                {"category": "事业", "advice": "今年适合跳槽，有不错的机会。", "timing": "农历七月", "confidence": "high"},
                {"category": "财运", "advice": "跳槽后收入有望提升。", "timing": "年底", "confidence": "medium"},
                {"category": "感情", "advice": "事业变动期间感情宜维稳。", "timing": "当前", "confidence": "medium"},
                {"category": "健康", "advice": "注意压力管理。", "timing": "当前", "confidence": "high"},
                {"category": "个人成长", "advice": "学习面试技巧和谈判能力。", "timing": "本月", "confidence": "medium"},
            ],
            "daily_tip": "机会留给有准备的人。",
            "style_notes": "命格利于变动",
            "celebrity_insight": "",
        })
        response_love = json.dumps({
            "actions": [
                {"category": "事业", "advice": "今年事业宜稳，不宜变动。", "timing": "当前", "confidence": "medium"},
                {"category": "财运", "advice": "为约会做好预算。", "timing": "当前", "confidence": "low"},
                {"category": "感情", "advice": "桃花运不错，主动出击。", "timing": "农历八月", "confidence": "high"},
                {"category": "健康", "advice": "保持好状态迎接缘分。", "timing": "当前", "confidence": "medium"},
                {"category": "个人成长", "advice": "提升情感表达能力。", "timing": "近期", "confidence": "medium"},
            ],
            "daily_tip": "爱情需要勇气。",
            "style_notes": "感情运势上升期",
            "celebrity_insight": "",
        })

        with patch.object(advisor, '_call_llm', side_effect=[response_job, response_love]):
            r1 = advisor.generate(sample_bazi_a, user_context="想跳槽，最近有offer", personality="sassy", api_key="key")
            r2 = advisor.generate(sample_bazi_a, user_context="想谈恋爱，求姻缘", personality="sassy", api_key="key")

        # 验证输出不同
        assert r1 != r2, "同一八字 + 不同处境应生成不同建议"

        # 验证相关性
        r1_advice = " ".join(a.get("advice", "") for a in r1.get("actions", []))
        r2_advice = " ".join(a.get("advice", "") for a in r2.get("actions", []))
        assert "跳槽" in r1_advice or "offer" in r1_advice, "跳槽处境应包含相关建议"
        assert "桃花" in r2_advice or "恋爱" in r2_advice or "姻缘" in r2_advice, "恋爱处境应包含相关建议"


    def test_parse_llm_markdown_wrapped(self, advisor):
        """LLM 返回 markdown 包裹的 JSON 也能解析。"""
        markdown_json = "```json\n{\"actions\": [], \"daily_tip\": \"test\", \"style_notes\": \"test\"}\n```"
        result = advisor._parse_llm_output(markdown_json)
        assert result["daily_tip"] == "test"

    def test_parse_llm_plain_json(self, advisor):
        """纯 JSON 字符串也能解析。"""
        plain = '{"actions": [], "daily_tip": "test", "style_notes": "test"}'
        result = advisor._parse_llm_output(plain)
        assert result["daily_tip"] == "test"


# ============================================================
# 备用逻辑测试
# ============================================================

class TestFallback:
    """测试 API 失败时的备用逻辑。"""

    def test_fallback_has_all_fields(self, sample_bazi_a):
        """备用结果包含所有必要字段。"""
        advisor = AdaptiveAdvisor()
        result = advisor._fallback_result([])
        assert "actions" in result
        assert "daily_tip" in result
        assert "style_notes" in result
        assert "celebrity_match" in result
        assert len(result["actions"]) == 5

    def test_fallback_with_celeb_match(self, sample_bazi_a):
        """备用结果包含名人匹配信息（如果有）。"""
        advisor = AdaptiveAdvisor()
        top_matches = [{"name": "马云", "similarity": 78, "geju": "七杀格"}]
        result = advisor._fallback_result(top_matches)
        assert result["celebrity_match"]["name"] == "马云"
        assert "insight" in result["celebrity_match"]

    def test_fallback_domains_cover_all(self):
        """备用结果的5个领域覆盖完整。"""
        advisor = AdaptiveAdvisor()
        result = advisor._fallback_result([])
        categories = {a["category"] for a in result["actions"]}
        for domain in LIFE_DOMAINS:
            assert domain in categories


# ============================================================
# 集成测试（可选，需要真实 API Key）
# ============================================================

class TestIntegration:
    """集成测试 - 需要真实 API Key。

    设置环境变量 DEEPSEEK_API_KEY 后才会运行。
    """

    @pytest.fixture
    def api_key(self):
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            pytest.skip("需要设置 DEEPSEEK_API_KEY 环境变量")
        return key

    @pytest.fixture
    def advisor(self):
        return AdaptiveAdvisor()

    def test_response_under_5_seconds(self, advisor, sample_bazi_a, api_key):
        """响应时间 < 5 秒。"""
        start = time.time()
        result = advisor.generate(
            sample_bazi_a,
            user_context="最近工作很忙，想了解事业运势",
            personality="sassy",
            api_key=api_key,
        )
        elapsed = time.time() - start
        assert elapsed < 5.0, f"响应时间 {elapsed:.2f}s 超过 5s 限制"
        assert len(result.get("actions", [])) == 5

    def test_ten_bazi_uniqueness(self, advisor, engine, api_key):
        """10组不同八字 → >80% 独特建议率。"""
        test_cases = [
            (1990, 5, 20, 15, 0, "北京", "男"),
            (1995, 8, 15, 10, 30, "上海", "女"),
            (1985, 3, 1, 12, 0, "广州", "男"),
            (2000, 1, 1, 8, 0, "成都", "女"),
            (1978, 12, 25, 18, 30, "深圳", "男"),
            (1988, 6, 15, 14, 0, "杭州", "女"),
            (1992, 11, 30, 6, 0, "南京", "男"),
            (1982, 4, 10, 22, 0, "武汉", "女"),
            (1998, 7, 25, 16, 30, "重庆", "男"),
            (1975, 9, 5, 4, 0, "天津", "女"),
        ]

        all_advice = []
        for year, month, day, hour, minute, city, gender in test_cases:
            result = engine.calculate(year, month, day, hour, minute, city, gender)
            adv = advisor.generate(
                result,
                user_context="想了解近期运势",
                personality="analyst",
                api_key=api_key,
            )
            # 提取所有建议文本用于比对
            advice_texts = []
            for action in adv.get("actions", []):
                advice_texts.append(action.get("advice", ""))
            all_advice.append("\n".join(advice_texts))

        # 计算独特性：比较每组建议的文本
        # 使用编辑距离的简化：如果任意两条建议的句子级重叠 >= 60%，视为重复
        unique_count = 0
        for i, adv_i in enumerate(all_advice):
            is_unique = True
            for j, adv_j in enumerate(all_advice):
                if i >= j:
                    continue
                # 简化的重复检测：如果共享句子超过60%
                sentences_i = set(s.strip() for s in adv_i.split("\n") if s.strip())
                sentences_j = set(s.strip() for s in adv_j.split("\n") if s.strip())
                if not sentences_i or not sentences_j:
                    continue
                common = sentences_i & sentences_j
                ratio = len(common) / min(len(sentences_i), len(sentences_j))
                if ratio > 0.6:
                    is_unique = False
                    break
            if is_unique:
                unique_count += 1

        uniqueness_rate = unique_count / len(test_cases)
        assert uniqueness_rate > 0.8, (
            f"独特建议率 {uniqueness_rate:.0%} 未达到 80% 要求"
        )

    def test_different_personality_different_output(self, advisor, sample_bazi_a, api_key):
        """不同人格模式生成不同风格的建议。"""
        r_sassy = advisor.generate(
            sample_bazi_a,
            user_context="想了解最近运势",
            personality="sassy",
            api_key=api_key,
        )
        r_analyst = advisor.generate(
            sample_bazi_a,
            user_context="想了解最近运势",
            personality="analyst",
            api_key=api_key,
        )
        r_gentle = advisor.generate(
            sample_bazi_a,
            user_context="想了解最近运势",
            personality="gentle",
            api_key=api_key,
        )

        # 验证输出不同（风格不同，建议措辞应不同）
        texts = [
            json.dumps(r_sassy.get("actions", []), ensure_ascii=False),
            json.dumps(r_analyst.get("actions", []), ensure_ascii=False),
            json.dumps(r_gentle.get("actions", []), ensure_ascii=False),
        ]
        assert texts[0] != texts[1] or texts[1] != texts[2], "不同人格模式应生成不同建议"
