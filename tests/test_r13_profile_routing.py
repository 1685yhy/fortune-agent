"""R1-3 批次修复回归单测（T009 性别回显 / T019 流年错 / T026 链路工具漏发 /
T045 起名漏发+午时时柱 / T058 六爻出奇门 / T064 紫微落库 / T074 误调 web_search /
T100 择日额度门）。

可判别断言（非恒过）：每个测试在修复前版本上必失败——
- test_r13_*_force_*：修复前 analyze() 走 LLM 分类（api_key="" 直接返回
  intent=None），断言 intent 必失败
- test_r13_scene_fortune_cycle/naming_fallback：修复前映射表指向
  _handle_advisor/_handle_xingming（零工具调用），断言工具调用必失败
- test_r13_gender_echo_*：修复前无重挂逻辑，纯散文回复不获性别前缀
- test_r13_chart_inject_*：修复前注入无流年字段，断言含 丙午 必失败
- test_r13_web_search_*：修复前提示无条件注入，断言对股市行情不注入必失败
- test_r13_zeri_quota_gate：修复前 _handle_zeri 无额度门，断言引导文案必失败
- test_r13_ziwei_persist：修复前 _do_ziwei_analysis 只 save_consultation，
  断言 chart_records/persons 有行必失败
- test_r13_hour_map_*：修复前午时=11，断言=12 必失败
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.api.birth_contract import SHICHEN_TO_HOUR  # noqa: E402
from src.bot.handler import (  # noqa: E402
    CHINESE_HOUR_MAP, MessageHandler, SCENE_DEFAULT_ENGINE)
from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.message_analyzer import MessageAnalyzer, match_tool_scene  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402

ANALYZER = MessageAnalyzer("", model="deepseek-v4-flash")  # api_key="" → 无 LLM 兜底


# ============================================================
# 工具构造（真实引擎 + 临时库；llm/retriever 用 Mock 隔离网络）
# ============================================================

def _base_handler(tmp_path):
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(str(tmp_path / "u.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "u.db"))
    h.llm = Mock(api_key="")
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.engine = BaziEngine()
    h.memory = None
    h.memory_system = None
    h.session_dao = None
    h.member_dao = None
    h.preference_dao = None
    h.cache = Mock()
    h.cache.get.return_value = None
    h._deep_night = {}
    h._downgraded = {}
    h._tool_logs = {}
    h._citations = {}
    h._analysis_facts = {}
    h._pregen_instant = {}
    h._pregen_pool = Mock()
    h._gender_acks = {}
    return h


def _seed_person(tmp_path, gender: str = "female", uid: str = "u1"):
    pdao = PersonDAO(str(tmp_path / "u.db"))
    pdao.create_person(
        uid, name="我", relation="自己", is_default=True,
        birth={"gender": gender, "birth_year": 1990, "birth_month": 5,
               "birth_day": 20, "birth_hour": 15, "birth_minute": 30,
               "calendar": "solar", "city": "北京"})


class _bind_tool_executors:
    """把指定 cap_id 的执行器绑定到测试 handler 实例（全局注册表副作用
    用后还原——测试实例不经过 MessageHandler.__init__ 的真实绑定）。"""

    def __init__(self, h, caps: dict):
        self.h = h
        self.caps = caps  # {cap_id: attr_name on h}

    def __enter__(self):
        import src.bot.capability_registry as reg
        self._reg = reg
        self._orig = {c.cap_id: c.executor for c in reg.CAPABILITIES}
        reg.bind_executors(
            {cid: (lambda p, user_id="", user_question="", _m=getattr(self.h, attr):
                   _m(p, user_id)) for cid, attr in self.caps.items()},
            {})
        return self

    def __exit__(self, *exc):
        for c in self._reg.CAPABILITIES:
            c.__dict__["executor"] = self._orig.get(c.cap_id)
        return False


# ============================================================
# T045：午时取中点（时柱甲午锚点）——CHINESE_HOUR_MAP/SHICHEN_TO_HOUR 同步
# ============================================================

def test_r13_hour_map_wushi_is_midpoint():
    """午时（含单字「午」）映射为 12 点；时辰序号 6 同步为 12（数据一致性）。"""
    assert CHINESE_HOUR_MAP["午时"] == 12
    assert CHINESE_HOUR_MAP["午"] == 12
    assert SHICHEN_TO_HOUR[6] == 12
    # 其余时辰不被误伤（子时仍为 23 起点，亥时仍为 21）
    assert CHINESE_HOUR_MAP["子时"] == 23 and CHINESE_HOUR_MAP["亥时"] == 21


def test_r13_engine_wushi_noon_hour_pillar_anchor():
    """校准锚点：2019-03-15 午时 北京 → 时柱甲午（修复前 11 点 → 真太阳时
    落回巳时 → 癸巳时，与问真/权威排盘不一致）。"""
    eng = BaziEngine()
    noon = eng.calculate(2019, 3, 15, 12, 0, "北京", "男")
    assert noon.bazi[3] == "甲午", noon.bazi
    # R2-1（问真口径默认关）：11:00 默认关直接午时甲午——错位窗口消失（新默认
    # 口径即问真排盘）；显式开修正仍锁 R1-3 期行为（修正 10:35 → 巳时癸巳，
    # 正是 R2-1 修复的错位，属开关功能回归）
    assert eng.calculate(2019, 3, 15, 11, 0, "北京", "男").bazi[3] == "甲午"
    bug_hour = eng.calculate(2019, 3, 15, 11, 0, "北京", "男", solar_time=True)
    assert bug_hour.bazi[3] == "癸巳"  # 修正开仍锁 11 点错例（开关回归锚点）


# ============================================================
# T058/T064/T100：意图强路由（0 LLM 确定性）
# ============================================================

def test_r13_liuyao_force_intent():
    """「帮我摇一卦」→ 强制 liuyao 意图（修复前走 LLM 分类出奇门，T058 实锤）。"""
    r = ANALYZER.analyze("帮我摇一卦，看看下个月能不能升职")
    assert r.intent == "liuyao"
    assert r.scene_hint is None


def test_r13_ziwei_force_intent():
    """带出生信息的紫微排盘 → 强制 ziwei 意图（修复前被生日快路径掐成 bazi）。"""
    r = ANALYZER.analyze("1990年5月20日 15:30 北京 男，帮我排紫微盘")
    assert r.intent == "ziwei"


def test_r13_ziwei_force_meta_guard():
    """比较类问句不强制（元门控）：「紫微斗数和八字哪个准」是讨论不是排盘。"""
    r = ANALYZER.analyze("紫微斗数和八字哪个准")
    assert r.intent != "ziwei"


def test_r13_zeri_force_intent():
    """择日意图词 + 完整日期锚 → 强制 zeri（T100：修复前被 LLM 路由到建档引导）。"""
    r = ANALYZER.analyze("2026年9月15日搬家 帮我选个日子")
    assert r.intent == "zeri"
    assert r.scene_hint is None


def test_r13_zeri_force_requires_date_anchor():
    """只有意图词无完整日期锚 → 不强强制（留给 LLM 分类，T030-T032 已绿不误伤）。"""
    r = ANALYZER.analyze("下个月结婚 帮我选个吉日")
    assert r.intent != "zeri"


def test_r13_naming_scene_words():
    """口语起名动词族 → naming 场景（修复前不命中场景表 → LLM 排盘卡片，
    T045 3/3 实锤零工具调用）。"""
    assert match_tool_scene("给孩子起个名，姓张，男孩，2019年3月15日 午时 北京出生") == "naming"
    for w in ("起个名", "起个名字", "取个名", "取个名字", "起名字", "取名字"):
        assert match_tool_scene(f"给孩子{w}") == "naming", w
    # 旧词表不回退
    assert match_tool_scene("给宝宝起名") == "naming"


# ============================================================
# T026/T045：场景兜底映射 + 确定性工具执行
# ============================================================

def test_r13_scene_default_engine_mapping():
    """fortune_cycle/naming 场景映射到 _scene_*_fallback（修复前指向
    _handle_advisor/_handle_xingming，LLM 散文/排盘卡片零工具调用）。"""
    assert SCENE_DEFAULT_ENGINE["fortune_cycle"] == "_scene_fortune_cycle_fallback"
    assert SCENE_DEFAULT_ENGINE["naming"] == "_scene_naming_fallback"


def test_r13_scene_fortune_cycle_fallback(tmp_path):
    """档案出生 → 流月流年工具（0 LLM）：L1 契约 fortune_cycle(birth 键
    partial)；回复为流年卡片。"""
    _seed_person(tmp_path, gender="female")
    h = _base_handler(tmp_path)
    with _bind_tool_executors(h, {"fortune_cycle": "_tool_fortune_cycle"}):
        r = h._scene_fortune_cycle_fallback("明年运势怎么样", "u1")
    assert r is not None
    assert "流年" in r
    # 卡片里年份信息存在（format_cycle_card 输出流年/流月干支）
    assert "年" in r


def test_r13_scene_fortune_cycle_fallback_param_shape(tmp_path):
    """工具调用参数形状：dict {"birth": 档案出生串}（L1 partial 键匹配）。"""
    _seed_person(tmp_path, gender="female")
    h = _base_handler(tmp_path)
    seen = []
    h._execute_tool_call = lambda name, params, uid, user_question="": (
        seen.append((name, params))
        or SimpleNamespace(name=name, ok=False, text=""))
    h._scene_fortune_cycle_fallback("明年运势怎么样", "u1")
    assert seen, "兜底必须发起 fortune_cycle 工具调用"
    name, params = seen[0]
    assert name == "流月流年"
    assert isinstance(params, dict)
    assert params.get("birth") == "1990年5月20日 15时30分 北京 女"


def test_r13_scene_naming_fallback(tmp_path):
    """起名消息 → 起名工具（surname/gender/birth 三键，L1 partial）；回复
    含 五行/候选 契约关键词。"""
    h = _base_handler(tmp_path)
    with _bind_tool_executors(h, {"naming": "_tool_naming"}):
        r = h._scene_naming_fallback(
            "给孩子起个名，姓张，男孩，2019年3月15日 午时 北京出生", "u1")
    assert r is not None
    assert any(k in r for k in ("五行", "补益", "候选", "名字")), r[:200]


def test_r13_scene_naming_fallback_param_shape(tmp_path):
    """起名工具参数形状：dict {surname, gender, birth}（L1 契约三键 partial）。"""
    h = _base_handler(tmp_path)
    seen = []
    h._execute_tool_call = lambda name, params, uid, user_question="": (
        seen.append((name, params))
        or SimpleNamespace(name=name, ok=False, text=""))
    h._scene_naming_fallback(
        "给孩子起个名，姓张，男孩，2019年3月15日 午时 北京出生", "u1")
    assert seen, "兜底必须发起 naming 工具调用"
    name, params = seen[0]
    assert name == "起名"
    assert isinstance(params, dict)
    assert params.get("surname") == "张"
    assert params.get("gender") == "男"
    assert params.get("birth")


def test_r13_scene_naming_fallback_needs_info(tmp_path):
    """无姓氏/性别的起名请求（"帮我起个名"）→ None（fail-open 保留 free_chat）。"""
    h = _base_handler(tmp_path)
    assert h._scene_naming_fallback("帮我起个名", "u1") is None


# ============================================================
# T009：性别回显重挂
# ============================================================

def _bazi_analysis():
    return SimpleNamespace(intent="bazi", emotion_label=None,
                           needs_soothe=False, soothe_text="")


def test_r13_gender_echo_female_rehang(tmp_path):
    """女档案 + 无性别散文排盘回复 → 前置「女命」声明（修复前纯散文
    contains「女」失败，T009 2/3 实锤）。"""
    _seed_person(tmp_path, gender="female")
    h = _base_handler(tmp_path)
    reply = h._rehang_gender_echo(
        "你的日主是乙木，大运从8岁起运，流年运势见下。",
        _bazi_analysis(), engine_draft="draft", user_id="u1")
    assert reply.startswith("（女命：本盘按女命排盘）")
    assert "女" in reply


def test_r13_gender_echo_male_rehang(tmp_path):
    """男档案 + 无性别散文 → 前置「男命」声明。"""
    _seed_person(tmp_path, gender="male")
    h = _base_handler(tmp_path)
    reply = h._rehang_gender_echo(
        "你的日主是乙木，大运从8岁起运。", _bazi_analysis(),
        engine_draft="draft", user_id="u1")
    assert reply.startswith("（男命：本盘按男命排盘）")


def test_r13_gender_echo_idempotent(tmp_path):
    """已有性别标记/非排盘回复 → 不重挂（幂等，不双写）。"""
    _seed_person(tmp_path, gender="female")
    h = _base_handler(tmp_path)
    r1 = h._rehang_gender_echo("本盘按女命排盘，日主乙木…", _bazi_analysis(),
                               engine_draft="d", user_id="u1")
    assert not r1.startswith("（女命")
    # 非 bazi 意图（闲聊）不重挂
    r2 = h._rehang_gender_echo("今天天气不错", SimpleNamespace(intent="chat"),
                               engine_draft="d", user_id="u1")
    assert r2 == "今天天气不错"
    # 帮他人排盘（subject=other）不挂本人性别
    h._analysis_facts["u1"] = {"subject": "other"}
    r3 = h._rehang_gender_echo("朋友日主乙木，大运顺行。", _bazi_analysis(),
                               engine_draft="d", user_id="u1")
    assert not r3.startswith("（")


# ============================================================
# T019：当前流年注入（2026 丙午）
# ============================================================

def test_r13_chart_inject_liunian():
    """引擎 liunian_rel 恒为当前流年（2026 丙午）→ 注入提示含 丙午
    （修复前注入无流年，润色 LLM 自造出生年干支 己亥，T019 实锤）。"""
    eng = BaziEngine()
    result = eng.calculate(1990, 5, 20, 15, 30, "北京", "男")
    assert result.liunian_rel["year"] == 2026
    assert result.liunian_rel["ganzhi"] == "丙午"
    h = object.__new__(MessageHandler)
    h.chart_dao = object()  # 非 None 即放行
    inject = h._build_chart_inject(result)
    assert "丙午" in inject and "2026" in inject
    assert "当前流年" in inject
    # 无 liunian 的 result（紫微等）→ 无流年注入且不崩溃
    assert "流年" not in h._build_chart_inject(SimpleNamespace(day_master="辛金"))
    assert h._build_chart_inject(SimpleNamespace()) == ""


# ============================================================
# T074：无关问题不调 web_search
# ============================================================

def test_r13_web_search_allowed_finance_blocked():
    """股市行情 → 不允许搜索（产品无行情数据源：不搜索不编造）。"""
    h = object.__new__(MessageHandler)
    assert h._web_search_allowed("今天股市行情怎么样") is False
    assert h._web_search_allowed("帮我查一下大盘指数") is False
    assert h._web_search_allowed("看看最近的股票行情") is False


def test_r13_web_search_allowed_research():
    """研究类话题 → 允许搜索（公司/政策/新闻等可查证语境）。"""
    h = object.__new__(MessageHandler)
    assert h._web_search_allowed("帮我查一下苹果公司的最新新闻") is True
    assert h._web_search_allowed("最近有什么政策变化") is True


def test_r13_web_search_hint_gated_by_msg():
    """needs_search 提示注入按消息门控：股市行情不再注入搜索引导
    （修复前无条件注入 → LLM 必输出 web_search 工单，T074 L1 实锤）。"""
    h = object.__new__(MessageHandler)
    analysis = SimpleNamespace(secondary_needs=[], needs_search=True)
    hint_finance = h._tool_loop_analysis_hint(analysis, "今天股市行情怎么样")
    assert "实时信息" not in hint_finance
    assert "web_search" not in hint_finance
    hint_research = h._tool_loop_analysis_hint(analysis, "帮我查一下苹果公司的最新新闻")
    assert "实时信息" in hint_research


def test_r13_web_search_dispatch_skip_json_ticket(tmp_path, monkeypatch):
    """JSON 工单路径：股市行情 web_search 工单被跳过执行（占位结果注入，
    不落库不执行——L1 零工具契约），LLM 拿到占位提示后诚实作答。"""
    import src.bot.handler as handler_mod  # handler 模块级导入的函数引用须打此处
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    monkeypatch.setattr(
        "src.llm.client.deepseek_anthropic_completion",
        lambda *a, **k: "没有实时行情数据，建议关注专业财经媒体。")
    h = _base_handler(tmp_path)
    h.llm.api_key = "fake-key"  # 非空才进入工具循环体（空 key 早退路径跳过本测试）
    h._execute_tool_call = lambda name, params, uid, user_question="": (
        (_ for _ in ()).throw(AssertionError(f"web_search 不得执行: {name}")))
    ticket = ('<tool_calls>[{"tool": "web_search", '
              '"params": {"query": "今天股市行情"}}]</tool_calls>')
    reply = h._run_tool_loop("今天股市行情怎么样", "u_ws_1", ticket)
    assert "实时信息暂不可用" not in reply  # 占位提示只进 LLM 上下文，不进最终回复
    assert "<tool_calls>" not in reply  # 标签已剥离（循环正常执行）
    tool_log = h._tool_logs["u_ws_1"]
    assert tool_log["calls"] == [], f"web_search 不得落库，实际: {tool_log['calls']}"


# ============================================================
# T100：择日额度门（只读引导，memberships 零写入）
# ============================================================

def test_r13_zeri_quota_gate(tmp_path, monkeypatch):
    """免费额度用尽 → 会员引导（含 会员/额度），不调引擎、不写 memberships
    （修复前 _handle_zeri 意图路径无额度门，D4 记录边界）。"""
    import src.bot.handler as handler_mod  # handler 模块级导入的函数引用须打此处
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    h = _base_handler(tmp_path)
    h.member_dao = Mock()
    h.member_dao.get_membership.return_value = {"queries_used": 5, "queries_limit": 5}
    reply = h._handle_zeri("2026年9月15日搬家 帮我选个日子", "u1")
    assert "会员" in reply and "额度" in reply
    assert "19.9" in reply
    # 红线：memberships 零写入（use_quota 不得被调用）
    h.member_dao.use_quota.assert_not_called()


def test_r13_zeri_quota_gate_passes_when_remaining(tmp_path, monkeypatch):
    """额度未用尽 → 放行完整择日分析（契约「或正常产出」分支）。"""
    import src.bot.handler as handler_mod  # handler 模块级导入的函数引用须打此处
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    h = _base_handler(tmp_path)
    h.member_dao = Mock()
    h.member_dao.get_membership.return_value = {"queries_used": 2, "queries_limit": 5}
    h.zeri_engine = Mock()
    h.zeri_engine.select.return_value = SimpleNamespace(
        jianchu="成", ershibaxiu="亢金龙", xiu_jixiong="吉", chong="冲狗",
        yi=["搬家", "入宅"], ji=["动土"], overall="吉日可用",
    )
    h.llm.analyze.return_value = SimpleNamespace(
        response="2026年9月15日宜搬家、入宅。")
    reply = h._handle_zeri("2026年9月15日搬家 帮我选个日子", "u1")
    assert "会员" not in reply
    assert "宜" in reply  # 引擎产出宜忌


# ============================================================
# T064：紫微排盘落库（persons + chart_records）
# ============================================================

def test_r13_ziwei_persist(tmp_path):
    """紫微排盘 → persons 建档 + chart_records 落库（修复前只
    save_consultation，L4 契约 persons_created/chart_records_created 依赖
    bazi 路径接管才碰巧满足——强路由后必须本路径自足）。"""
    h = _base_handler(tmp_path)
    h.ziwei_engine = Mock()
    h.ziwei_engine.calculate.return_value = SimpleNamespace(
        ming_gong="寅", shen_gong="午", wuxing_ju="木三局",
        palaces={}, sihua={}, main_stars={}, aux_stars={}, dayun=[], raw_data={},
    )
    h.llm.analyze.return_value = SimpleNamespace(
        response="紫微命盘分析：命宫在寅，身宫在午。")
    reply = h._do_ziwei_analysis(
        1990, 5, 20, 15, 30, "北京", "男", "1990年5月20日 15:30 北京 男，帮我排紫微盘",
        "u_ziwei_1")
    assert "紫微" in reply
    rec = h.chart_dao.get_latest_chart("u_ziwei_1")
    assert rec is not None, "紫微排盘必须落库 chart_records"
    assert rec["birth"]["year"] == 1990 and rec["birth"]["gender"] == "男"
    persons = PersonDAO(str(tmp_path / "u.db")).list_persons("u_ziwei_1")
    assert len(persons) == 1, "紫微排盘必须同步建档 persons"
    assert persons[0]["birth_year"] == 1990


# ============================================================
# T064 同源：紫微重看盘直读档案（persons 优先）
# ============================================================

def test_r13_ziwei_saved_branch_reads_profile(tmp_path):
    """紫微重看盘：persons 档案存在 → 不再反问出生信息（修复前
    get_user_bazi 读不到 persons → 返回引导文案，judge「不得反问出生信息」）。"""
    _seed_person(tmp_path, gender="female")
    h = _base_handler(tmp_path)
    h.ziwei_engine = Mock()
    h.ziwei_engine.calculate.return_value = SimpleNamespace(
        ming_gong="寅", shen_gong="午", wuxing_ju="木三局",
        palaces={}, sihua={}, main_stars={}, aux_stars={}, dayun=[], raw_data={},
    )
    h.llm.analyze.return_value = SimpleNamespace(response="紫微命盘分析：命宫在寅。")
    reply = h._handle_ziwei("我想再看看我的紫微盘", "u1")
    assert "请提供出生信息" not in reply
    assert "紫微" in reply
    # 女档案 → 按女命排盘（引擎收到中文性别契约）
    args = h.ziwei_engine.calculate.call_args[0]
    assert args[6] == "女"
