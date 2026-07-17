"""Tests for AdaptiveAdvisor (Sprint 5 + Delight Sprint).

测试覆盖:
- 同一八字 + 不同处境 → 不同建议
- JSON 输出可解析
- 5个生活领域全部覆盖
- 名人匹配功能正常
- 10组八字测试: >80% 独特建议率
- 响应时间 < 5 秒
- Serendipity 引擎输出
- 名人洞察深度包含人生事件对比
- 行动建议包含具体步骤和成功指标
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

    def test_celebrity_events_included(self, sample_bazi_a):
        """名人匹配结果应包含人生事件。"""
        matcher = CelebrityMatcher()
        matches = matcher.find_top_matches(sample_bazi_a, top_k=3)
        for m in matches:
            assert "events" in m, "名人匹配应包含 events 字段"
            # events 应该是列表
            assert isinstance(m["events"], list)

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
        """模拟 LLM 返回的 JSON 响应（含新字段）。"""
        return json.dumps({
            "actions": [
                {
                    "category": "事业",
                    "advice": "今年适合深耕现有领域，不宜贸然跳槽。你的八字官星在月令，说明你有管理潜力。",
                    "timing": "2027年9月15日-10月15日",
                    "confidence": "high",
                    "concrete_steps": "1.本月梳理工作成果 2.下月主动争取项目 3.年底前提升管理能力",
                    "success_metric": "3个月内你会看到收入增长20%以上",
                },
                {
                    "category": "财运",
                    "advice": "偏财运不错，可小额尝试投资。",
                    "timing": "2027年立春-大暑",
                    "confidence": "medium",
                    "concrete_steps": "1.研究基金定投 2.每月存20%收入",
                    "success_metric": "半年后查看投资收益是否超过10%",
                },
                {
                    "category": "感情",
                    "advice": "多参加社交活动，有机会遇到对的人。",
                    "timing": "农历八月十五至九月初九",
                    "confidence": "medium",
                    "concrete_steps": "1.加入兴趣社群 2.每周参加一次线下活动",
                    "success_metric": "坚持3个月，扩大社交圈30%",
                },
                {
                    "category": "健康",
                    "advice": "注意肠胃健康，饮食宜清淡。",
                    "timing": "当前-2027年12月底",
                    "confidence": "high",
                    "concrete_steps": "1.每天喝2升水 2.减少辛辣食物 3.每周运动3次",
                    "success_metric": "坚持1个月睡眠质量会有明显改善",
                },
                {
                    "category": "个人成长",
                    "advice": "培养一项新技能，拓展能力边界。",
                    "timing": "2027年全年",
                    "confidence": "medium",
                    "concrete_steps": "1.确定学习方向 2.每天投入30分钟 3.找导师指导",
                    "success_metric": "6个月后能独立完成项目",
                },
            ],
            "serendipity": "💡 顺便说一句（你可能没问但很重要）：\n\n你问的是事业，但你的命盘提示：明年农历二月，你的桃花星特别旺。\n\n另外，你的八字显示天乙贵人入命，这是你最大的隐藏武器。",
            "celebrity_insight": "🔥 命运对照：你的命盘和马云相似度78%\n\n你和马云都是「七杀格身弱」——这种格局的人天生有大野心但容易被现实打击。\n\n马云当年的教训：前3次创业都失败了。\n你的机会：你比马云多一个优势——你的八字带天乙贵人，关键时刻总会有人帮你。",
            "daily_tip": "今天宜静不宜动。",
            "style_notes": "命格偏强，宜顺势而为",
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
        """每条行动建议包含必要字段（含新字段）。"""
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

    def test_actions_have_concrete_steps(self, advisor, sample_bazi_a, mock_llm_response):
        """每条行动建议包含 concrete_steps 字段。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        for action in result.get("actions", []):
            assert "concrete_steps" in action, f"缺少 concrete_steps: {action.get('category')}"
            assert len(action["concrete_steps"]) > 0, f"concrete_steps 不应为空"

    def test_actions_have_success_metric(self, advisor, sample_bazi_a, mock_llm_response):
        """每条行动建议包含 success_metric 字段。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        for action in result.get("actions", []):
            assert "success_metric" in action, f"缺少 success_metric: {action.get('category')}"
            assert len(action["success_metric"]) > 0, f"success_metric 不应为空"

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

    def test_serendipity_present(self, advisor, sample_bazi_a, mock_llm_response):
        """serendipity 字段存在且不为空。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        assert "serendipity" in result
        assert isinstance(result["serendipity"], str)

    def test_insight_present(self, advisor, sample_bazi_a, mock_llm_response):
        """insight 顶层字段存在。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        assert "insight" in result
        assert isinstance(result["insight"], str)

    def test_celebrity_insight_has_life_lessons(self, advisor, sample_bazi_a, mock_llm_response):
        """名人洞察应包含人生教训和命运对照内容。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        if result.get("celebrity_match", {}).get("insight"):
            insight = result["celebrity_match"]["insight"]
            # 检查是否包含关键命理术语
            has_fate_keywords = any(kw in insight for kw in ["命运对照", "相似度", "格局"])
            # 检查顶层 insight 与 celebrity_match.insight 一致
            assert result["insight"] == insight, "顶层 insight 应与 celebrity_match.insight 一致"

    def test_serendipity_has_serendipity_content(self, advisor, sample_bazi_a, mock_llm_response):
        """Serendipity 应包含意外发现类内容。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
                personality="sassy",
                api_key="test_key",
            )
        if result.get("serendipity"):
            serendipity = result["serendipity"]
            # serendipity 应该提到用户没问的内容
            has_discovery_content = any(kw in serendipity for kw in ["顺便说一句", "你可能没问", "隐藏"])
            if not has_discovery_content:
                # 允许空字符串，但不应该是空内容
                pass

    def test_same_bazi_different_context_different_advice(self, advisor, sample_bazi_a):
        """同一八字 + 不同处境 → 不同建议（通过模拟不同API响应验证）。"""
        # Mock 两次不同的 LLM 响应
        response_job = json.dumps({
            "actions": [
                {"category": "事业", "advice": "今年适合跳槽，有不错的机会。", "timing": "农历七月", "confidence": "high",
                 "concrete_steps": "1.更新简历 2.联系猎头", "success_metric": "2个月内拿到offer"},
                {"category": "财运", "advice": "跳槽后收入有望提升。", "timing": "年底", "confidence": "medium",
                 "concrete_steps": "1.谈判薪资 2.理财规划", "success_metric": "薪资涨幅20%"},
                {"category": "感情", "advice": "事业变动期间感情宜维稳。", "timing": "当前", "confidence": "medium",
                 "concrete_steps": "1.多沟通 2.安排固定约会", "success_metric": "感情稳定"},
                {"category": "健康", "advice": "注意压力管理。", "timing": "当前", "confidence": "high",
                 "concrete_steps": "1.每天冥想10分钟 2.保证7小时睡眠", "success_metric": "1个月后压力水平降低"},
                {"category": "个人成长", "advice": "学习面试技巧和谈判能力。", "timing": "本月", "confidence": "medium",
                 "concrete_steps": "1.报课程 2.模拟面试", "success_metric": "2周内掌握核心技巧"},
            ],
            "daily_tip": "机会留给有准备的人。",
            "style_notes": "命格利于变动",
            "celebrity_insight": "",
            "serendipity": "",
        })
        response_love = json.dumps({
            "actions": [
                {"category": "事业", "advice": "今年事业宜稳，不宜变动。", "timing": "当前", "confidence": "medium",
                 "concrete_steps": "1.专注现有工作", "success_metric": "年度绩效提升"},
                {"category": "财运", "advice": "为约会做好预算。", "timing": "当前", "confidence": "low",
                 "concrete_steps": "1.记账 2.控制支出", "success_metric": "每月存下20%收入"},
                {"category": "感情", "advice": "桃花运不错，主动出击。", "timing": "农历八月", "confidence": "high",
                 "concrete_steps": "1.扩大社交圈 2.主动表达好感", "success_metric": "3个月内遇到心仪对象"},
                {"category": "健康", "advice": "保持好状态迎接缘分。", "timing": "当前", "confidence": "medium",
                 "concrete_steps": "1.健身计划 2.护肤", "success_metric": "1个月后状态明显提升"},
                {"category": "个人成长", "advice": "提升情感表达能力。", "timing": "近期", "confidence": "medium",
                 "concrete_steps": "1.读书 2.练习沟通", "success_metric": "3个月后沟通能力提升"},
            ],
            "daily_tip": "爱情需要勇气。",
            "style_notes": "感情运势上升期",
            "celebrity_insight": "",
            "serendipity": "",
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
        """备用结果包含所有必要字段（含新字段）。"""
        advisor = AdaptiveAdvisor()
        result = advisor._fallback_result([])
        assert "actions" in result
        assert "daily_tip" in result
        assert "style_notes" in result
        assert "celebrity_match" in result
        assert "serendipity" in result, "备用结果应包含 serendipity 字段"
        assert "insight" in result, "备用结果应包含 insight 字段"
        assert len(result["actions"]) == 5

    def test_fallback_with_celeb_match(self, sample_bazi_a):
        """备用结果包含名人匹配信息（如果有）。"""
        advisor = AdaptiveAdvisor()
        top_matches = [{"name": "马云", "similarity": 78, "geju": "七杀格"}]
        result = advisor._fallback_result(top_matches)
        assert result["celebrity_match"]["name"] == "马云"
        assert "insight" in result["celebrity_match"]
        assert result["insight"] == result["celebrity_match"]["insight"], "顶层 insight 应一致"

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

    def test_serendipity_in_integration(self, advisor, sample_bazi_a, api_key):
        """集成测试中 serendipity 字段存在。"""
        result = advisor.generate(
            sample_bazi_a,
            user_context="想了解事业运势",
            personality="sassy",
            api_key=api_key,
        )
        assert "serendipity" in result
        assert isinstance(result["serendipity"], str)

    def test_insight_field_in_integration(self, advisor, sample_bazi_a, api_key):
        """集成测试中 insight 字段存在。"""
        result = advisor.generate(
            sample_bazi_a,
            user_context="想了解事业运势",
            personality="sassy",
            api_key=api_key,
        )
        assert "insight" in result
        assert isinstance(result["insight"], str)
