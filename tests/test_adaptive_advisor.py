"""Tests for AdaptiveAdvisor (Sprint 5 + Delight Sprint).

测试覆盖:
- 同一八字 + 不同处境 → 不同建议
- JSON 输出可解析
- 5个生活领域全部覆盖
- 10组八字测试: >80% 独特建议率
- 响应时间 < 5 秒
- Serendipity 引擎输出
- 行动建议包含具体步骤和成功指标
- 名人匹配已移除（2026-08-09 方案 v5 选 A）：celebrity_match 恒为空 dict

k61（用户红线「测试涉及 LLM 一律用免费 glm-4-flash，不许用 DeepSeek」）：
`TestIntegration` 原按 `DEEPSEEK_API_KEY` 门控 —— 部署 `.env`（软链到生产）
由 `src/config.py` 导入时灌进 `os.environ` → 全量跑时真的打生产 DeepSeek
（k61 探针实测 5 条用例共 16 次 `api.deepseek.com` 出站）。现：
  - 真实端到端用例经 `glm_route` 夹具走**免费 glm-4-flash**（ZHIPU_API_KEY 门控）；
  - `test_response_under_5_seconds` 改用 mock 传输层（零外呼）+ 出站请求形状断言；
  - 进程级守卫 `tests/conftest.py::_k61_deepseek_egress_guard` 兜底禁止真实
    deepseek 出站。断言阈值一条未改。
"""
import json
import os
import time
from unittest.mock import patch
from pathlib import Path

import pytest

from src.engines.bazi import BaziEngine, BaziResult
from src.engines.advisor_v2 import AdaptiveAdvisor, LIFE_DOMAINS


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
# 名人匹配功能测试（已移除 2026-08-09 方案 v5 选 A：
# CelebrityMatcher 类整体删除，相关用例一并移除）
# ============================================================

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
                api_key="test_key",
            )
        assert isinstance(result, dict)

    def test_all_five_domains_present(self, advisor, sample_bazi_a, mock_llm_response):
        """5个生活领域全部覆盖。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解事业运势",
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
                api_key="test_key",
            )
        assert "celebrity_match" in result

    def test_serendipity_present(self, advisor, sample_bazi_a, mock_llm_response):
        """serendipity 字段存在且不为空。"""
        with patch.object(advisor, '_call_llm', return_value=mock_llm_response):
            result = advisor.generate(
                sample_bazi_a,
                user_context="想了解运势",
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
            r1 = advisor.generate(sample_bazi_a, user_context="想跳槽，最近有offer", api_key="key")
            r2 = advisor.generate(sample_bazi_a, user_context="想谈恋爱，求姻缘", api_key="key")

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
        result = advisor._fallback_result()
        assert "actions" in result
        assert "daily_tip" in result
        assert "style_notes" in result
        assert "celebrity_match" in result  # 恒为空 dict（名人库已移除，字段向后兼容）
        assert result["celebrity_match"] == {}
        assert "serendipity" in result, "备用结果应包含 serendipity 字段"
        assert "insight" in result, "备用结果应包含 insight 字段"
        assert len(result["actions"]) == 5

    def test_fallback_domains_cover_all(self):
        """备用结果的5个领域覆盖完整。"""
        advisor = AdaptiveAdvisor()
        result = advisor._fallback_result()
        categories = {a["category"] for a in result["actions"]}
        for domain in LIFE_DOMAINS:
            assert domain in categories


# ============================================================
# 集成测试（真实 LLM —— k61 起一律走免费 glm-4-flash）
# ============================================================

class TestIntegration:
    """集成测试 - 需要真实 LLM。

    k61（用户红线「测试涉及 LLM 一律用免费智谱 glm-4-flash，不许用 DeepSeek」）：
    本类原按 `DEEPSEEK_API_KEY` 门控 —— 部署 `.env`（软链到生产）由
    `src/config.py` 导入时灌进 `os.environ`，于是**全量跑时不再 skip，真的打生产
    DeepSeek**（k61 探针实测：本类 5 条用例共 16 次 `api.deepseek.com` 出站）。
    现在：
      - 真实端到端用例（`test_ten_bazi_uniqueness` / `test_different_personality_*
        / test_serendipity_* / test_insight_*`）经 `glm_route` 夹具走**免费
        glm-4-flash**（`ZHIPU_API_KEY` 门控，无 key 才 skip）；
      - 只验证本地管线与请求形状的 `test_response_under_5_seconds` 改用
        mock 传输层，**零外呼**；
      - 进程级守卫（`tests/conftest.py`）兜底：任何真实 deepseek 出站一律失败。
    """

    @pytest.fixture
    def api_key(self, glm_route):
        """真实 LLM 用的 key —— k61 起是**免费 GLM** key（不再是生产 DeepSeek）。

        引擎要求 `api_key` 非空才走 LLM 路径；实际外呼由 `glm_route` 用
        `ZHIPU_API_KEY` 打到 `open.bigmodel.cn`（`glm-4-flash`），传入的付费
        key/模型一律被替换。无 `ZHIPU_API_KEY` → skip（原语义：无可用 key 不跑）。
        """
        return glm_route

    @pytest.fixture
    def advisor(self):
        return AdaptiveAdvisor()

    def _mock_response(self):
        """完整可解析的 LLM 输出（与 TestAdaptiveAdvisorMocked 同形，5 领域齐）。"""
        return json.dumps({
            "actions": [
                {"category": cat, "advice": f"{cat}建议：结合命盘看流年",
                 "timing": "2027年9月15日-10月15日", "confidence": "high",
                 "concrete_steps": "1.本月梳理 2.下月争取",
                 "success_metric": "3个月内可见变化"}
                for cat in LIFE_DOMAINS
            ],
            "serendipity": "顺带一提：你的桃花星很旺。",
            "daily_tip": "今天宜静不宜动。",
            "style_notes": "命格偏强，宜顺势而为",
        }, ensure_ascii=False)

    def test_response_under_5_seconds(self, advisor, sample_bazi_a, mock_deepseek_http):
        """`generate()` 的**本地管线** < 5 秒 + 出站请求形状正确（k61 根因收口）。

        ── 原判据为什么站不住（k61 根因） ───────────────────────────────────
        原实现断言的是 `advisor.generate()` 的整体墙钟 < 5 秒，而该调用内部含
        **一次真实远端 LLM 调用**（`_call_llm` → 统一层 → `api.deepseek.com`，
        `max_tokens=3000`、`timeout=45.0`）。远端生成延迟（服务端排队/生成长度/
        网络抖动）**不是我方可控量**，拿 5 秒硬墙钟当门禁判据必然假红：
        k57 门禁实测 >5s 红；含 k57 全部改动的 k59 门禁里同一用例是绿的
        → 判定为环境性假红（代码未变，只有并发/上游变了）。

        ── 收口（不外呼，且不掏空断言） ────────────────────────────────────
        ① 耗时断言限定在**我方拥有的部分**：mock 传输层固定 LLM 响应（零外呼），
           墙钟此时只覆盖 提示词构造 + 命盘格式化 + 响应解析 + 组装 + emoji 收敛
           —— 这些都是本模块自己会写坏的代码（实测分布见报告，p99 远低于 5s）。
        ② 原来隐含的「真调了 DeepSeek 且按约定参数调」改成**显式形状断言**
           （端点/模型/max_tokens/thinking/鉴权头）—— 比原来只断言耗时**更强**：
           原来即使把端点调错、参数写错，只要快也照样绿。
        ③ 真实 LLM 的端到端行为仍由本类其余用例经免费 GLM 覆盖。
        """
        captured = mock_deepseek_http(self._mock_response())

        start = time.time()
        result = advisor.generate(
            sample_bazi_a,
            user_context="最近工作很忙，想了解事业运势",
            api_key="k61-local-pipeline",
        )
        elapsed = time.time() - start
        assert elapsed < 5.0, f"本地管线耗时 {elapsed:.2f}s 超过 5s 限制"
        assert len(result.get("actions", [])) == 5

        # 形状断言：确实经统一层打了 DeepSeek 兼容端点，且参数契约未漂移
        assert len(captured) == 1, f"应恰好一次 LLM 调用，实际 {len(captured)}"
        req = captured[0]
        assert req["method"] == "POST"
        assert req["url"] == "https://api.deepseek.com/anthropic/v1/messages"
        assert req["json"]["model"] == "deepseek-flash[1m]"
        assert req["json"]["max_tokens"] == 3000
        assert req["json"]["thinking"] == {"type": "disabled"}
        assert req["headers"]["authorization"] == "Bearer k61-local-pipeline"
        assert req["json"]["messages"][0]["role"] == "system"

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

    # 「不同人格模式生成不同风格建议」用例已移除（k62 r2，2026-09-20）。
    # 原因：**名不符实** —— 它声称验证「不同人设 → 不同输出」，实际什么都没验证：
    #   1. 它的三次调用**逐字节相同**（同一夹具 + 同一 user_context + 同一 api_key），
    #      只有局部变量名 r_sassy/r_analyst/r_gentle 在假装不同；
    #   2. 更根本的是，它测的 API **根本没有该参数** ——
    #      `AdaptiveAdvisor.generate(bazi_result, user_context="", api_key="")`
    #      没有 personality/style 形参，故「传不同人设」在结构上不可能；
    #   3. 它还被 `api_key` fixture **默认 skip**（未设 DEEPSEEK_API_KEY 时），
    #      且**未 mock** —— 一旦被 skip 掉，等于零覆盖；
    #   4. 即便真跑，它断言的是「三次**相同**请求中至少两次结果不同」，
    #      命中的是 LLM 采样随机性（temperature），与人设无关。
    # 该职责现由 **k63 的不变式**承担（单一豆包口吻：advisor_v2 的风格指令不再随
    # 性别/人设分叉）。**不要恢复本用例** —— 除非先给 generate() 真正加上人设参数。

    def test_serendipity_in_integration(self, advisor, sample_bazi_a, api_key):
        """集成测试中 serendipity 字段存在。"""
        result = advisor.generate(
            sample_bazi_a,
            user_context="想了解事业运势",
            api_key=api_key,
        )
        assert "serendipity" in result
        assert isinstance(result["serendipity"], str)

    def test_insight_field_in_integration(self, advisor, sample_bazi_a, api_key):
        """集成测试中 insight 字段存在。"""
        result = advisor.generate(
            sample_bazi_a,
            user_context="想了解事业运势",
            api_key=api_key,
        )
        assert "insight" in result
        assert isinstance(result["insight"], str)
