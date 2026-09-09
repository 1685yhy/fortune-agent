# -*- coding: utf-8 -*-
"""k18：bazi_info 深度直读点 persons-first 化行为测试（分支 k18-direct-reads）。

数据一致性铁律：persons 默认档案 = 单一事实源；users.bazi_info 仅兜底；
四柱/盘面键只取「出生档案匹配的 chart_records」（k8 语义）。本批把残余
裸读 users.bazi_info 的消费点（问候/快路径/自由对话门/解梦/学堂/报告侧
dashboard/share-card/legacy calendar/hourly REST）逐一改走统一档案链
（get_user_birth_profile / get_user_birth_profile_full），本文件按改造点
锁行为：persons 有 → 读 persons；persons 无 → 兜底 bazi_info；一致性分裂
场景（persons=1999 vs 旧行 1995 / 他人盘 bazi 键）→ persons 胜、旧行
bazi 键不被消费。

运行：cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k18_direct_reads.py \
  -q -p no:cacheprovider（import 冷启动 1-2 分钟正常）
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402


# ================================================================
# 装配：真实 DAO/PersonDAO/ChartDAO 同库（与生产一致）
# ================================================================

CHART_BIRTH = {"year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
               "city": "吉林省长春市", "gender": "男", "calendar": "solar"}
CHART_BAZI = ["乙亥", "己卯", "戊午", "丁巳"]   # 1995-03-28 9:00 长春男（引擎复算）
POLLUTED_BAZI = ["丙午", "丙申", "甲子", "甲子"]  # 21:44 事故形态：2026-08-18 他人盘


def _db(tmp_path):
    db = str(tmp_path / "k18.db")
    return UserDAO(db), PersonDAO(db), ChartDAO(db), db


def _mk_person(pdao, uid, birth=None):
    if birth is None:
        birth = {"gender": "男", "birth_year": 1995, "birth_month": 3,
                 "birth_day": 28, "birth_hour": 9, "birth_minute": 0,
                 "calendar": "solar", "city": "吉林省长春市"}
    pdao.create_person(uid, name="我", relation="自己", is_default=True,
                       birth=birth)


def _mk_chart(chart_dao, uid, birth=None, bazi=None, **extra):
    if birth is None:
        birth = dict(CHART_BIRTH)
    if bazi is None:
        bazi = list(CHART_BAZI)
    result = {"bazi": bazi, "day_master": extra.pop("day_master", "戊"),
              "geju": extra.pop("geju", "正官格"), "yongshen": extra.pop("yongshen", "水"),
              **extra}
    chart_dao.save_chart(uid, 1, birth, result)


def _mk_handler(dao, chart_dao, uid, **kw):
    """object.__new__ 装配（真实 DAO/chart_dao，k9 同款）。"""
    from src.bot.handler import MessageHandler
    h = object.__new__(MessageHandler)
    h.dao = dao
    h.chart_dao = chart_dao
    h.memory_system = None
    h.memory = None
    h._analysis_facts = {}
    h._citations = {}
    h._downgraded = {}
    h._deep_night = {}
    h._gender_acks = {}
    h.session_dao = None
    h.llm = None
    h.engine = None
    h.dream_engine = None
    h._emit_stream_event = Mock()
    for k, v in kw.items():
        setattr(h, k, v)
    return h


# ================================================================
# H1 问候：档案存在性走统一链（persons-only 不再误引导建档）
# ================================================================

class TestK18GreetingPersonsFirst:
    def test_greeting_persons_only_says_saved(self, tmp_path):
        """persons 建档、bazi_info 无行 → 「八字信息已保存」（原裸读误判无档案）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "u1")
        h = _mk_handler(dao, chart_dao, "u1")
        reply = h._greeting_reply("u1")
        assert "八字信息已保存" in reply
        # 自愈：persons → bazi_info 单向打通（k8 语义，无 bazi 键）
        assert dao.get_user_bazi("u1")["year"] == 1995

    def test_greeting_bazi_info_only_no_regression(self, tmp_path):
        """仅 bazi_info 有档案 → 文案不回退（② 兜底语义）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        dao.save_user_bazi("u2", {"year": 1995, "month": 3, "day": 28,
                                  "hour": 9, "gender": "男"})
        h = _mk_handler(dao, chart_dao, "u2")
        assert "八字信息已保存" in h._greeting_reply("u2")

    def test_greeting_chart_records_only_says_saved(self, tmp_path):
        """③ 级兜底：无 persons/bazi_info，但排盘落库 → 视为有档案（D8 语义）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_chart(chart_dao, "u3")
        h = _mk_handler(dao, chart_dao, "u3")
        assert "八字信息已保存" in h._greeting_reply("u3")

    def test_greeting_no_archive_default_guide(self, tmp_path):
        """无任何档案 → 默认引导建档文案。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        h = _mk_handler(dao, chart_dao, "u-none")
        assert "直接告诉我您的出生日期" in h._greeting_reply("u-none")

    def test_greeting_split_persons_wins_existence(self, tmp_path):
        """一致性分裂（persons=1995 真档案 vs bazi_info 旧行=2026 他人盘）→
        persons 胜：有档案文案 + 自愈全量重建行（旧行 bazi 键丢弃，21:44 族）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "u5")
        dao.save_user_bazi("u5", {
            "year": 2026, "month": 8, "day": 18, "hour": 0, "minute": 0,
            "city": "北京", "gender": "unknown",
            "bazi": list(POLLUTED_BAZI)})
        h = _mk_handler(dao, chart_dao, "u5")
        assert "八字信息已保存" in h._greeting_reply("u5")
        row = dao.get_user_bazi("u5")
        assert row["year"] == 1995
        assert "bazi" not in row, "k8 自愈重建不得保留旧行 bazi 键"


# ================================================================
# H2 _should_fastpath：「档案」存在性走统一链
# ================================================================

class TestK18FastpathPersonsFirst:
    BIRTH = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
             "city": "北京", "gender": "男"}

    def test_fastpath_persons_only_true(self, tmp_path):
        """persons 建档（无 chart 无 bazi_info 行）→ True（文档语义「或档案时」）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "f1")
        h = _mk_handler(dao, chart_dao, "f1")
        assert h._should_fastpath("f1", self.BIRTH) is True

    def test_fastpath_bazi_info_only_no_regression(self, tmp_path):
        """仅 bazi_info → True（既有语义不回退）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        dao.save_user_bazi("f2", {"year": 1990, "month": 5, "day": 20,
                                  "hour": 15, "gender": "男"})
        h = _mk_handler(dao, chart_dao, "f2")
        assert h._should_fastpath("f2", self.BIRTH) is True

    def test_fastpath_split_persons_wins(self, tmp_path):
        """分裂场景：persons=1995 档案存在 + bazi_info 旧行被他人盘污染 →
        仍按档案 True；且不因污染行 birth 与给定生辰不同而漏判（存在性以
        persons 为事实源）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "f3", birth={"gender": "男", "birth_year": 1995,
                                      "birth_month": 3, "birth_day": 28,
                                      "birth_hour": 9, "birth_minute": 0,
                                      "calendar": "solar",
                                      "city": "吉林省长春市"})
        dao.save_user_bazi("f3", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        h = _mk_handler(dao, chart_dao, "f3")
        assert h._should_fastpath("f3", self.BIRTH) is True

    def test_fastpath_none_false(self, tmp_path):
        """无档案 → False（走原检索路径）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        h = _mk_handler(dao, chart_dao, "u-none")
        assert h._should_fastpath("u-none", self.BIRTH) is False

    def test_fastpath_chart_only_mismatch_false(self, tmp_path):
        """仅 chart_records（无 persons/bazi_info 档案）且 chart birth 与当前
        生辰不匹配 → False（k9 契约：③ 兜底不算档案，他人/择时盘不构成
        跳过预检索的依据；「已存同盘」由上方 chart 比对条件覆盖）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_chart(chart_dao, "f4")          # 1995-03-28 盘（与给定生辰不同）
        h = _mk_handler(dao, chart_dao, "f4")
        assert h._should_fastpath("f4", self.BIRTH) is False


# ================================================================
# H5 _free_chat 出生信息门：persons 建档用户不再被引导建档
# ================================================================

class TestK18FreeChatGatePersonsFirst:
    MSG = "1990年5月20日 下午3点 北京 男"

    def _free_chat_gate(self, dao, chart_dao, uid, handle_bazi_marker="进入更新路径"):
        h = _mk_handler(dao, chart_dao, uid)
        h._downgraded = {uid: True}
        h._rule_analyze = Mock(return_value=Mock(intent="bazi"))
        h._handle_bazi = Mock(return_value=handle_bazi_marker)
        h._collect_partial_birth = Mock(return_value=None)
        return h._free_chat(self.MSG, uid, session_id="s1"), h

    def test_free_chat_persons_only_goes_update_path(self, tmp_path):
        """persons 建档（bazi_info 无行）+ 消息含年份性别 → 走「已有档案可更新」
        路径（原裸读误判无档案 → 反复要求按完整格式建档，T089 同类自由对话面）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "g1")
        reply, h = self._free_chat_gate(dao, chart_dao, "g1")
        assert reply == "进入更新路径"
        h._handle_bazi.assert_called_once()

    def test_free_chat_no_archive_guides_format(self, tmp_path):
        """真无档案 → 原引导文案不变。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        reply, h = self._free_chat_gate(dao, chart_dao, "u-none")
        assert "看起来您可能在提供出生信息" in reply
        h._handle_bazi.assert_not_called()

    def test_free_chat_split_row_still_archive(self, tmp_path):
        """分裂：persons=1995 + bazi_info 旧行 2026 他人盘 → persons 胜（有档案）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "g3")
        dao.save_user_bazi("g3", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown"})
        reply, h = self._free_chat_gate(dao, chart_dao, "g3")
        assert reply == "进入更新路径"


# ================================================================
# H3 解梦：日主个性化只取「匹配 chart_records 盘」（k8 语义）
# ================================================================

class TestK18DreamPillarsChartRecords:
    def _run_dream(self, dao, chart_dao, uid):
        """跑 _do_dream_analysis（dream_engine=None + llm Mock，零网络）。"""
        from src.bot.handler import MessageHandler
        from src.engines.dream import DreamResult
        h = object.__new__(MessageHandler)
        h.dao = dao
        h.chart_dao = chart_dao
        h.dream_engine = None
        h.llm = Mock()
        h.llm.analyze.return_value = Mock(response="梦境解读")
        h._emit_stream_event = Mock()
        h._alloc_citations = Mock(return_value=1)
        h._append_citations = Mock()
        h._mark_card_turn = Mock()
        h.dao.save_consultation = Mock()  # 不依赖 consultations 表形态
        prompt = None

        def _fake_analyze(*args, **kwargs):
            nonlocal prompt
            prompt = args[0]
            return Mock(response="梦境解读")

        h.llm.analyze.side_effect = _fake_analyze
        reply = h._do_dream_analysis("梦见一条大蛇在追我", uid)
        return reply, prompt

    def test_dream_persons_plus_matching_chart_personalized(self, tmp_path):
        """persons + 出生匹配 chart_records → 梦境 prompt 带日柱命理信息。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "d1")
        _mk_chart(chart_dao, "d1")
        _, prompt = self._run_dream(dao, chart_dao, "d1")
        assert "戊午 当前" in prompt            # bazi[2] 日柱串（既有显示形态）
        assert "### 命理信息" in prompt

    def test_dream_persons_only_no_chart_not_personalized(self, tmp_path):
        """persons 建档但从未排盘 → 无命理信息注入（不虚构日主）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "d2")
        _, prompt = self._run_dream(dao, chart_dao, "d2")
        assert "### 命理信息" not in prompt

    def test_dream_split_polluted_row_not_consumed(self, tmp_path):
        """21:44 事故形态：persons=1995 + bazi_info 旧行带他人盘四柱（无匹配
        chart）→ 旧行 bazi 键不被消费（k8 语义：四柱只属于 chart_records）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "d3")
        dao.save_user_bazi("d3", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        _, prompt = self._run_dream(dao, chart_dao, "d3")
        assert "### 命理信息" not in prompt
        assert "甲子" not in prompt

    def test_dream_bazi_info_only_matching_chart_personalized(self, tmp_path):
        """无 persons → bazi_info 兜底 birth 键 + 匹配 chart_records → 个性化。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        dao.save_user_bazi("d4", {"year": 1995, "month": 3, "day": 28,
                                  "hour": 9, "gender": "男"})
        _mk_chart(chart_dao, "d4")
        _, prompt = self._run_dream(dao, chart_dao, "d4")
        assert "戊午 当前" in prompt

    def test_dream_no_archive_not_personalized(self, tmp_path):
        """无档案 → 无命理信息。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _, prompt = self._run_dream(dao, chart_dao, "u-none")
        assert "### 命理信息" not in prompt


# ================================================================
# H4 学堂（对话侧）：个性化四柱只取匹配 chart_records（k8 语义，与
# REST /api/xuetang 同源——原 chat 侧裸读 bazi_info 的残余面）
# ================================================================

class TestK18XuetangChatChartRecords:
    def _run_xuetang(self, dao, chart_dao, uid):
        import src.engines.xuetang as xt
        captured = {}

        def _fake_personalized(topic, bazi_data=None, retriever=None):
            captured["bazi_data"] = bazi_data
            return "PERSONAL"

        def _fake_get_lesson(topic, retriever=None):
            return "GENERIC"

        with patch.object(xt, "personalized_lesson", side_effect=_fake_personalized), \
             patch.object(xt, "get_lesson", side_effect=_fake_get_lesson):
            from src.bot.handler import MessageHandler
            h = object.__new__(MessageHandler)
            h.dao = dao
            h.chart_dao = chart_dao
            reply = h._handle_xuetang("学堂 十神分析", uid)
        return reply, captured

    def test_xuetang_persons_plus_matching_chart_personalized(self, tmp_path):
        """persons + 匹配 chart → 个性化（四柱/盘面键来自 chart_records）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "x1")
        _mk_chart(chart_dao, "x1", day_master="戊", geju="正官格", yongshen="水")
        reply, captured = self._run_xuetang(dao, chart_dao, "x1")
        assert reply == "PERSONAL"
        assert captured["bazi_data"]["bazi"] == CHART_BAZI
        assert captured["bazi_data"]["day_master"] == "戊"

    def test_xuetang_persons_only_generic(self, tmp_path):
        """persons 建档未排盘 → 通用课程（无虚构四柱）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "x2")
        reply, captured = self._run_xuetang(dao, chart_dao, "x2")
        assert reply == "GENERIC"

    def test_xuetang_split_polluted_row_generic(self, tmp_path):
        """21:44 形态：persons=1995 + 旧行他人盘四柱（无匹配 chart）→ 通用。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "x3")
        dao.save_user_bazi("x3", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        reply, captured = self._run_xuetang(dao, chart_dao, "x3")
        assert reply == "GENERIC"
        assert not captured.get("bazi_data")

    def test_xuetang_no_archive_generic(self, tmp_path):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        reply, captured = self._run_xuetang(dao, chart_dao, "u-none")
        assert reply == "GENERIC"


# ================================================================
# A6 hourly REST：日主只取匹配 chart_records（persons 建档不再误报）
# ================================================================

class TestK18HourlyApiPersonsFirst:
    @pytest.fixture(autouse=True)
    def auth(self):
        from src.security.auth import set_auth_handler, AuthHandler
        set_auth_handler(AuthHandler())
        yield
        set_auth_handler(None)

    def _client(self, dao, tmp_path):
        from src.api import hourly as hourly_api
        hourly_api.setup(dao, None)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(hourly_api.router)
        return TestClient(app)

    def _token(self, uid):
        from src.security.auth import JWTHandler
        tok = JWTHandler(os.environ["JWT_SECRET_KEY"]).create_token(uid)
        return {"Authorization": f"Bearer {tok}"}

    def test_hourly_persons_plus_chart_ok(self, tmp_path):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "h1")
        _mk_chart(chart_dao, "h1")
        client = self._client(dao, tmp_path)
        r = client.get("/api/hourly-fortune?date=2026-09-10",
                       headers=self._token("h1"))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["total_slots"] == 12

    def test_hourly_persons_only_no_bazi_not_blocked(self, tmp_path):
        """persons 建档未排盘 → 不再误报 no_bazi（「请先设置八字」为假引导，
        T089 同类 hourly 面）；盘面无日主 → 仍返回插槽（与旧 bazi_info
        无 bazi 键行行为一致）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "h2")
        client = self._client(dao, tmp_path)
        r = client.get("/api/hourly-fortune?date=2026-09-10",
                       headers=self._token("h2"))
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_hourly_split_polluted_row_not_consumed(self, tmp_path):
        """persons=1995 + 旧行他人盘四柱（无匹配 chart）→ 日主不取污染四柱
        （行 bazi 键不消费；若取污染盘，日主=甲子盘的丙）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "h3")
        dao.save_user_bazi("h3", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        client = self._client(dao, tmp_path)
        r = client.get("/api/hourly-fortune?date=2026-09-10",
                       headers=self._token("h3"))
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_hourly_no_archive_no_bazi(self, tmp_path):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        client = self._client(dao, tmp_path)
        r = client.get("/api/hourly-fortune?date=2026-09-10",
                       headers=self._token("u-none"))
        assert r.status_code == 200
        assert r.json()["status"] == "no_bazi"


# ================================================================
# A4/A5 dashboard：profile + calendar_today 读统一链（persons-first）
# ================================================================

class TestK18DashboardReads:
    def _handler_stub(self, dao):
        stub = Mock()
        stub.dao = dao
        stub.preference_dao = None
        stub.llm = None
        return stub

    def test_dashboard_persons_plus_chart(self, tmp_path):
        from src.api.dashboard import build_dashboard
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "u1")
        _mk_chart(chart_dao, "u1")
        dash = build_dashboard("u1", self._handler_stub(dao))
        prof = dash["profile"]
        assert prof["has_bazi"] is True
        assert prof["bazi"] == " ".join(CHART_BAZI)
        assert "戊" in prof["day_master"]
        assert "1995年3月28日" in prof["birth_info"]
        assert prof["gender"] == "男"

    def test_dashboard_persons_only_no_chart(self, tmp_path):
        from src.api.dashboard import build_dashboard
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "u2")
        dash = build_dashboard("u2", self._handler_stub(dao))
        prof = dash["profile"]
        assert prof["has_bazi"] is True        # 有档案（persons）→ 不再误报未设置
        assert prof["birth_info"] == "1995年3月28日"

    def test_dashboard_split_polluted_row_not_consumed(self, tmp_path):
        from src.api.dashboard import build_dashboard
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "u3")
        dao.save_user_bazi("u3", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        dash = build_dashboard("u3", self._handler_stub(dao))
        prof = dash["profile"]
        assert prof["birth_info"] == "1995年3月28日"  # persons 胜（birth）
        assert "丙午" not in prof["bazi"]              # 污染四柱不被消费
        assert "甲子" not in prof["bazi"]

    def test_dashboard_no_archive_hint(self, tmp_path):
        from src.api.dashboard import build_dashboard
        dao, pdao, chart_dao, _ = _db(tmp_path)
        dash = build_dashboard("u-none", self._handler_stub(dao))
        assert dash["profile"]["has_bazi"] is False
        assert "告诉我你的出生日期" in dash["profile"]["hint"]


# ================================================================
# A1/A2/A3 main.py legacy：calendar/daily、/week、/share-card
# ================================================================

class TestK18MainLegacyEndpoints:
    """直调端点函数（uid 直接传参绕过 Depends）+ 模块级 handler stub；
    LuckyCalendar.daily/week 打桩（零 LLM），只验读取链喂给引擎的档案形态。"""

    def _make_ctx(self, tmp_path, dao, monkeypatch):
        import src.main as m
        from src.engines.calendar import LuckyCalendar, CalendarDay
        m.handler = self._handler_stub(dao)
        captured = {}

        def _fake_daily(self_, user_bazi, date_str=None, preferences=""):
            captured["daily_bazi"] = dict(user_bazi)
            return CalendarDay(date=date_str or "2026-09-10",
                               day_stem="甲", day_branch="子",
                               yi=[{"action": "宜测试"}], ji=[],
                               lucky_color="红", lucky_direction="南",
                               lucky_number="9", overall_mood="稳")

        def _fake_week(self_, user_bazi, preferences=""):
            captured["week_bazi"] = dict(user_bazi)
            return [_fake_daily(self_, user_bazi, "2026-09-10")]

        monkeypatch.setattr(LuckyCalendar, "daily", _fake_daily)
        monkeypatch.setattr(LuckyCalendar, "week", _fake_week)
        return m, captured

    def _handler_stub(self, dao):
        stub = Mock()
        stub.dao = dao
        stub.llm = None
        return stub

    def _req(self, date="2026-09-10"):
        return type("Req", (), {"date": date})()

    def test_daily_persons_only_ok_and_feeds_full(self, tmp_path, monkeypatch):
        """persons 建档（bazi_info 无行）→ status ok（原误报 no_bazi）；
        engine 收到的是全量形态（匹配 chart 四柱）。"""
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "c1")
        _mk_chart(chart_dao, "c1")
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_daily_calendar(self._req(), uid="c1"))
        assert r["status"] == "ok"
        assert captured["daily_bazi"]["bazi"] == CHART_BAZI

    def test_daily_split_polluted_row_uses_persons_birth(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "c2")
        dao.save_user_bazi("c2", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_daily_calendar(self._req(), uid="c2"))
        assert r["status"] == "ok"
        # 无匹配 chart → 四柱空（污染旧行不被消费）
        assert captured["daily_bazi"].get("bazi") is None
        assert captured["daily_bazi"].get("year") == 1995  # persons birth 胜

    def test_daily_no_archive_no_bazi(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_daily_calendar(self._req(), uid="u-none"))
        assert r["status"] == "no_bazi"
        assert "calendar" in r and r["calendar"]  # 通用日历兜底

    def test_week_persons_only_ok(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "c3")
        _mk_chart(chart_dao, "c3")
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_week_calendar(self._req(), uid="c3"))
        assert r["status"] == "ok"
        assert captured["week_bazi"]["bazi"] == CHART_BAZI

    def test_week_no_archive_no_bazi(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_week_calendar(self._req(), uid="u-none"))
        assert r["status"] == "no_bazi"

    def test_share_card_persons_plus_chart(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "s1")
        _mk_chart(chart_dao, "s1", day_master="戊")
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_share_card("s1", uid="s1"))
        assert r["status"] == "ok"
        assert r["bazi"] == " ".join(CHART_BAZI)
        assert r["day_master"] == "戊"

    def test_share_card_split_polluted_row_not_shown(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        _mk_person(pdao, "s2")
        dao.save_user_bazi("s2", {"year": 2026, "month": 8, "day": 18,
                                  "hour": 0, "gender": "unknown",
                                  "bazi": list(POLLUTED_BAZI)})
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_share_card("s2", uid="s2"))
        assert r["status"] == "ok"
        assert r["bazi"] == "?"           # 无匹配 chart → 不显示污染四柱
        assert "丙午" not in r["share_text"]
        assert "甲子" not in r["share_text"]

    def test_share_card_no_archive_no_bazi(self, tmp_path, monkeypatch):
        dao, pdao, chart_dao, _ = _db(tmp_path)
        m, captured = self._make_ctx(tmp_path, dao, monkeypatch)
        r = asyncio.run(m.get_share_card("u-none", uid="u-none"))
        assert r["status"] == "no_bazi"
