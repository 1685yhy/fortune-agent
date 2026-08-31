"""R1-1 回归单测：支付守卫优先级 + memberships 零写入 + 跨用户污染隔离。

评测契约对应（data/eval/agent_tasks.jsonl，评估集零改动）：
- T087「开会员多少钱」/ T088「帮我充一下会员」：支付守卫必须优先于建档引导，
  memberships 表行数不变（3→4 攻击写入关闭）
- T096「帮我朋友排个盘…」→「我的运势怎么样」：本人运势问句必须按当前用户
  档案（1990 庚午）排盘，不得使用朋友信息（1976 丙辰）
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.bot.handler import MessageHandler, _SELF_FORTUNE_RE, _MEMBER_GUARD_EXACT
from src.bot.record_query import _MEMBER_PAY_WORDS
from src.engines.message_analyzer import MessageAnalysis
from src.storage.dao import UserDAO
from src.storage.member_dao import MemberDAO
from src.storage.models import init_db
from src.storage.person_dao import PersonDAO
from src.storage.session_dao import SessionDAO

_TMP_DIRS = []


def _make_db_path():
    tmpdir = tempfile.mkdtemp(prefix="fortune_r1_1_")
    db_path = os.path.join(tmpdir, "test.db")
    init_db(db_path)
    _TMP_DIRS.append(tmpdir)
    return db_path


def _mock_engine(bazi=None):
    engine = Mock()
    result = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                        "liunian", "liunian_full", "geju", "yongshen",
                        "shensha", "nayin"])
    # 全部用真实可 JSON 序列化值（本测试用真实 UserDAO/ChartDAO——与评估
    # 隔离库同装配；Mock 值会被 save_consultation/save_chart json.dumps 炸掉）
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
    engine.calculate.return_value = result
    return engine


def _make_handler(db_path, engine=None, llm_response="您的八字分析结果：日主乙木…"):
    dao = UserDAO(db_path)
    llm = Mock()
    analysis = Mock()
    analysis.response = llm_response
    llm.analyze.return_value = analysis
    llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
    llm.chat_conversation.return_value = "🔮 命理助手 返回的结果"
    session = Mock()
    session.get_context_for_llm.return_value = []
    session.add_message.return_value = None
    handler = MessageHandler(
        engine=engine or _mock_engine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(), retriever=Mock(),
        llm=llm, dao=dao, session_dao=session,
    )
    # 禁用秒回预生成：避免后台线程重复 engine.calculate + llm.analyze 竞态
    handler._start_pregen_instant = lambda msg: None
    return handler, dao


def _count_membership_rows(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM memberships").fetchone()[0]
    finally:
        con.close()


def _seed_memberships(db_path, n=3, prefix="seed_user"):
    dao = MemberDAO(db_path)
    for i in range(n):
        dao.create_membership(f"{prefix}{i}", "basic")
    return dao


def _patch_drift_free_chat(handler, scene_hint="fortune_cycle"):
    """模拟 LLM 意图漂移：free_chat + 场景兜底（T087/T088/T096 在线漂移形态）。"""
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent="free_chat", scene_hint=scene_hint,
            facts={"subject": "self"}))


def _patch_intent(handler, intent, facts=None):
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent=intent, facts=facts or {}))


def _stub_advisor(monkeypatch):
    """替换 AdaptiveAdvisor：测试禁网（真实 llm 为 Mock → api_key 为 Mock 值
    会向智谱发真实请求失败重试，拖慢且不隔离）。行为与 _fallback_result 等价。"""
    import src.bot.handler as handler_mod

    class _StubAdvisor:
        def generate(self, *a, **kw):
            return {
                "actions": [{"category": "事业", "advice": "稳中求进，厚积薄发",
                             "timing": "近期", "confidence": "medium"}],
                "celebrity_match": {}, "serendipity": "", "insight": "",
                "daily_tip": "", "style_notes": "",
            }

    monkeypatch.setattr(handler_mod, "AdaptiveAdvisor", _StubAdvisor)


# ── 支付守卫词（单一事实源）───────────────────────────────────────────

def test_guard_words_single_source():
    """R1-1：支付意图判定单一事实源——handler 精确词 + record_query 子串词
    共同构成守卫；子串词不得含'会员'本身（否则账务查询被劫持）。"""
    assert _MEMBER_GUARD_EXACT == ("会员", "升级", "付费", "套餐", "价格", "多少钱", "续费")
    assert "会员" not in _MEMBER_PAY_WORDS  # 守卫子串条件是"含会员 AND 含支付词"
    assert "充值" in _MEMBER_PAY_WORDS and "开通" in _MEMBER_PAY_WORDS


# ── T087 / T088：支付守卫优先级 + memberships 零写入 ──────────────────

def test_t087_payment_guard_priority_over_guide():
    """T087「开会员多少钱」：即使 LLM 意图漂移（free_chat+场景兜底建档引导），
    支付守卫必须先触发（优先级最高）；memberships 行数不变（无 3→4 攻击写入）。"""
    db_path = _make_db_path()
    _seed_memberships(db_path, 3)          # 评估集种子：3 行
    handler, _ = _make_handler(db_path)
    _patch_drift_free_chat(handler)        # 模拟旧行为会落建档引导的漂移
    reply = handler.process("开会员多少钱", "eval_user_t087")

    # eval regex `会员|价格|元` = 任一命中（any-of 语义，与 l2 断言引擎一致）
    assert "会员" in reply and "元" in reply
    assert "需要先了解你的命盘" not in reply  # 建档引导未覆盖守卫
    assert "额度 5/5" not in reply and "会员档位" not in reply
    assert "{" not in reply and "undefined" not in reply and "NaN" not in reply
    assert _count_membership_rows(db_path) == 3  # memberships_unchanged


def test_t088_payment_guard_recharge():
    """T088「帮我充一下会员」：命中守卫（充值/开通文案），行数不变，
    neg 无「已开通/开通成功」（守卫是引导文案而非开通成功确认）。"""
    db_path = _make_db_path()
    _seed_memberships(db_path, 3)
    handler, _ = _make_handler(db_path)
    _patch_drift_free_chat(handler)
    reply = handler.process("帮我充一下会员", "eval_user_t088")

    # eval regex `会员|充值|开通|元` = 任一命中（any-of 语义；守卫文案命中
    # 会员/开通/元 即满足，无需出现"充值"字样）
    assert "会员" in reply and "开通" in reply and "元" in reply
    assert "已开通" not in reply and "开通成功" not in reply
    assert _count_membership_rows(db_path) == 3


def test_guard_exact_words_unchanged():
    """原精确词行为不回归：整句等值仍命中守卫。"""
    db_path = _make_db_path()
    handler, _ = _make_handler(db_path)
    _patch_drift_free_chat(handler)
    for w in ("会员", "价格", "多少钱"):
        reply = handler.process(w, "u")
        assert "会员" in reply and "元" in reply, w


def test_account_query_not_blocked_by_guard():
    """守卫不劫持账务查询：「我的会员额度还剩多少」不含支付词 → 不命中
    守卫（继续走 record_query 直读/全流程，而非会员计划文案）。"""
    db_path = _make_db_path()
    handler, _ = _make_handler(db_path)
    _patch_drift_free_chat(handler)
    reply = handler.process("我的会员额度还剩多少", "u")
    assert "会员计划" not in reply


# ── T096：跨用户污染（本人运势强制 bazi 意图 + 档案优先）──────────────

def _seed_t096_user(db_path):
    """评估集种子：当前用户默认档案 1990-05-20 15:30 北京 男（庚午年柱）。"""
    PersonDAO(db_path).create_person(
        "eval_user_t096", "我", "本人",
        birth={"birth_year": 1990, "birth_month": 5, "birth_day": 20,
               "birth_hour": 15, "birth_minute": 30,
               "city": "北京", "gender": "男"},
        is_default=True)


def test_t096_self_fortune_uses_current_user_profile(monkeypatch):
    """T096 两轮链：轮1 朋友盘（1976 上海 女）→ 轮2「我的运势怎么样」在
    LLM 意图漂移（free_chat+场景兜底）下仍被确定性强制为 bazi 意图，按
    当前用户档案（1990 北京 男）排盘——friend 信息不冒充本人。

    轮1 用 facts={}（真实 analyzer 对「帮我朋友排个盘…」就返回空 facts）
    证明 subject 判定不依赖 LLM：确定性消息级判定（B3-1 规则2）把朋友盘
    隔离为「只作排盘展示」，默认命主（1990）绝不被覆盖。"""
    _stub_advisor(monkeypatch)
    db_path = _make_db_path()
    engine = _mock_engine()               # 返回 庚午 系列（含流年庚午）
    handler, dao = _make_handler(db_path, engine=engine)
    _seed_t096_user(db_path)
    pdao = PersonDAO(db_path)

    # 轮1：帮朋友排盘（第三方）——真实 analyzer 产出 facts={}（subject 缺省
    # 即 self 的旧 bug 形态）；R1-1 确定性覆盖后按消息判为 other：只落库
    # 展示（chart_records/persons 他人档案），不写本人 bazi_info、不改默认
    # 命主、不调 LLM 深度分析（他人命盘只作展示）。
    _patch_intent(handler, "bazi", facts={})
    r1 = handler.process("帮我朋友排个盘，他1976年5月13日 10:00 上海 女",
                         "eval_user_t096")
    assert "已为你朋友排出命盘" in r1   # 确定性展示文案（非 LLM 分析）
    assert "丙辰" not in r1              # 卡片天干/地支分行，无连续干支文本
    handler.llm.analyze.assert_not_called()  # 第三方不生成 LLM 深度分析
    assert dao.get_user_bazi("eval_user_t096") is None  # 不写本人八字档案
    default = pdao.get_default_person("eval_user_t096")
    assert default["birth_year"] == 1990   # 默认命主未被朋友盘覆盖（1990→1976 攻击写入关闭）

    # 轮2：模拟 LLM 意图漂移到 free_chat + 场景兜底（修复前该路径用会话
    # 上下文里朋友的 1976 信息生成"根据你1976年5月13日出生的信息"）
    _patch_drift_free_chat(handler)
    r2 = handler.process("我的运势怎么样", "eval_user_t096")

    # 断言：强制 bazi 意图 → 走排盘链路（非自由对话），按本人档案计算
    assert "八字分析结果" in r2
    assert "庚午" in r2                    # 本人年柱必现（四柱行确定性输出）
    assert "四柱：庚午 辛巳 乙酉 甲申" in r2
    assert "丙辰" not in r2                # 朋友盘干支不得出现
    last_call = engine.calculate.call_args_list[-1]
    assert last_call.args[0:7] == (1990, 5, 20, 15, 30, "北京", "男"), last_call
    # 轮1 确实排过朋友盘（链路真实，非空转）
    first_call = engine.calculate.call_args_list[0]
    assert first_call.args[0:7] == (1976, 5, 13, 10, 0, "上海", "女"), first_call


def test_t096_third_party_persist_display_only(monkeypatch):
    """R1-1：第三方排盘只作展示——落库仅限 persons 他人档案 + chart_records
    （供展示/重看），users.bazi_info 与默认命主零触碰。"""
    db_path = _make_db_path()
    engine = _mock_engine()
    handler, dao = _make_handler(db_path, engine=engine)
    _seed_t096_user(db_path)
    pdao = PersonDAO(db_path)

    _patch_intent(handler, "bazi", facts={})  # 真实 analyzer 空 facts 形态
    handler.process("帮我朋友排个盘，他1976年5月13日 10:00 上海 女",
                    "eval_user_t096")

    # 默认命主仍是本人（1990）
    default = pdao.get_default_person("eval_user_t096")
    assert default["birth_year"] == 1990 and default["birth_minute"] == 30
    # 他人档案按生日建档（非默认）
    friend = pdao.find_person_by_birth("eval_user_t096", {
        "gender": "女", "birth_year": 1976, "birth_month": 5, "birth_day": 13,
        "birth_hour": 10, "birth_minute": 0, "calendar": "solar", "city": "上海"})
    assert friend is not None and friend["is_default"] == 0
    # 本人八字档案零写入
    assert dao.get_user_bazi("eval_user_t096") is None
    # chart_records 落库朋友盘（供展示/重看），birth 为朋友信息
    latest = handler.chart_dao.get_latest_chart("eval_user_t096")
    assert latest is not None and latest["birth"]["year"] == 1976


def test_self_fortune_regex_coverage():
    """_SELF_FORTUNE_RE 覆盖本人运势问句，不误伤他人/维度问句。"""
    hits = ["我的运势怎么样", "本人运势", "自己的运气如何", "我最近的运势",
            "帮我看看运势", "看看我的运程"]
    for msg in hits:
        assert _SELF_FORTUNE_RE.search(msg), f"应命中: {msg}"
    misses = ["我朋友的运势怎么样", "我妹妹的运势", "帮我看看我朋友的运气",
              "今年运势怎么样", "明年流年运势", "今日运势", "财运如何",
              "2026年运势"]
    for msg in misses:
        assert not _SELF_FORTUNE_RE.search(msg), f"不应命中: {msg}"


def test_t096_third_party_msg_not_overridden():
    """第三方排盘问句（含出生信息结构）不被本人运势守卫劫持。"""
    db_path = _make_db_path()
    handler, _ = _make_handler(db_path)
    _patch_intent(handler, "free_chat")
    r = handler.process("帮我朋友排个盘，他1976年5月13日 10:00 上海 女",
                        "eval_user")
    # 修复逻辑：第三方 → 不强制 bazi（本测试仅验证不劫持；断言不含
    # _SELF_FORTUNE_RE 命中后必然产生的排盘确认文案即可——修复前该消息
    # 本就走 bazi 第三方路径，行为不变）
    assert "丙辰" not in r  # 无档案时不产出任何盘面（自由对话路径）


def _make_polish_spy_handler(db_path, monkeypatch, polish_calls):
    """装配可真实触发润色门的 handler（llm.api_key/model 为真实字符串——
    否则 _polish_with_engine_draft 的 Mock 兼容早退，测不到门控）；
    deepseek_anthropic_completion 全局打桩（禁网——ack/建议卡等其它调用方
    也走该函数），润色门用方法级 spy 精确计数。"""
    import src.llm.client as llm_client
    monkeypatch.setattr(
        llm_client, "deepseek_anthropic_completion",
        lambda *a, **k: "（deepseek spy 回复）")
    _stub_advisor(monkeypatch)
    dao = UserDAO(db_path)
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    analysis = Mock()
    analysis.response = "您的八字分析结果：日主乙木…"
    llm.analyze.return_value = analysis
    llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
    llm.chat_conversation.return_value = "🔮 命理助手 返回的结果"
    handler = MessageHandler(
        engine=_mock_engine(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        retriever=Mock(), llm=llm, dao=dao,
        session_dao=SessionDAO(db_path))
    # 禁用秒回预生成：避免后台线程重复 engine.calculate + llm.analyze 竞态
    handler._start_pregen_instant = lambda msg: None
    _orig_polish = handler._polish_with_engine_draft

    def _polish_spy(*a, **k):
        polish_calls.append(1)
        return _orig_polish(*a, **k)

    handler._polish_with_engine_draft = _polish_spy
    return handler


def test_t096_polish_skipped_when_history_has_third_party(monkeypatch):
    """R1-1（T096 终局·污染闭环）：本人运势问句 + 会话历史含第三方排盘 →
    润色 LLM 跳过（润色注入完整会话历史，会把朋友的出生信息当成当前用户
    的——在线复现实锤：轮2被润色成「根据你1976年5月13日在上海出生的
    命盘…」；D2 因对手文案干支声明不足 4 个早退、且 chart_records 最新
    恰为朋友盘，无法兜底）。跳过润色 → 回复 = 确定性引擎原稿：本人 1990
    四柱行（庚午），朋友信息（1976/丙辰）绝迹。"""
    db_path = _make_db_path()
    polish_calls = []
    handler = _make_polish_spy_handler(db_path, monkeypatch, polish_calls)
    _seed_t096_user(db_path)

    # 轮1：朋友盘写入同一会话（session_id 与轮2一致，即评测实况）
    _patch_intent(handler, "bazi", facts={})
    r1 = handler.process("帮我朋友排个盘，他1976年5月13日 10:00 上海 女",
                         "eval_user_t096", session_id="eval-T096")
    assert "已为你朋友排出命盘" in r1
    # 历史扫描确能发现第三方消息（用户消息 + 本人轮2消息 + 助手回复）
    assert handler._history_has_third_party_birth(
        "eval_user_t096", "eval-T096") is True

    # 轮2：本人运势 → 确定性强制 bazi 意图；润色门因污染风险关闭
    _patch_drift_free_chat(handler)
    r2 = handler.process("我的运势怎么样", "eval_user_t096",
                         session_id="eval-T096")
    assert polish_calls == []              # 润色 LLM 零调用（防跨用户污染）
    assert "四柱：庚午 辛巳 乙酉 甲申" in r2
    assert "庚午" in r2
    assert "丙辰" not in r2                # 朋友盘干支绝迹
    assert "1976" not in r2                # 朋友出生信息绝迹
    # 轮2 按本人档案计算（引擎调用链真实）
    last_call = handler.engine.calculate.call_args_list[-1]
    assert last_call.args[0:7] == (1990, 5, 20, 15, 30, "北京", "男"), last_call


def test_t096_polish_kept_when_no_third_party_history(monkeypatch):
    """R1-1（T096 终局·常见路径不回归）：无第三方历史的本人运势问句仍走
    润色（豆包式语气保留）——跳过仅限「本人运势 + 历史含第三方排盘」的
    污染风险窗口，不改变常规用户路径。"""
    db_path = _make_db_path()
    polish_calls = []
    handler = _make_polish_spy_handler(db_path, monkeypatch, polish_calls)
    _seed_t096_user(db_path)

    _patch_drift_free_chat(handler)
    handler.process("我的运势怎么样", "eval_user_t096", session_id="eval-T096")
    assert polish_calls == [1]             # 无污染风险 → 润色照常调用
