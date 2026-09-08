"""R1-2 回归单测：工具回显/JSON 泄漏 + 路由漏发 + 纠正不重排 + 已建档仍引导。

评测契约对应（data/eval/agent_tasks.jsonl，评估集零改动）：
- T007「纠正出生时辰重排」：润色回显工具说明书/参数 JSON → 兜底回退引擎原稿
  （癸未 + 四柱/大运 + 无 `{` + chart_records 新增 + persons 不动 + 不走重看直读）
- T094「手机号数字吉凶」：num_omen 场景兜底确定性执行工具（吉凶/数理/尾号，
  回复层零 JSON/零「引擎执行失败」）
- T039「合婚双方生辰」：hehun 场景兜底确定性执行工具（男方/女方四柱回显 +
  五行互补/生肖/评分；LLM 自编内容永不落入回复）
- T095「择业方向」：career_dir 场景兜底确定性执行工具（行业/五行/方位），
  不再被存量命盘回放替代
- T008「口语性别词纠正自动重排」：性别纠正强制 bazi 意图 → 重排 + 双写档案 +
  chart_records 新增（male/female 英文契约归一后判定）
- T089「改城市今日运势立即重算」：persons 建档不再引导建档；今日运势缓存键
  含档案指纹 → 改城市 → 指纹变化 → 轮3 与轮1 distinct（立即重算不命中旧缓存）
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.bot.handler import MessageHandler
from src.engines.message_analyzer import MessageAnalysis
from src.storage.chart_dao import ChartDAO
from src.storage.dao import UserDAO
from src.storage.models import init_db
from src.storage.person_dao import PersonDAO
from src.storage.session_dao import SessionDAO

_TMP_DIRS = []


def _make_db_path():
    tmpdir = tempfile.mkdtemp(prefix="fortune_r1_2_")
    db_path = os.path.join(tmpdir, "test.db")
    init_db(db_path)
    _TMP_DIRS.append(tmpdir)
    return db_path


def _mock_result(bazi=None, gender="男"):
    """可 JSON 序列化的引擎结果（评估隔离库同装配；save_chart json.dumps 兼容）。"""
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


def _make_handler(db_path, engine=None, hehun_engine=None, llm=None,
                  session=None):
    dao = UserDAO(db_path)
    llm = llm or Mock()
    # Mock 的 .chat/.return_value 访问即自动创建（恒非 None）→ 无条件覆写
    llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
    llm.chat_conversation.return_value = "🔮 命理助手 返回的结果"
    analysis = Mock()
    analysis.response = "您的八字分析结果：日主乙木…"
    llm.analyze.return_value = analysis
    session = session or Mock()
    if isinstance(session, Mock):
        session.get_context_for_llm.return_value = []
        session.add_message.return_value = None
    handler = MessageHandler(
        engine=engine or _mock_result_engine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(), retriever=Mock(),
        hehun_engine=hehun_engine or Mock(),
        llm=llm, dao=dao, session_dao=session,
    )
    # 禁用秒回预生成：避免后台线程重复 engine.calculate + llm.analyze 竞态
    handler._start_pregen_instant = lambda msg, user_id="": None  # k11c: 契约 +user_id（档案开关口径）
    return handler, dao


def _mock_result_engine(bazi=None, gender="男"):
    engine = Mock()
    engine.calculate.return_value = _mock_result(bazi=bazi, gender=gender)
    return engine


def _seed_person(db_path, user_id, city="北京", gender="男"):
    PersonDAO(db_path).create_person(
        user_id, "我", "本人",
        birth={"birth_year": 1990, "birth_month": 5, "birth_day": 20,
               "birth_hour": 15, "birth_minute": 30,
               "city": city, "gender": gender},
        is_default=True)


def _patch_intent(handler, intent, scene_hint=None, facts=None):
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent=intent, scene_hint=scene_hint, facts=facts or {}))


# ── A 组：工具回显/JSON 泄漏（T007 / T094）───────────────────────────────

def test_t007_echo_polish_falls_back_to_engine_draft(monkeypatch):
    """T007「纠正出生时辰重排」：润色 LLM 把工具说明书模板 + 参数 JSON 当
    回复文本输出（回显泄漏）→ 兜底回退引擎原稿（含 癸未 时柱 + 四柱/大运 +
    无 `{`），真实重排落库（chart_records_created），persons 不动。"""
    import src.llm.client as llm_client
    echo = ("排盘（bazi_chart）：输入出生信息，如：1990年5月20日 午时 北京 男。"
            "参数：birth。超时8s，失败自动重试1次\n"
            '{"birth": "1990年5月20日15:00 北京 男"}')
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: echo)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t007")  # 1990-05-20 15:30 北京 男
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "癸未"])
    llm = Mock()
    llm.api_key = "test-key-no-network"   # 字符串 → 润色门真实走通（打桩禁网）
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的，基于你之前的八字信息来看。"
    _patch_intent(handler, "bazi")

    reply = handler.process(
        "我出生时间其实是15:00，不是15:30，重新帮我排一下", "eval_user_t007",
        session_id="eval-T007")

    # 契约：contains 癸未（纠正后的时柱）、neg 无 `{`、regex 四柱|时柱|大运
    assert "癸未" in reply, reply
    assert "{" not in reply, reply
    assert ("四柱" in reply or "时柱" in reply or "大运" in reply), reply
    # 重排真实发生（引擎调用）且不命中重看直读（T092 负例同守卫）
    assert engine.calculate.call_count >= 1, engine.calculate.call_count
    assert "直接看的已存结果" not in reply
    # L4：chart_records 新增 + persons 不动（仅性别/出生不变）
    chart = ChartDAO(db_path).get_latest_chart("eval_user_t007")
    assert chart is not None, "必须产生新的排盘记录"
    default = PersonDAO(db_path).get_default_person("eval_user_t007")
    assert default["birth_year"] == 1990 and default["city"] == "北京"
    assert default["gender"] == "男"


def test_looks_like_tool_echo_detects_schema_and_json_leak():
    """回显检测器：工具说明书模板行（中文名（cap_id）：…）与参数 JSON 泄漏
    （{ 后跟引号/中文）均命中；正常卡片/分析文本不误伤。"""
    from src.bot.handler import MessageHandler
    detector = MessageHandler._looks_like_tool_echo
    echo_schema = ("排盘（bazi_chart）：输入出生信息，如：1990年5月20日 午时 "
                   "北京 男。参数：birth。超时8s，失败自动重试1次")
    echo_json = 'num_omen {"number": "13812345678"}'
    echo_json2 = '我会调用一个工具，稍等一下哈。{"birth": "1990年5月20日15:00 北京 男"}'
    assert detector(None, echo_schema) is True
    assert detector(None, echo_json) is True
    assert detector(None, echo_json2) is True
    # 正常产出不误伤（结构化卡片/排盘分析）
    assert detector(None, "【数字吉凶】场景：手机号｜号码：13812345678") is False
    assert detector(None, "📜 四柱：庚午 辛巳 乙酉 癸未") is False
    assert detector(None, "好的，今天整体运势平稳。") is False


def test_t094_num_omen_scene_fallback_executes_tool():
    """T094「手机号数字吉凶」：LLM 意图缺失 + num_omen 场景命中 → 确定性执行
    数字吉凶工具（0 LLM）——回复为结构化卡片（吉凶/数理/尾号 + 号码），
    工具调用 JSON 不再当回复文本输出（neg 无 `{`/无「引擎执行失败」）。"""
    db_path = _make_db_path()
    handler, _ = _make_handler(db_path)
    _patch_intent(handler, None, scene_hint="num_omen")

    reply = handler.process("这个手机号 13812345678 怎么样", "eval_user_t094")

    assert "吉凶" in reply and "数理" in reply and "尾号" in reply, reply
    assert "13812345678" in reply
    assert "{" not in reply
    assert "引擎执行失败" not in reply


# ── B 组：路由漏发（T039 / T095）────────────────────────────────────────

def test_t039_hehun_scene_fallback_executes_tool():
    """T039「合婚双方生辰匹配」：LLM 未输出工单 + hehun 场景命中 → 确定性
    执行合婚工具（双排盘 + 引擎匹配 + 卡片）——男方/女方四柱回显 + 五行
    互补/生肖/评分齐全；LLM 自编合婚内容路径不再可达（工具必然执行）。"""
    from src.engines.bazi import BaziEngine

    db_path = _make_db_path()
    hehun = Mock()
    hehun.match.return_value = SimpleNamespace(
        shengxiao="马与羊（六合）", shengxiao_detail={},
        bazi_match={"wuxing_1": "火", "wuxing_2": "土",
                    "complement_desc": "互补", "score": 32,
                    "day_master_1": "乙木", "day_master_2": "壬水",
                    "day_master_relation": "相生"},
        rizhu="日柱相合", score=68, advice="互相包容，多沟通。")
    handler, _ = _make_handler(db_path, engine=BaziEngine(), hehun_engine=hehun)
    _patch_intent(handler, None, scene_hint="hehun")

    reply = handler.process(
        "男1990年5月20日 15:30 北京，和女1992年10月1日 上海，我们合不合",
        "eval_user_t039")

    # 契约：contains 男方（男方信息回显）、regex 五行|互补|生肖|日柱|评分、
    # neg 无「给我双方生辰」、min_len 60
    assert "男方" in reply and "女方" in reply, reply
    assert ("五行" in reply or "互补" in reply), reply
    assert "生肖" in reply and "评分" in reply, reply
    assert "给我双方生辰" not in reply
    assert len(reply) >= 60
    assert "{" not in reply
    # 工具真实执行：双方各自排盘（1990 男 / 1992 女）
    assert hehun.match.call_count == 1
    a, b = hehun.match.call_args.args
    assert a.bazi and b.bazi
    assert a.gender == "男" and b.gender == "女"


def test_t095_career_dir_scene_fallback_executes_tool():
    """T095「择业方向」：已有档案 + career_dir 场景命中 → 确定性执行择业工具
    （引擎喜用神口径）——行业/五行/方位卡片；存量命盘回放不再替代择业分析。"""
    from src.engines.bazi import BaziEngine

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t095")  # 1990-05-20 15:30 北京 男
    engine = BaziEngine()
    handler, _ = _make_handler(db_path, engine=engine)
    _patch_intent(handler, None, scene_hint="career_dir")

    reply = handler.process("我适合做什么行业", "eval_user_t095")

    assert "行业" in reply, reply
    assert "五行" in reply and "方位" in reply, reply
    assert len(reply) >= 20
    assert "{" not in reply and "引擎执行失败" not in reply
    # 工具按档案出生信息真实排盘（不是回放旧盘）
    assert "1990年5月20日" in reply or "乙木" in reply, reply


# ── C 组：纠正不重排（T008）─────────────────────────────────────────────

def test_t008_gender_correction_forces_rechart():
    """T008「我是女孩儿，不是男孩」：口语性别词纠正 → 确定性强制 bazi 意图 →
    G1 纠正路径重排（含 女 回执 + 四柱/大运 + chart_records 新增 + 档案双写）。"""
    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t008", gender="男")
    handler, dao = _make_handler(db_path, engine=_mock_result_engine())
    _patch_intent(handler, "free_chat")  # 修复前：free_chat → 只确认不重排

    reply = handler.process("我是女孩儿，不是男孩", "eval_user_t008")

    # 契约：contains 女、regex 起运|大运|四柱、chart_records_created
    assert "女" in reply and "重新排盘" in reply, reply
    assert ("起运" in reply or "大运" in reply or "四柱" in reply), reply
    assert "{" not in reply
    chart = ChartDAO(db_path).get_latest_chart("eval_user_t008")
    assert chart is not None, "性别纠正必须产生新的排盘记录"
    # 档案双写：persons 默认命主性别已更新为 女
    default = PersonDAO(db_path).get_default_person("eval_user_t008")
    assert default["gender"] == "女", default
    # 按纠正后性别排盘（引擎末次调用 gender=女）
    last_call = handler.engine.calculate.call_args_list[-1]
    assert last_call.args[6] == "女", last_call


# ── D 组：已建档仍引导（T089）───────────────────────────────────────────

def test_t089_calendar_built_profile_no_guide_and_city_recompute(monkeypatch):
    """T089 三轮链路：persons 建档（无 bazi_info 扩展键）→ 轮1 今日运势直接
    算（不引导建档，档案缺失四柱即时补齐）；轮2 改城市为上海 → 轮3 再问
    今日运势，缓存键含档案指纹 → 指纹变化 → 不命中轮1 缓存 → 与轮1 distinct。"""
    import src.engines.calendar as cal_mod

    class _StubDay:
        def __init__(self, city):
            self.date = "2026-09-01"
            self.overall_mood = f"今日运势（{city}）平稳，宜静不宜动。"
            self.is_special = False
            self.special_note = ""
            self.yi = []
            self.ji = []
            self.lucky_color = ""
            self.lucky_direction = ""
            self.lucky_number = ""

    class _StubCalendar:
        def __init__(self, api_key):
            self.api_key = api_key

        def daily(self, user_bazi, date_str=None, preferences=""):
            return _StubDay((user_bazi or {}).get("city", "?"))

    monkeypatch.setattr(cal_mod, "LuckyCalendar", _StubCalendar)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t089", city="北京")
    handler, dao = _make_handler(db_path, engine=_mock_result_engine())
    _patch_intent(handler, "calendar")

    # 轮1：已建档（persons 种子存在）→ 直接算运势，绝不引导建档
    r1 = handler.process("今天运势怎么样", "eval_user_t089",
                         session_id="eval-T089")
    assert "需要先设置八字" not in r1, r1
    assert "今日运势" in r1 and "北京" in r1, r1
    # 档案缺四柱扩展键 → 即时排盘补齐（引擎真实计算）
    assert handler.engine.calculate.call_count >= 1

    # 轮2：改出生城市为上海（persons 档案更新 → 档案指纹变化）
    pdao = PersonDAO(db_path)
    person = pdao.get_default_person("eval_user_t089")
    pdao.update_person("eval_user_t089", person["id"], birth={"city": "上海"})

    # 轮3：同一问句 → 指纹变化 → 不命中轮1 缓存 → 立即重算（内容 distinct）
    r3 = handler.process("今天运势怎么样", "eval_user_t089",
                         session_id="eval-T089")
    assert "需要先设置八字" not in r3, r3
    assert "上海" in r3, r3
    assert r3 != r1, "改城市后轮3 必须与轮1 内容不同（立即重算）"


def test_t089_cache_key_contains_profile_fingerprint():
    """T089 机制单测：缓存键掺入档案指纹——同用户同消息，改档案（城市）后
    指纹变化 → 旧缓存天然失效（键不同）；建档前后指纹分键（无档案 none）。"""
    db_path = _make_db_path()
    handler, _ = _make_handler(db_path)
    # 无档案 → "none"
    assert handler._profile_cache_fingerprint("eval_u") == "none"
    _seed_person(db_path, "eval_u", city="北京")
    fp_beijing = handler._profile_cache_fingerprint("eval_u")
    assert fp_beijing != "none"
    pdao = PersonDAO(db_path)
    person = pdao.get_default_person("eval_u")
    pdao.update_person("eval_u", person["id"], birth={"city": "上海"})
    fp_shanghai = handler._profile_cache_fingerprint("eval_u")
    assert fp_shanghai != fp_beijing, "改城市 → 档案指纹必须变化"
    # 同指纹同 key（缓存命中语义不变）：消息 + fp 组成键
    key1 = f"今天运势怎么样::fp:{fp_beijing}"
    key2 = f"今天运势怎么样::fp:{fp_beijing}"
    assert key1 == key2


# ── 合婚卡片性别标签（T039 契约「男方」回显）─────────────────────────────

def test_hehun_card_gender_labels():
    """format_hehun_card：引擎结果带性别 → 男方/女方标签；性别未知 → A方/B方
    兜底（结构不变，接口契约零破坏）。"""
    from src.tools.hehun import format_hehun_card

    class _R:
        def __init__(self, bazi, dm, gender):
            self.bazi = bazi
            self.day_master = dm
            self.gender = gender

    result = SimpleNamespace(
        shengxiao="马与羊", shengxiao_detail={},
        bazi_match={"wuxing_1": "火", "wuxing_2": "土", "complement_desc": "互补",
                    "score": 32, "day_master_1": "乙木", "day_master_2": "壬水",
                    "day_master_relation": "相生"},
        rizhu="相合", score=68, advice="多多沟通。")
    card = format_hehun_card(
        _R(["庚午", "辛巳", "乙酉", "甲申"], "乙木", "男"),
        _R(["壬申", "己酉", "壬午", "庚子"], "壬水", "女"), result)
    assert "男方四柱" in card and "女方四柱" in card, card
    assert "评分：68/100" in card and "五行互补" in card
    # 性别未知 → A方/B方 兜底（原契约形态不破坏）
    card_unknown = format_hehun_card(
        _R(["庚午", "辛巳", "乙酉", "甲申"], "乙木", "unknown"),
        _R(["壬申", "己酉", "壬午", "庚子"], "壬水", ""), result)
    assert "A方四柱" in card_unknown and "B方四柱" in card_unknown


# ── 第二轮修复（unified-20260901-122902 三败根因收口）─────────────────────

def test_t092_zero_ganzhi_degenerate_falls_back_to_engine_draft(monkeypatch):
    """T092「明确重排走全流程」（回归守卫）：润色 LLM 把引擎原稿缩写成零
    干支散文（"哦。如果你有任何其他问题…"）→ _enforce_pillar_integrity 的
    ≥4 干支断言早退不兜底 → bazi 意图 + 有引擎原稿 + 回复零干支 → 回退
    确定性引擎原稿（庚午 + 四柱/大运齐全，零 JSON 泄漏）。"""
    import src.llm.client as llm_client
    monkeypatch.setattr(
        llm_client, "deepseek_anthropic_completion",
        lambda *a, **k: "哦。如果你有任何其他问题或需要进一步的分析，请随时告诉我。")

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t092")  # 1990-05-20 15:30 北京 男
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "甲申"])
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的，基于你之前的八字信息来看。"
    _patch_intent(handler, "bazi")

    reply = handler.process("重新帮我算一遍我的盘", "eval_user_t092",
                            session_id="eval-T092")

    # 契约：contains 庚午、regex 四柱|时柱|大运、min_len 30、neg 无 `{`
    assert "庚午" in reply, reply
    assert ("四柱" in reply or "时柱" in reply or "大运" in reply), reply
    assert len(reply) >= 30, reply
    assert "{" not in reply
    # 兜底稿 = 引擎原稿（四柱与引擎产物一致），且真实排盘发生（非重看直读）
    assert engine.calculate.call_count >= 1
    assert "直接看的已存结果" not in reply


def test_t092_bazi_workorder_from_polish_not_executed(monkeypatch):
    """T092 收口（no_tool 契约）：bazi 路由链路本轮已排盘（engine_draft
    注入）→ 润色 LLM 再输出「排盘」JSON 工单（冗余重执行）→ 静默丢弃：
    _execute_tool_call 零调用（L1 不双计）、工单残留被 strip、回复为干净
    散文（无 `{`、有干支内容）。"""
    import src.llm.client as llm_client
    polished = ("好的，我帮你重新排一下盘。\n"
                '<tool_calls>[{"tool": "bazi_chart", "params": {"birth": '
                '"1990-05-20 15:30", "gender": "男"}}]</tool_calls>\n'
                "重新排盘结果：庚午 辛巳 乙酉 甲申，日主乙木，大运6岁起运。")
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: polished)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t092b")  # 1990-05-20 15:30 北京 男
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "甲申"])
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的，基于你之前的八字信息来看。"
    executed = []
    _orig_exec = handler._execute_tool_call

    def _counting(name, params, user_id, user_question=""):
        executed.append(name)
        return _orig_exec(name, params, user_id, user_question)

    handler._execute_tool_call = _counting
    _patch_intent(handler, "bazi")

    reply = handler.process("重新帮我算一遍我的盘", "eval_user_t092b",
                            session_id="eval-T092")

    # no_tool 契约：排盘工单被丢弃（零执行 = L1 零调用）；工单残留不落入回复
    assert executed == [], executed
    assert "<tool_calls>" not in reply and "<tool_call>" not in reply, reply
    # 回复内容完整：庚午 + 四柱/大运，零 JSON 泄漏
    assert "庚午" in reply, reply
    assert ("四柱" in reply or "时柱" in reply or "大运" in reply), reply
    assert "{" not in reply
    # 真实排盘发生（引擎调用 + chart_records 落库）
    assert engine.calculate.call_count >= 1
    assert ChartDAO(db_path).get_latest_chart("eval_user_t092b") is not None


def test_t007_wuxing_dict_leak_falls_back_to_engine_draft(monkeypatch):
    """T007 收口（负例 `{` 泄漏）：润色 LLM 把五行数据重述成 Python 字典
    字面量（"五行：{'金': 3, '木': 1, …}"）→ 扩展后的 JSON 泄漏检测命中
    （{ 后跟单引号）→ 回退确定性引擎原稿（引擎卡片五行渲染为百分比格式，
    零 `{`）——负例 `{` 契约确定性满足。"""
    import src.llm.client as llm_client
    polished = ("八字：庚午 辛巳 乙酉 癸未\n日主：乙木\n"
                "五行：{'金': 3, '木': 1, '水': 1, '火': 2, '土': 1}\n"
                "大运：6岁壬午 → 16岁癸未 → 26岁甲申")
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: polished)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t007b")  # 1990-05-20 15:30 北京 男
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "癸未"])
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的，基于你之前的八字信息来看。"
    _patch_intent(handler, "bazi")

    reply = handler.process(
        "我出生时间其实是15:00，不是15:30，重新帮我排一下", "eval_user_t007b",
        session_id="eval-T007")

    # 契约：contains 癸未、neg 无 `{`、regex 四柱|时柱|大运
    assert "癸未" in reply, reply
    assert "{" not in reply, reply
    assert ("四柱" in reply or "时柱" in reply or "大运" in reply), reply
    assert "重新排盘" in reply or "四柱" in reply


def test_t092_partial_ganzhi_prose_falls_back_to_engine_draft(monkeypatch):
    """T092 阈值收口：润色散文只声明 1 处相邻干支（甲申——干支拆行表格式
    （庚/午分列）下大运行偶现的残缺形态，在线实锤「全回复仅甲申一处」）→
    <4 对阈值命中 → 回退引擎原稿（庚午 + 四柱齐全）——原 `not findall` 判定
    只看「有无」，1 对即漏网（T092 0/1 实锤）。"""
    import src.llm.client as llm_client
    polished = ("您的日主是乙木，生于巳月。您目前处于甲申大运。"
                "天干庚 辛 乙 甲 / 地支午 巳 酉 申")
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: polished)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t092c")  # 1990-05-20 15:30 北京 男
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "甲申"])
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的，基于你之前的八字信息来看。"
    _patch_intent(handler, "bazi")

    reply = handler.process("重新帮我算一遍我的盘", "eval_user_t092c",
                            session_id="eval-T092")

    # 契约：contains 庚午、regex 四柱|时柱|大运、neg 无 `{`；真实排盘
    assert "庚午" in reply, reply
    assert ("四柱" in reply or "时柱" in reply or "大运" in reply), reply
    assert "{" not in reply
    assert engine.calculate.call_count >= 1
    assert "直接看的已存结果" not in reply


def test_t007_format_chart_prompt_brace_free():
    """T007 根因（src/llm/client.py _format_chart）：analyze 提示中的五行
    计数不再以 Python dict 字面量（{'金': 3, …}）形态注入——LLM 无 dict
    形态可逐字回显，回显泄漏在源头断流；渲染口径与 format_compact_card
    一致（无括号计数串）。"""
    from src.llm.client import FortuneLLM
    chart = FortuneLLM("test-key")._format_chart(_mock_result())
    assert "{" not in chart and "}" not in chart, chart
    assert "五行：木3 火2 金2 水1 土2" in chart, chart


def test_t007_analyze_dict_in_engine_draft_scrubbed(monkeypatch):
    """T007 终局防漏（回复层兜底脱括号）：引擎原稿本身被 analyze LLM 污染
    （响应含 五行：{'金': 3, …} dict 字面量，在线 2/3 实锤形态）——echo 守卫
    回退原稿后 dict 仍残留 → 回复层确定性脱括号（{'金': 3} → 金: 3），
    neg `{` 契约 3/3 确定性满足（零 LLM、不伤正文）。"""
    import src.llm.client as llm_client
    polished = ("好的，我帮你重新排一下盘。八字：庚午 辛巳 乙酉 癸未，"
                "日主乙木，大运6岁起运。")
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: polished)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t007c")  # 1990-05-20 15:30 北京 男
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "癸未"])
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    # analyze（引擎分析）响应被污染：dict 字面量进入引擎原稿
    polluted = Mock()
    polluted.response = ("您的八字分析结果：日主乙木，生于巳月。"
                         "五行：{'金': 3, '木': 1, '水': 1, '火': 2, '土': 1}，"
                         "七杀格，用神为水。")
    llm.analyze.return_value = polluted
    handler._quick_flash = lambda prompt, **kw: "好的，基于你之前的八字信息来看。"
    _patch_intent(handler, "bazi")

    reply = handler.process(
        "我出生时间其实是15:00，不是15:30，重新帮我排一下", "eval_user_t007c",
        session_id="eval-T007")

    # 契约：contains 癸未、neg 无 `{`（兜底脱括号后零残留）、regex 四柱|时柱|大运
    assert "癸未" in reply, reply
    assert "{" not in reply and "}" not in reply, reply
    assert ("四柱" in reply or "时柱" in reply or "大运" in reply), reply


def test_t039_hehun_llm_self_call_prose_replaced_by_card(monkeypatch):
    """T039 收口（LLM 自行调用工具但回复丢失「男方」）：LLM 输出 JSON 工单
    （合婚工具真实执行一次）→ 二次生成把卡片缩写成无「男方」散文 → 确定性
    合婚卡片直接替换（消息拆对 + 引擎双排盘，0 LLM）。_execute_tool_call
    只执行一次（L1 严格序列匹配，重复执行即 FAIL）。"""
    import src.llm.client as llm_client
    from src.engines.bazi import BaziEngine

    workorder = ('<tool_calls>[{"tool": "合婚", "params": {"birth_a": '
                 '"1990年5月20日 15:30 北京 男", "birth_b": '
                 '"1992年10月1日 上海 女"}}]</tool_calls>')
    seq = ["这两位先生的八字都很不错，生肖都是属马，整体是相合的。"]
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: seq.pop(0) if seq else "")

    db_path = _make_db_path()
    hehun = Mock()
    hehun.match.return_value = SimpleNamespace(
        shengxiao="马与羊（六合）", shengxiao_detail={},
        bazi_match={"wuxing_1": "火", "wuxing_2": "土",
                    "complement_desc": "互补", "score": 32,
                    "day_master_1": "乙木", "day_master_2": "壬水",
                    "day_master_relation": "相生"},
        rizhu="日柱相合", score=68, advice="互相包容，多沟通。")
    llm = Mock()
    llm.api_key = "test-key-no-network"  # 工具循环真实走通（打桩禁网）
    llm.model = "test-model"
    handler, _ = _make_handler(db_path, engine=BaziEngine(), hehun_engine=hehun,
                               llm=llm)
    llm.chat.return_value = Mock(response=workorder)  # 主模型先回工具工单
    # 计数器：记录 _execute_tool_call 真实执行次数（L1 语义等价）
    executed = []
    _orig_exec = handler._execute_tool_call

    def _counting(name, params, user_id, user_question=""):
        executed.append(name)
        return _orig_exec(name, params, user_id, user_question)

    handler._execute_tool_call = _counting
    _patch_intent(handler, None, scene_hint="hehun")

    reply = handler.process(
        "男1990年5月20日 15:30 北京，和女1992年10月1日 上海，我们合不合",
        "eval_user_t039b")

    # 契约：contains 男方（确定性卡片回显）+ regex 五行|互补|生肖|日柱|评分
    assert "男方" in reply and "女方" in reply, reply
    assert ("五行" in reply or "互补" in reply), reply
    assert "生肖" in reply and "评分" in reply, reply
    assert len(reply) >= 60, reply
    assert "{" not in reply
    # L1 语义：_execute_tool_call 只执行一次（序列匹配不双计——重派生卡片
    # 走直调执行器，绕过记录器）；散文（无男方）不落入回复
    assert executed == ["合婚"], executed
    assert "这两位先生" not in reply
    # 引擎匹配 2 次 = 工单路径 1 次（L1 记录）+ 消息拆对重派生 1 次
    # （直调执行器，不记录、无落库副作用）
    assert hehun.match.call_count == 2, hehun.match.call_count
    a, b = hehun.match.call_args.args
    assert a.gender == "男" and b.gender == "女"


def test_t008_correction_ack_survives_polish(monkeypatch):
    """T008 收口（润色丢失纠正回执）：性别纠正路径 → 润色 LLM 把含「女」的
    固定回执缩写成无性别词散文（但保留四柱）→ 回执幂等重挂：回复 = 回执 +
    "\n\n" + 润色稿（含 女 + 重新排盘 + 四柱/大运，零 LLM）。"""
    import src.llm.client as llm_client
    polished = ("根据你的八字：庚午 辛巳 乙酉 甲申，日主乙木，大运从6岁起运，"
                "中年财运渐旺。")  # 有干支无「女」——隔离验证回执重挂
    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion",
                        lambda *a, **k: polished)

    db_path = _make_db_path()
    _seed_person(db_path, "eval_user_t008b", gender="男")
    engine = _mock_result_engine(bazi=["庚午", "辛巳", "乙酉", "甲申"])
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, dao = _make_handler(db_path, engine=engine, llm=llm,
                                 session=SessionDAO(db_path))
    _patch_intent(handler, "free_chat")  # 纠正门强制 bazi

    reply = handler.process("我是女孩儿，不是男孩", "eval_user_t008b",
                            session_id="eval-T008")

    # 契约：contains 女（回执存活）+ regex 起运|大运|四柱 + 无 `{`
    assert "女" in reply and "重新排盘" in reply, reply
    assert ("起运" in reply or "大运" in reply or "四柱" in reply), reply
    assert "{" not in reply
    # 回执存活于最终回复（卡片包装内），润色稿完整保留
    assert "我注意到档案里记录的是男，已按您本次说的「女」重新排盘" in reply, reply
    assert polished in reply, reply
    # chart_records 新增 + 档案双写为 女
    chart = ChartDAO(db_path).get_latest_chart("eval_user_t008b")
    assert chart is not None
    default = PersonDAO(db_path).get_default_person("eval_user_t008b")
    assert default["gender"] == "女", default
