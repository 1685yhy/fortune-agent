# -*- coding: utf-8 -*-
"""k11b 联网搜索语义触发改造测试（分支 k11b-search-trigger）。

覆盖（对应 plan docs/superpowers/plans/2026-09-07-k11b-search-trigger.md）：
A 触发判定纯函数（decide_search）：实体层/时效层/白名单正信号 − 金融排除 −
  命理本地锚；「今年运势如何/我明年财运」零触发；实体背景+本地问（我在易宝支付
  上班 今年运势怎么样）零触发；T074 金融词族仍硬排除
B 来源痕迹尾注（append_source_trace）：LLM 漏写来源时确定性补尾注；已有来源
  不补；反馈提示「准/不准」行不被挤开
C handler 接线：_web_search_allowed 委托语义判定（旧 T074 断言不变 + 新实体
  QA 放行）；hint 门控；频控护栏；同 query 复用去重；域名/长度护栏；
  _engine_domain_ground_search 命中/降级/跳过
D 评测正例（T104 实体 QA）：process() 全链路「易宝支付这家公司靠不靠谱」→
  触发 web_search + 注入润色上下文 + 回复带来源痕迹 + 无甩锅句 + 无 JSON 泄漏
  + 引用注册 type=web；检索空 → 降级话术注（不甩锅不静默）
E 评测负例（T105）：运势类本地计算问题全链路零搜索（含 LLM needs_search 误报
  也被本地锚否决）

运行：cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k11b_search_trigger.py -q -p no:cacheprovider
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

# e2e 走真实引擎主链时排盘卡 html 会写 CHARTS_DIR（默认 /opt/fortune-data 无权限）
# → 重定向到临时目录（须在导入 handler 前设置，模块级常量读取）
os.environ.setdefault(
    "CHARTS_DIR", tempfile.mkdtemp(prefix="k11b_charts_"))

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import MessageAnalysis  # noqa: E402
from src.rag.search_trigger import (  # noqa: E402
    append_source_trace, decide_search, extract_entity_mentions)
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

_TMP_DIRS = []


def _make_db_path():
    tmpdir = tempfile.mkdtemp(prefix="fortune_k11b_")
    db_path = os.path.join(tmpdir, "test.db")
    init_db(db_path)
    _TMP_DIRS.append(tmpdir)
    return db_path


def _seed_person(db_path, user_id):
    PersonDAO(db_path).create_person(
        user_id, "我", "本人",
        birth={"birth_year": 1990, "birth_month": 5, "birth_day": 20,
               "birth_hour": 15, "birth_minute": 30,
               "city": "北京", "gender": "男"},
        is_default=True)


def _mock_result(bazi=None, gender="男"):
    """可 JSON 序列化的引擎结果（同 test_eval_r1_2 装配；save_chart 兼容）。"""
    result = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                        "liunian", "liunian_full", "geju", "yongshen",
                        "shensha", "nayin", "gender"])
    result.bazi = bazi or ["庚午", "辛巳", "乙酉", "甲申"]
    result.day_master = "乙木"
    result.wuxing = {"木": 3, "火": 2, "金": 2, "水": 1, "土": 2}
    result.shishen = ["正官", "七杀", "正财", "偏印"]
    result.dayun = [(4, "庚辰"), (14, "己卯"), (24, "戊寅")]
    result.liunian = {"2026": "庚午", "2027": "辛未"}
    result.liunian_full = []
    result.geju = "七杀格"
    result.yongshen = "木"
    result.shensha = ["天乙贵人"]
    result.nayin = ["路旁土", "白蜡金", "泉中水", "井泉水"]
    result.gender = gender
    return result


def _mock_result_engine():
    engine = Mock()
    engine.calculate.return_value = _mock_result()
    return engine


def _make_handler(db_path, engine=None, llm=None, session=None):
    """同 test_eval_r1_2 装配：llm 主分析返回串接字段（.analyze/.chat）预配置。"""
    from src.bot.handler import MessageHandler
    dao = UserDAO(db_path)
    llm = llm or Mock()
    if isinstance(llm, Mock):
        llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
        llm.chat_conversation.return_value = "🔮 命理助手 返回的结果"
        analysis = Mock()
        analysis.response = "您的八字分析结果：日主乙木，整体格局…"
        llm.analyze.return_value = analysis
    session = session or Mock()
    if isinstance(session, Mock):
        session.get_context_for_llm.return_value = []
        session.add_message.return_value = None
    handler = MessageHandler(
        engine=engine or _mock_result_engine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(), retriever=Mock(),
        hehun_engine=Mock(), llm=llm, dao=dao, session_dao=session,
    )
    handler._start_pregen_instant = lambda msg: None
    return handler, dao


def _patch_intent(handler, intent, needs_search=False, facts=None):
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent=intent, needs_search=needs_search, facts=facts or {}))


def _offline_web(monkeypatch, results=None, available=True):
    """把 handler 模块命名空间里的 search_web/web_search_available 全部离线打桩。"""
    import src.bot.handler as handler_mod
    calls = []
    if results is None:
        results = [
            {"title": "易宝支付官网", "url": "https://www.yeepay.com/",
             "text": "易宝支付是第三方支付公司。", "site_name": ""},
            {"title": "知乎：易宝支付怎么样", "url": "https://www.zhihu.com/question/1",
             "text": "易宝支付口碑讨论。", "site_name": ""},
        ]
    monkeypatch.setattr(handler_mod, "search_web",
                        lambda keywords, limit=5: (calls.append(keywords) or results))
    monkeypatch.setattr(handler_mod, "web_search_available",
                        lambda force=False: available)
    return calls


# ============================================================
# A：触发判定纯函数（T104 正例 / T105 负例 / T074 防回归）
# ============================================================

class TestDecideSearch:
    def test_entity_qa_company_phrases(self):
        """PM 实诉句与变体（含「公司」后缀实体）→ 必搜，query=实体名。"""
        for msg in ("易宝支付这家公司靠不靠谱", "易宝支付这家公司怎么样",
                    "易宝支付这家公司咋样适合我吗", "易宝支付靠谱吗",
                    "易宝支付是什么公司", "易宝支付适合我吗"):
            d = decide_search(msg)
            assert d.should_search, msg
            assert d.entity == "易宝支付", (msg, d.entity)
            assert d.query == "易宝支付"
        d = decide_search("帮我查一下苹果公司的最新新闻")
        assert d.should_search and d.entity == "苹果"
        d = decide_search("招商银行的待遇怎么样")
        assert d.should_search and d.entity == "招商银行"
        d = decide_search("小米汽车值得买吗")
        assert d.should_search and d.entity == "小米汽车"

    def test_entity_qa_no_suffix_brand(self):
        """无后缀主流实体名表（腾讯/字节跳动…）→ 必搜。"""
        for msg in ("腾讯怎么样", "字节跳动靠谱吗", "阿里巴巴值得去吗"):
            d = decide_search(msg)
            assert d.should_search, msg
            assert d.entity, msg
            assert d.query == d.entity

    def test_local_fortune_zero_search(self):
        """T105：本地计算问题零触发（含实体只是背景的句式）。"""
        for msg in ("今年运势如何", "我明年财运怎么样", "这个月适合搬家吗",
                    "我和她八字合不合", "给孩子起个名字", "看看我适合做什么工作",
                    "帮我算算我明年的流年", "属猴的人今年运势怎么样"):
            d = decide_search(msg)
            assert not d.should_search, msg
        # 实体背景 + 本地问（问词挂在运势上，不搜）
        for msg in ("我在易宝支付上班 今年运势怎么样",
                    "我朋友在阿里巴巴工作，他今年运势如何",
                    "在易宝支付上班 运势如何"):
            d = decide_search(msg)
            assert not d.should_search, (msg, d)

    def test_local_fortune_r1_reproductions_zero(self):
        """k11b-r1 审查复现句式（P1-A）零触发：
        ①「X运」族（工作运/事业运/桃花运…）+最近/今年前缀（timely 误触修复）；
        ② 个人决策族+决策问句 cue（跳槽/换工作+什么时候/时机）；
        ③ 开公司/行业词/银行/金融+个人适配 cue（白名单裸词"公司"误触修复）；
        ④ 命理主题句（五行/行业+适合…吗 → 也不抽垃圾实体，见抽取测试）。"""
        for msg in ("最近工作运怎么样", "今年工作运怎么样", "最近事业运如何",
                    "最近桃花运怎么样", "帮我看看什么时候适合跳槽",
                    "想跳槽 帮我看看时机", "什么时候适合搬家",
                    "我该不该换工作", "去开公司适合我吗",
                    "五行属水的行业适合开公司吗", "金融行业适合我吗",
                    "银行工作适合我吗", "我适合去银行工作吗",
                    "在银行还是互联网公司适合我", "我适合去国企吗"):
            d = decide_search(msg)
            assert not d.should_search, (msg, d)

    def test_external_timely_news_policy_allowed_r1(self):
        """k11b-r1（P2-B 决策记录）：新闻/政策/行业类合法外部时效问放行——
        本地锚只拦「命理本地/个人决策」问，不拦纯外部时效事实（chat 域与引擎域
        同一判定）。"""
        for msg in ("最近有什么行业新闻", "最近有什么行业政策",
                    "最近有什么政策变化", "帮我查一下最近的行业新闻",
                    "最近有什么新闻"):
            d = decide_search(msg)
            assert d.should_search, (msg, d)

    def test_named_entity_timing_question_searchable_r1(self):
        """真实命名实体 + 外部时效问（什么时候发财报）→ 实体层放行——
        口语决策族只拦无命名实体的本地问，不误伤命名实体时效问。"""
        d = decide_search("XX科技公司什么时候发财报")
        assert d.should_search and d.entity == "XX科技"

    def test_finance_hard_exclude(self):
        """T074：金融行情永不搜（产品无行情数据源）。"""
        for msg in ("今天股市行情怎么样", "帮我查一下大盘指数",
                    "看看最近的股票行情", "买哪只基金好"):
            assert not decide_search(msg).should_search, msg
            assert decide_search(msg).reason == "finance"

    def test_timely_and_research_positive(self):
        """时效/查证层与研究白名单（无实体、无本地锚时仍可搜）。"""
        d = decide_search("最近有什么政策变化")
        assert d.should_search and d.reason == "timely"
        d = decide_search("帮我查一下最近的行业新闻")
        assert d.should_search and d.query
        d = decide_search("帮我查一下苹果公司的最新新闻")
        assert d.should_search and d.entity

    def test_deictic_only_not_searched(self):
        """纯指代无命名主体（这家公司/哪个公司/什么样的工作）→ 不搜。"""
        for msg in ("这家公司怎么样", "哪个公司靠谱", "什么样的工作适合我",
                    "这家医院好吗", "这个平台靠谱吗"):
            d = decide_search(msg)
            assert not d.should_search, (msg, d)

    def test_llm_signal_or(self):
        """LLM 语义信号（分析器 needs_search）作 OR：无命名实体+无时效词时仍可搜；
        评估类问词（口碑/评价）无实体主语不触发（缺主语搜不出价值）；本地锚硬否决。"""
        msg = "新开的那个中医馆 靠谱吗"
        assert not decide_search(msg).should_search          # 无实体无时效词 → 不搜
        d = decide_search(msg, llm_needs_search=True)
        assert d.should_search and d.reason == "llm"          # LLM 语义信号补位
        assert d.query
        # 本地锚是硬否决：LLM 误报 needs_search 也不搜
        assert not decide_search("今年运势如何", llm_needs_search=True).should_search
        assert not decide_search("我明年财运怎么样", llm_needs_search=True).should_search

    def test_eval_noun_without_entity_not_searched(self):
        """口碑/评价/待遇 类评估词无实体主语 → 不搜（垃圾 query 防御）。"""
        for msg in ("某新成立的医馆口碑如何", "那家店待遇怎么样"):
            assert not decide_search(msg).should_search, msg

    def test_extract_mentions_spots(self):
        """实体抽取：后缀剥离定中指代；泛化词干不算命名实体。"""
        assert extract_entity_mentions("易宝支付这家公司怎么样") == ["易宝支付"]
        assert extract_entity_mentions("帮我查一下苹果公司的最新新闻") == ["苹果"]
        assert extract_entity_mentions("培训机构靠谱吗") == []
        assert "腾讯" in extract_entity_mentions("腾讯怎么样")
        assert extract_entity_mentions("易宝支付") == ["易宝支付"]

    def test_extract_junk_candidates_r1(self):
        """k11b-r1（P2-C/P1-A）：句子功能词/命理主题词内嵌的名称串 = 垃圾候选；
        「某/某些」泛化指代 head 剥离；真实品牌（中国太保/美的）不误伤。"""
        for msg in ("五行属水的行业适合开公司吗", "去开公司适合我吗",
                    "在银行还是互联网公司适合我", "某公司怎么样", "某平台靠谱吗"):
            assert extract_entity_mentions(msg) == [], msg
        assert extract_entity_mentions("中国太保集团怎么样") == ["中国太保集团"]
        assert extract_entity_mentions("美的集团怎么样") == ["美的集团"]
        assert extract_entity_mentions("易宝支付这家公司怎么样") == ["易宝支付"]


# ============================================================
# B：来源痕迹确定性尾注
# ============================================================

class TestAppendSourceTrace:
    def test_appends_when_no_source_trace(self):
        r = append_source_trace("我觉得易宝支付这家公司还行。", "易宝支付",
                                ["www.zhihu.com", "yeepay.com"])
        assert "信息来源" in r
        assert "zhihu.com" in r and "yeepay.com" in r
        assert "官方渠道" in r
        assert r.endswith("为准）")

    def test_skip_when_has_source_or_no_entity(self):
        base = "我觉得易宝支付还行。（来源：知乎讨论）"
        assert append_source_trace(base, "易宝支付", ["zhihu.com"]) == base
        base2 = "今年运势整体平稳。"
        assert append_source_trace(base2, "易宝支付", ["zhihu.com"]) == base2
        assert append_source_trace("易宝支付还不错。", "", ["zhihu.com"]) == "易宝支付还不错。"
        assert append_source_trace("易宝支付还不错。", "易宝支付", []) == "易宝支付还不错。"

    def test_insert_before_feedback_prompt(self):
        fb = "分析完毕。\n\n———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我"
        r = append_source_trace("易宝支付是支付公司。\n\n" + fb, "易宝支付", ["zhihu.com"])
        assert "信息来源" in r
        assert r.index("信息来源") < r.index("———")
        assert r.endswith("告诉我")  # 准/不准交互位在最后，不被来源行挤开


# ============================================================
# C：handler 接线（gate / 护栏 / executor 复用 / ground 注入）
# ============================================================

@pytest.fixture
def bare_handler():
    """object.__new__ 装配（不跑 __init__）——同既有 handler 单测惯例。"""
    h = object.__new__(MessageHandler)
    h.__dict__.setdefault("_citations", {})
    return h


class TestWebSearchAllowedGate:
    def test_t074_finance_still_blocked(self, bare_handler):
        """T074 防回归：旧三层关键词门控的金融排除语义原样保留。"""
        for msg in ("今天股市行情怎么样", "帮我查一下大盘指数", "看看最近的股票行情"):
            assert bare_handler._web_search_allowed(msg) is False, msg

    def test_research_and_policy_allowed(self, bare_handler):
        assert bare_handler._web_search_allowed("帮我查一下苹果公司的最新新闻") is True
        assert bare_handler._web_search_allowed("最近有什么政策变化") is True

    def test_local_fortune_blocked(self, bare_handler):
        """T105 负例 gate：运势类本地问题不因白名单/时效词误放行。"""
        for msg in ("今年运势如何", "属猴的人今年运势怎么样", "我明年财运怎么样"):
            assert bare_handler._web_search_allowed(msg) is False, msg

    def test_entity_qa_now_allowed_without_whitelist_word(self, bare_handler):
        """原关键词硬门控漏放行的实体 QA（无「公司/行业…」白名单词）→ 放行。"""
        assert bare_handler._web_search_allowed("易宝支付靠谱吗") is True
        assert bare_handler._web_search_allowed("易宝支付这家公司怎么样") is True


class TestHintGate:
    def _hint(self, h, analysis, msg):
        return h._tool_loop_analysis_hint(analysis, msg)

    def test_hint_entity_qa_now_injected(self, bare_handler, monkeypatch):
        """chat 域 needs_search + 实体 QA（无旧白名单词）→ 注入工单引导（k11b 语义）。"""
        import src.rag.web_search as ws
        monkeypatch.setattr(ws, "web_search_available", lambda force=False: True)
        a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                            intent="career", needs_search=True)
        hint = self._hint(bare_handler, a, "易宝支付这家公司靠不靠谱")
        assert "实时信息" in hint and 'web_search' in hint

    def test_hint_finance_still_silent(self, bare_handler):
        a = MessageAnalysis(needs_soothe=False, soothe_text="", emotion_label=None,
                            intent="career", needs_search=True)
        assert "实时信息" not in self._hint(bare_handler, a, "今天股市行情怎么样")
        assert "web_search" not in self._hint(bare_handler, a, "今天股市行情怎么样")


class TestRateAndReuseGuards:
    def test_rate_ok_then_blocked(self, bare_handler):
        assert bare_handler._search_rate_ok("u_rt") is True
        assert bare_handler._search_rate_ok("u_rt") is True
        assert bare_handler._search_rate_ok("u_rt") is True
        assert bare_handler._search_rate_ok("u_rt") is False  # 第 4 次被拦
        assert bare_handler._search_rate_ok("u_other") is True  # 按用户隔离

    def test_same_query_normalization(self, bare_handler):
        assert bare_handler._same_search_query("易宝支付", "易宝支付 ") is True
        assert bare_handler._same_search_query("易宝支付", "易宝支付吗") is True
        assert bare_handler._same_search_query("易宝支付", "易宝支付公司") is False

    def test_domain_parse(self, bare_handler):
        text = ("https://www.yeepay.com/a（来源） https://WWW.ZHIHU.com/q/1 "
                "https://www.zhihu.com/q/2 https://cn.bing.com/x https://a.b.cn/p")
        out = bare_handler._parse_result_domains(text)
        assert out == ["yeepay.com", "zhihu.com", "a.b.cn"]

    def test_tool_web_search_reuses_grounded_query(self, bare_handler, monkeypatch):
        """引擎域已自动检索过同 query → executor 直取首查结果，不二次真实检索。"""
        calls = _offline_web(monkeypatch)
        bare_handler.__dict__.setdefault("_citations", {})["u_reuse"] = []
        bare_handler.__dict__.setdefault("_turn_grounded", {})["u_reuse"] = {
            "entity": "易宝支付", "query": "易宝支付", "ok": True,
            "text": "[1] 易宝支付（https://www.yeepay.com/）\n    第三方支付。",
            "domains": ["yeepay.com"]}
        tr = bare_handler._tool_web_search("易宝支付", "u_reuse")
        assert tr.ok is True and calls == []
        assert "易宝支付" in tr.text
        # 不同 query 不命中复用 → 真实执行
        bare_handler.__dict__.setdefault("_search_ticks", {})
        tr2 = bare_handler._tool_web_search("易宝支付 融资", "u_reuse")
        assert calls and tr2.ok is True

    def test_tool_web_search_rate_gate(self, bare_handler, monkeypatch):
        """频控护栏：执行侧超限 → 受限降级占位（不静默不编造）。"""
        calls = _offline_web(monkeypatch)
        for _ in range(bare_handler._SEARCH_RATE_LIMIT):
            assert bare_handler._search_rate_ok("u_rg") is True
        bare_handler.__dict__.setdefault("_citations", {})["u_rg"] = []
        tr = bare_handler._tool_web_search("易宝支付", "u_rg")
        assert tr.ok is False and "过于频繁" in tr.text
        assert calls == []  # 未发起真实检索

    def test_ground_search_ok_path(self, bare_handler, monkeypatch):
        """引擎域自动检索命中：注入块/实体/域名 + citations 注册 type=web。"""
        calls = _offline_web(monkeypatch)
        bare_handler._citations["u_g1"] = []
        analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="career",
                                   needs_search=False)
        g = bare_handler._engine_domain_ground_search(
            "易宝支付这家公司靠不靠谱", "u_g1", analysis)
        assert g["skip"] is False and g["ok"] is True
        assert g["entity"] == "易宝支付" and g["query"] == "易宝支付"
        assert "yeepay.com" in g["domains"] and "zhihu.com" in g["domains"]
        assert "【网络检索结果】" in g["block"]
        assert "[1]" in g["block"] and "易宝支付" in g["block"]
        assert calls == ["易宝支付"]
        web_items = [c for c in bare_handler._citations["u_g1"]
                     if c.get("type") == "web"]
        assert len(web_items) == 2
        # 复用表已记录：同 query 工单不二次检索
        tr = bare_handler._tool_web_search("易宝支付", "u_g1")
        assert tr.ok is True and calls == ["易宝支付"]

    def test_ground_search_empty_degraded(self, bare_handler, monkeypatch):
        """检索无结果 → 降级注（graceful，非甩锅），不注册 web 引用。"""
        _offline_web(monkeypatch, results=[])
        bare_handler._citations["u_g2"] = []
        analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="career")
        g = bare_handler._engine_domain_ground_search("易宝支付靠谱吗", "u_g2", analysis)
        assert g["skip"] is False and g["ok"] is False
        assert "公开信息" in g["block"] and "官方渠道" in g["block"]
        assert not [c for c in bare_handler._citations["u_g2"]
                    if c.get("type") == "web"]

    def test_ground_search_rate_limited(self, bare_handler, monkeypatch):
        """频控超限 → 降级注 + 零真实检索。"""
        calls = _offline_web(monkeypatch)
        for _ in range(bare_handler._SEARCH_RATE_LIMIT):
            bare_handler._search_rate_ok("u_g3")
        analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="career")
        g = bare_handler._engine_domain_ground_search("易宝支付怎么样", "u_g3", analysis)
        assert g["skip"] is False and g["ok"] is False
        assert "频繁" in g["block"] and calls == []

    def test_ground_search_local_skipped(self, bare_handler, monkeypatch):
        _offline_web(monkeypatch)
        analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="career")
        g = bare_handler._engine_domain_ground_search("今年运势如何", "u_g4", analysis)
        assert g.get("skip") is True

    def test_ground_llm_signal_fires(self, bare_handler, monkeypatch):
        """无后缀实体 + 分析器 needs_search → 触发（query=原句精简）。"""
        calls = _offline_web(monkeypatch)
        bare_handler._citations["u_g5"] = []
        analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="career",
                                   needs_search=True)
        g = bare_handler._engine_domain_ground_search(
            "最近开的那家中医馆靠谱吗", "u_g5", analysis)
        assert g["skip"] is False and calls  # 发起过检索
        assert "中医馆" in g["query"]


# ============================================================
# D/E：process() 全链路（T104 实体 QA 正例 / T105 本地负例）
# ============================================================

def _polish_reply_capture(monkeypatch):
    """打桩 deepseek_anthropic_completion：返回固定润色稿并捕获 system prompt。"""
    import src.llm.client as llm_client
    captured = {}

    def fake_completion(api_key, messages, **kw):
        captured["messages"] = messages
        return _POLISH_FIXED_REPLY

    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake_completion)
    return captured


_POLISH_FIXED_REPLY = (
    "易宝支付是一家第三方支付公司，做这行跟你的八字合不合还得细看，"
    "不过公司口碑这块你可以再多问问内部的人。"
)


def _engine_handler(db_path, monkeypatch, user_id):
    """真实引擎主链装配（行动建议并行线程关闭——建议卡非本批对象）。"""
    import src.bot.handler as handler_mod
    from src.engines.bazi import BaziEngine
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    monkeypatch.setattr(handler_mod, "HAS_ADVISOR_V2", False)
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, _ = _make_handler(db_path, engine=BaziEngine(), llm=llm,
                               session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的。"
    _seed_person(db_path, user_id)
    return handler


class TestProcessT104EntityQA:
    def test_entity_qa_triggers_search_with_sources(self, monkeypatch):
        """行 54 同款：career 意图 + 「易宝支付这家公司靠不靠谱」→
        自动检索（1 次）+ 润色上下文注入 + 回复带来源尾注 + 无甩锅/JSON。"""
        import src.bot.handler as handler_mod
        from src.rag import web_search as ws_mod
        monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
        monkeypatch.setattr(ws_mod, "web_search_available", lambda force=False: True)
        search_calls = _offline_web(monkeypatch)

        db_path = _make_db_path()
        handler = _engine_handler(db_path, monkeypatch, "eval_user_t104")
        captured = _polish_reply_capture(monkeypatch)
        _patch_intent(handler, "career")

        reply = handler.process("易宝支付这家公司靠不靠谱", "eval_user_t104",
                                session_id="T104")

        # ① 触发 web_search 且 query=实体名
        assert search_calls == ["易宝支付"], search_calls
        # ② 检索结果注入润色上下文（system 含结果块与使用要求）
        sys_text = "".join(m.get("content", "") for m in captured["messages"]
                           if m.get("role") == "system")
        assert "【网络检索结果】" in sys_text
        assert "禁止凭记忆编造" in sys_text
        assert "以官方渠道为准" in sys_text
        # 已自动检索 → 不再硬性要求 LLM 先输出搜索工单（防双搜）
        assert "先输出一次" not in sys_text
        assert "先输出" not in sys_text
        # ③ 回复含来源痕迹（LLM 漏写 → 确定性尾注兜底）
        assert "易宝支付" in reply
        assert "来源" in reply
        assert "zhihu.com" in reply or "yeepay.com" in reply
        # ④ 无甩锅句 / 无 JSON / 无工具回显
        for bad in ("你自己查", "自己查证", "你自己查证", "实时信息暂不可用",
                    "web_search", "<tool_calls>", "{", "}"):
            assert bad not in reply, (bad, reply)
        # ⑤ 引用注册含 type=web（前端来源抽屉数据）
        web_items = [c for c in handler._citations.get("eval_user_t104", [])
                     if c.get("type") == "web"]
        assert web_items and "yeepay.com" in web_items[0].get("url", "")

    def test_entity_qa_empty_results_graceful(self, monkeypatch):
        """检索无结果 → 降级注注入润色上下文；不崩、不甩锅、不补来源尾注。"""
        import src.bot.handler as handler_mod
        from src.rag import web_search as ws_mod
        monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
        monkeypatch.setattr(ws_mod, "web_search_available", lambda force=False: True)
        search_calls = _offline_web(monkeypatch, results=[])

        db_path = _make_db_path()
        handler = _engine_handler(db_path, monkeypatch, "eval_user_t104b")
        captured = _polish_reply_capture(monkeypatch)
        _patch_intent(handler, "career")

        reply = handler.process("易宝支付这家公司怎么样", "eval_user_t104b",
                                session_id="T104b")
        assert search_calls == ["易宝支付"]
        sys_text = "".join(m.get("content", "") for m in captured["messages"]
                           if m.get("role") == "system")
        assert "公开渠道未找到有效结果" in sys_text
        assert "以官方渠道为准" in sys_text
        assert "推诿让用户自己去查证" in sys_text
        assert reply and "{" not in reply
        # 检索未命中 → 无 ok → 不补来源尾注（也不谎称有来源）
        assert "信息来源：" not in reply


class TestProcessT105LocalZeroSearch:
    @pytest.mark.parametrize("msg,intent", [
        ("今年运势如何", "bazi"),
        ("我明年财运怎么样", "career"),
        ("这个月适合搬家吗", "bazi"),
        # k11b-r1（P1-A）：X运族 + 最近前缀（timely 误触发修复）引擎域零搜索
        ("最近工作运怎么样", "bazi"),
    ])
    def test_local_fortune_never_searches(self, monkeypatch, msg, intent):
        """运势/财运/择日类本地计算问题全链路零搜索。"""
        import src.bot.handler as handler_mod
        from src.rag import web_search as ws_mod
        monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
        monkeypatch.setattr(ws_mod, "web_search_available", lambda force=False: True)
        search_calls = _offline_web(monkeypatch)

        db_path = _make_db_path()
        handler = _engine_handler(db_path, monkeypatch, "eval_user_t105")
        captured = _polish_reply_capture(monkeypatch)
        # needs_search=True（LLM 误报场景）：本地锚仍硬否决
        _patch_intent(handler, intent, needs_search=True)

        reply = handler.process(msg, "eval_user_t105", session_id="T105")
        assert search_calls == [], (msg, search_calls)
        assert reply and "{" not in reply
        sys_text = "".join(m.get("content", "") for m in captured["messages"]
                           if m.get("role") == "system")
        assert "【网络检索结果】" not in sys_text
        assert "先输出一次" not in sys_text  # search_hint 未被误触发
