# -*- coding: utf-8 -*-
"""k15 评测体系尾巴收口批测试（分支 k15-eval-tails，BASE main=622e1b1）。

覆盖（对应 plan docs/superpowers/plans/2026-09-09-k15-eval-tails.md）：
A age_claim pattern2 双单位变体误报修复（3 复现样例 + 真实事故句回归锁定，
  与 tests/test_k11_fact_discipline.py 的 TestEvalDerived 同层互补）
B 挂载行 schema/计数：agent_tasks 108 行全绿（T104-T108 新增）+ persons.
  solar_time 契约校验（int 0/1，bool/越界值报错）
C 挂载行可执行且非恒真：
  - hour_boundary 双行（T106 开=壬午 / T107 关=辛巳）：引擎 golden 复算锚定
    （1999-05-13 10:55 长春 男 solar_time on/off → 时柱逐字一致）+ 行内
    contains 与引擎输出互洽 + 好坏回复双向断言（对侧时柱/缺失四柱 → FAIL）
  - entity_qa 三行（T104/T105 正例 + T108 负例）：行文本 decide_search
    触发/零触发判定 + 好坏回复双向断言（无来源/甩锅句 → FAIL；负例出现
    「信息来源」→ FAIL）+ process() 全链路（mock LLM/离线打桩检索）复现
    行文本语义（与 k11b tests/test_k11b_search_trigger.py D/E 同构）
D 种子注入：persons.solar_time=0 建档透传（T107 关档行可执行前提）；
  缺省 = 默认开

运行：cd /mnt/e/fae-k15 && OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k15_eval_tails.py \
  tests/test_k11_fact_discipline.py tests/test_k15_bing_smoke.py -q -p no:cacheprovider
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_EVAL = _REPO / "scripts" / "eval_agent"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

# e2e 走真实引擎主链时排盘卡 html 会写 CHARTS_DIR（默认 /opt/fortune-data 无权限）
# → 重定向到临时目录（须在导入 handler 前设置，模块级常量读取）
os.environ.setdefault(
    "CHARTS_DIR", tempfile.mkdtemp(prefix="k15_charts_"))

import pytest  # noqa: E402

import l2_eval  # noqa: E402
import validate_tasks as vt  # noqa: E402

_FACTS = {"gender": "男", "shensha": ["太极贵人"],
          "age_zhousui": 27, "age_xusui": 28, "dayun_ganzhi": "丙寅"}
_TASK_PATH = _REPO / "data" / "eval" / "agent_tasks.jsonl"


def _rows():
    return [json.loads(l) for l in _TASK_PATH.read_text(encoding="utf-8")
            .splitlines() if l.strip()]


def _task(tid):
    return next(t for t in _rows() if t["id"] == tid)


def _failed(out, name=None):
    """未过断言名清单（name 过滤可选）。"""
    return [c["name"] for c in out if not c["ok"]
            and (name is None or c["name"] == name)]


# ============================================================
# B：挂载行 schema/计数 + persons.solar_time 契约
# ============================================================

class TestMountedRowsSchema:
    def test_eval_set_108_green_with_new_ids(self):
        errs, tasks = vt.validate_file(str(_TASK_PATH))
        assert not errs, errs[:5]
        cerrs, stats = vt.coverage_errors(tasks)
        assert not cerrs, cerrs
        assert stats["total"] == 108
        ids = [t["id"] for t in tasks]
        assert ids[-5:] == ["T104", "T105", "T106", "T107", "T108"]

    def test_new_rows_row_shape(self):
        """新行 reply_checks 契约：derived 只用 tool_json（零 persons 依赖）/
        无 derived 也行；neg 含四占位符；min_len 非负。"""
        for tid in ("T104", "T105", "T106", "T107", "T108"):
            t = _task(tid)
            errs = []
            vt.check_task(t, errs)
            assert not errs, (tid, errs)
        # T106 开档行不带 solar_time 键（= 默认开语义兼测旧档案兼容）；
        # T107 关档行必须显式 0（种子透传契约，见 D）
        assert "solar_time" not in _task("T106")["setup"]["persons"][0]
        assert _task("T107")["setup"]["persons"][0]["solar_time"] == 0

    def test_persons_solar_time_schema_contract(self):
        """persons.solar_time 只允许 int 0/1；bool/字符串/越界 → schema 报错。"""
        base = {"id": "T999", "title": "x", "category": "paipan",
                "severity": "P1", "pass_k": 1, "source": "bug-k11c",
                "turns": [{"role": "user", "text": "q"}],
                "expected_tools": [], "no_tool": False,
                "reply_checks": {"contains": [], "neg_checks":
                                 ["{", "undefined", "NaN", "null"],
                                 "regex": [], "min_len": 1}}
        for bad_val in (True, "0", 2, -1):
            t = json.loads(json.dumps(base))
            t["setup"] = {"persons": [{"name": "a", "gender": "male",
                                       "birth": "1999-05-13 10:55",
                                       "city": "长春", "solar_time": bad_val}]}
            errs = []
            vt.check_task(t, errs)
            assert any("solar_time" in e for e in errs), (bad_val, errs)
        for ok_val in (0, 1):
            t = json.loads(json.dumps(base))
            t["setup"] = {"persons": [{"name": "a", "gender": "male",
                                       "birth": "1999-05-13 10:55",
                                       "city": "长春", "solar_time": ok_val}]}
            errs = []
            vt.check_task(t, errs)
            assert not errs, errs


# ============================================================
# D：种子注入 persons.solar_time 透传（T107 可执行前提）
# ============================================================

def _fresh_db():
    from src.storage.models import init_db
    db = os.path.join(tempfile.mkdtemp(prefix="k15_seed_"), "t.db")
    init_db(db)
    return db


class TestSeedSolarTime:
    def test_seed_off_0_preserved(self):
        db = _fresh_db()
        from src.storage.person_dao import PersonDAO
        err = l1_eval_seed_persons(db, "u_off",
                                   {"solar_time": 0})
        assert err is None, err
        p = PersonDAO(db).get_default_person("u_off")
        assert p["solar_time"] == 0

    def test_seed_default_on_when_missing(self):
        db = _fresh_db()
        from src.storage.person_dao import PersonDAO
        err = l1_eval_seed_persons(db, "u_on", {})
        assert err is None, err
        p = PersonDAO(db).get_default_person("u_on")
        assert p["solar_time"] == 1

    def test_seed_explicit_on_1(self):
        db = _fresh_db()
        from src.storage.person_dao import PersonDAO
        err = l1_eval_seed_persons(db, "u_on1", {"solar_time": 1})
        assert err is None, err
        p = PersonDAO(db).get_default_person("u_on1")
        assert p["solar_time"] == 1


def l1_eval_seed_persons(db_path, user_id, extra):
    """直接调 seed_task_setup persons 分支（隔离 import，避免 l1_eval 全量副作用）。"""
    import l1_eval
    p = {"name": "边界男", "gender": "male",
         "birth": "1999-05-13 10:55", "city": "长春"}
    p.update(extra)
    return l1_eval.seed_task_setup(db_path, user_id, {"persons": [p]})


# ============================================================
# C1：hour_boundary 双行可执行且非恒真（引擎 golden 复算 + 双向断言）
# ============================================================

class TestHourBoundaryRows:
    def test_engine_golden_on_off_pillars(self):
        """k11c golden：10:55 长春男 开=11:20 壬午午时 / 关=10:55 辛巳巳时；
        年/月/日柱逐字一致，仅时柱随开关翻转。"""
        from src.engines.bazi import BaziEngine
        engine = BaziEngine()
        on = engine.calculate(1999, 5, 13, 10, 55, "长春", "男", solar_time=True)
        off = engine.calculate(1999, 5, 13, 10, 55, "长春", "男", solar_time=False)
        assert on.bazi[:3] == ["己卯", "己巳", "乙丑"]
        assert off.bazi[:3] == ["己卯", "己巳", "乙丑"]
        assert on.bazi[3] == "壬午" and off.bazi[3] == "辛巳"
        assert on.bazi != off.bazi

    def _check(self, task, reply):
        out = l2_eval.eval_reply_checks(task, reply)
        out += l2_eval.eval_derived_checks(task, reply)
        return out

    def test_t106_on_row_good_bad(self):
        t = _task("T106")
        good = ("【八字命盘】📜 四柱：己卯 己巳 乙丑 壬午\n"
                "日主乙木午时生人，时柱壬午。润色正文补充若干分析，"
                "让你读起来像一段完整的排盘解读文字。")
        out = self._check(t, good)
        assert not _failed(out), [(c["name"], c["detail"]) for c in out
                                       if not c["ok"]]
        # 对侧时柱（引擎关档口径）→ contains 壬午 失败（非恒真）
        bad = good.replace("壬午", "辛巳")
        assert _failed(self._check(t, bad), "contains")
        # 缺失四柱/时柱（纯散文）→ FAIL
        assert _failed(
            self._check(t, "你今年运势整体平稳，建议深耕专业。"), "contains")

    def test_t107_off_row_good_bad(self):
        t = _task("T107")
        good = ("【八字命盘】📜 四柱：己卯 己巳 乙丑 辛巳\n"
                "日主乙木巳时生人，时柱辛巳。润色正文补充若干分析，"
                "让你读起来像一段完整的排盘解读文字。")
        out = self._check(t, good)
        assert not _failed(out), [(c["name"], c["detail"]) for c in out
                                       if not c["ok"]]
        bad = good.replace("辛巳", "壬午")
        assert _failed(self._check(t, bad), "contains")


# ============================================================
# C2：entity_qa 三行可执行且非恒真（判定层 + 双向断言）
# ============================================================

class TestEntityRows:
    def test_row_texts_decide_search(self):
        """行文本触发/零触发判定与 k11b 语义一致（纯函数，零 LLM）。"""
        from src.rag.search_trigger import decide_search
        d = decide_search(_task("T104")["turns"][0]["text"])
        assert d.should_search and d.entity == "易宝支付"
        d = decide_search(_task("T105")["turns"][0]["text"])
        assert d.should_search and d.entity == "腾讯"
        d = decide_search(_task("T108")["turns"][0]["text"])
        assert not d.should_search  # 实体仅是背景，问词挂在运势上
        assert d.reason == "local"

    def _check(self, tid, reply):
        t = _task(tid)
        out = l2_eval.eval_reply_checks(t, reply)
        out += l2_eval.eval_derived_checks(t, reply)
        return out

    def test_t104_good_bad(self):
        t = _task("T104")
        good = ("易宝支付是一家第三方支付公司，公开信息显示其主营收单业务。"
                "（信息来源：yeepay.com、zhihu.com 等第三方公开网络内容，"
                "仅供参考，具体请以官方渠道核实为准）\n以上为润色正文补充。")
        out = self._check("T104", good)
        assert not _failed(out), [(c["name"], c["detail"]) for c in out
                                       if not c["ok"]]
        # 无来源痕迹 → contains FAIL
        assert _failed(self._check("T104", "易宝支付是一家公司。润色正文补充。"), "contains")
        # 甩锅句 → neg FAIL
        assert _failed(self._check("T104", good + "你自己查证一下吧。"), "neg_checks")
        # 工具 JSON 泄漏 → derived.tool_json FAIL
        assert _failed(
            self._check("T104", good + "<tool_calls>[{\"tool\": \"web_search\"}]</tool_calls>"),
            "derived.tool_json")
        assert t["reply_checks"]["min_len"] > 0  # 防恒真退化

    def test_t105_good_bad(self):
        good = ("腾讯是知名互联网公司，公开信息可查。"
                "（信息来源：tencent.com 等第三方公开网络内容，仅供参考，"
                "具体请以官方渠道核实为准）\n以上为润色正文补充。")
        out = self._check("T105", good)
        assert not _failed(out), [(c["name"], c["detail"]) for c in out
                                       if not c["ok"]]
        assert _failed(self._check("T105", "腾讯挺大的。润色正文补充。"), "contains")

    def test_t108_good_bad(self):
        """本地运势问：回复含盘内大运/运势内容且零来源痕迹。"""
        good = ("【大运】当前正走丙寅大运，2026 丙午流年运势整体平稳，"
                "事业上宜稳扎稳打，感情上顺其自然即可。"
                "以上为排盘正文补充分析，供你参考这段完整的运势解读。")
        out = self._check("T108", good)
        assert not _failed(out), [(c["name"], c["detail"]) for c in out
                                       if not c["ok"]]
        # 出现来源痕迹（自动检索发生了）→ FAIL（负例语义反噬）
        bad = good + "（信息来源：yeepay.com 等第三方公开网络内容，仅供参考）"
        assert _failed(self._check("T108", bad), "neg_checks")


# ============================================================
# C3：entity_qa 行 process() 全链路复现（与 k11b D/E 同构；
# LLM 打桩 + 离线打桩检索 —— 纯本地可跑，零 key/零网络）
# ============================================================

def _mock_result(bazi=None, gender="男"):
    """可 JSON 序列化的引擎结果（同 test_eval_r1_2/k11b 装配）。"""
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


def _make_handler(db_path, engine=None, llm=None, session=None):
    """同 k11b 装配：llm 主分析返回串接字段（.analyze/.chat）预配置；
    返回 (handler, dao)（k11b 同款解包契约）。"""
    from src.bot.handler import MessageHandler
    from src.storage.dao import UserDAO
    dao = UserDAO(db_path)
    llm = llm or Mock()
    if isinstance(llm, Mock):
        llm.chat.return_value = Mock(response="🔮 命理助手 返回的结果")
        llm.chat_conversation.return_value = "🔮 命理助手 返回的结果"
        analysis = Mock()
        analysis.response = "您的八字分析结果：日主乙木，整体格局…"
        llm.analyze.return_value = analysis
    handler = MessageHandler(
        engine=engine or _MockResultEngine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(), retriever=Mock(),
        hehun_engine=Mock(), llm=llm, dao=dao, session_dao=session,
    )
    handler._start_pregen_instant = lambda msg, user_id="": None
    return handler, dao


def _MockResultEngine():
    from unittest.mock import Mock
    engine = Mock()
    engine.calculate.return_value = _mock_result()
    return engine


def _patch_intent(handler, intent, needs_search=False, facts=None):
    from src.engines.message_analyzer import MessageAnalysis
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: MessageAnalysis(
            needs_soothe=False, soothe_text="", emotion_label=None,
            intent=intent, needs_search=needs_search, facts=facts or {}))


def _seed_person(db_path, user_id, birth=None):
    from src.storage.person_dao import PersonDAO
    birth = birth or {"birth_year": 1990, "birth_month": 5, "birth_day": 20,
                      "birth_hour": 15, "birth_minute": 30,
                      "city": "北京", "gender": "男"}
    PersonDAO(db_path).create_person(
        user_id, "我", "本人", birth=birth, is_default=True)


def _engine_handler(db_path, monkeypatch, user_id):
    """真实引擎主链装配（同 k11b：行动建议线程关闭——建议卡非本批对象）。"""
    import src.bot.handler as handler_mod
    from src.engines.bazi import BaziEngine
    from src.storage.session_dao import SessionDAO
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    monkeypatch.setattr(handler_mod, "HAS_ADVISOR_V2", False)
    llm = Mock()
    llm.api_key = "test-key-no-network"
    llm.model = "test-model"
    handler, _ = _make_handler(db_path, engine=BaziEngine(), llm=llm,
                               session=SessionDAO(db_path))
    handler._quick_flash = lambda prompt, **kw: "好的。"
    _seed_person(db_path, user_id)  # k11b 同款：默认命主档案（主链排盘引来源）
    return handler


def _offline_web(monkeypatch, results=None):
    """把 handler 模块命名空间里的 search_web/web_search_available 离线打桩。"""
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
                        lambda force=False: True)
    return calls


def _polish_fixed(monkeypatch, text):
    """打桩 deepseek_anthropic_completion：返回固定润色稿并捕获 system prompt。"""
    import src.llm.client as llm_client
    captured = {}

    def fake_completion(api_key, messages, **kw):
        captured["messages"] = messages
        return text

    monkeypatch.setattr(llm_client, "deepseek_anthropic_completion", fake_completion)
    return captured


def _make_db():
    from src.storage.models import init_db
    db_path = os.path.join(tempfile.mkdtemp(prefix="k15_e2e_"), "test.db")
    init_db(db_path)
    return db_path


class TestProcessEntityRows:
    def test_t104_row_triggers_search_and_sources(self, monkeypatch):
        """T104 行文本全链路：自动检索 1 次（query=易宝支付）+ 回复带来源
        痕迹 + 无甩锅/JSON（k11b T104 e2e 同构，行文本为 agent_tasks 原文）。"""
        import src.bot.handler as handler_mod
        from src.rag import web_search as ws_mod
        monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
        monkeypatch.setattr(ws_mod, "web_search_available", lambda force=False: True)
        search_calls = _offline_web(monkeypatch)

        db_path = _make_db()
        handler = _engine_handler(db_path, monkeypatch, "k15_t104")
        captured = _polish_fixed(
            monkeypatch,
            "易宝支付是第三方支付公司，做这行要看你自己的八字适配度。")
        _patch_intent(handler, "career")

        reply = handler.process(
            _task("T104")["turns"][0]["text"], "k15_t104", session_id="k15-T104")

        assert search_calls == ["易宝支付"], search_calls
        sys_text = "".join(m.get("content", "") for m in captured["messages"]
                           if m.get("role") == "system")
        assert "【网络检索结果】" in sys_text
        assert "禁止凭记忆编造" in sys_text
        assert "易宝支付" in reply and "来源" in reply
        for bad in ("你自己查", "自己查证", "实时信息暂不可用",
                    "<tool_calls>", "{"):
            assert bad not in reply, (bad, reply)

    def test_t105_row_triggers_search(self, monkeypatch):
        """T105 行文本（无后缀主流实体腾讯）全链路同构复现。"""
        import src.bot.handler as handler_mod
        from src.rag import web_search as ws_mod
        monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
        monkeypatch.setattr(ws_mod, "web_search_available", lambda force=False: True)
        search_calls = _offline_web(monkeypatch, results=[
            {"title": "腾讯官网", "url": "https://www.tencent.com/",
             "text": "腾讯是一家互联网公司。", "site_name": ""},
            {"title": "腾讯新闻", "url": "https://news.qq.com/",
             "text": "腾讯近期动态。", "site_name": ""},
        ])

        db_path = _make_db()
        handler = _engine_handler(db_path, monkeypatch, "k15_t105")
        captured = _polish_fixed(
            monkeypatch, "腾讯是知名互联网公司，具体发展看公开资料。")
        _patch_intent(handler, "career")

        reply = handler.process(
            _task("T105")["turns"][0]["text"], "k15_t105", session_id="k15-T105")

        assert search_calls == ["腾讯"], search_calls
        sys_text = "".join(m.get("content", "") for m in captured["messages"]
                           if m.get("role") == "system")
        assert "【网络检索结果】" in sys_text
        assert "腾讯" in reply and "来源" in reply
        assert "{" not in reply

    def test_t108_row_zero_search_no_source(self, monkeypatch):
        """T108 行文本（实体背景+本地运势问）：needs_search=True（LLM 误报
        场景）仍零检索、回复零来源痕迹（k11b T105 e2e 同构）。"""
        import src.bot.handler as handler_mod
        from src.rag import web_search as ws_mod
        monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
        monkeypatch.setattr(ws_mod, "web_search_available", lambda force=False: True)
        search_calls = _offline_web(monkeypatch)

        db_path = _make_db()
        handler = _engine_handler(db_path, monkeypatch, "k15_t108")
        _polish_fixed(monkeypatch,
                      "今年运势整体平稳，当前正走丙寅大运，宜稳扎稳打，"
                      "详见上方命盘。")
        _patch_intent(handler, "career", needs_search=True)

        reply = handler.process(
            _task("T108")["turns"][0]["text"], "k15_t108", session_id="k15-T108")

        assert search_calls == [], search_calls
        assert reply and "{" not in reply
        assert "信息来源" not in reply
        # 本地答含盘内内容（确定性大运丙寅）
        assert "丙寅" in reply
