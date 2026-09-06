"""k9 R2-5/R2-6 遗留 Minor 收敛批测试（分支 k9-r2-minors，base main=2ac1fc5）。

覆盖（逐项对应批次）：
1. format_birth_line 直渲染矛盾（R2-6 遗留①）——lunar 档案行渲染公历口径、
   转换失败保留原始日期并显式「农历」标注（绝不冒充公历）；solar/无键零回退。
   + _collect_key_facts 集成：LLM 上下文出生行不再与画像公历行（5/13）矛盾。
2. lunarDateToSolar 三处重复收敛 —— miniprogram JS 侧，见 miniprogram/tests/
   lunar_date_util.test.js + paipan_lunar_date.test.js 锚定更新（本文件不涉）。
3. 记忆层写侧 calendar 标记（R2-5 Minor①）——核实结论：17e0961+R2-5 已闭环
   （两处写侧均写引擎实收公历值、记忆层恒公历口径缺省 solar；calendar 标记只
   属存储层原始值）。本文件补锁定测试：_tool_bazi lunar 档案流 → 记忆层
   bazi_info/L3 = 公历 5/13（memory_system=None 的旧装配盲区）。
4. _handle_bazi ~4679 残余农历直喂 —— 核实结论：R2-6 Gap B 已全路径归一
   （B1/B2/F2/三方/parsed 全部经 _feed_birth/_extract_bazi_info 公历化），
   行为由 tests/test_bazi_residual_paths.py 锁定；本批只补 item 6 的 B1
   solar 档案+农历月日覆写跨年边角（最后未铺路径）。
5. 扩展点行为测试（R2-6 Minor②）：_should_fastpath 补 lunar chart 行比对
   语义；_handle_advisor/_handle_hourly/_map_user_bazi_for_zeri 补直接行为
   测试（临时库真实 DAO/PersonDAO + 记录式真实引擎或 Mock，零 LLM 零网络）。
6. B1 solar 档案 + 农历月日覆写跨年边角（R2-6 Minor③ edge-of-edge）：
   档案 year 按农历年语义换算可跨公历年（1999-12-26 腊月廿六 → 2000-02-01），
   引擎实收公历、落库保留原始值+标记；规则与 D7/to_solar_date 同约定。
7. 测试注释锚定（R2-5 Minor②）：9 点 → 「出生后2年4月12天0时起运」vs 11 点 →
   「出生后2年4月22天0时起运」（问真逐字各自验证）：同月同日同「2年4月」前缀、
   天数恰差 10 天——真太阳时修正后两时辰（巳/午）的正常口径差异，非舍入误差，
   勿互相「修齐」。本文件两条校准断言锁定 + tests/test_bazi_residual_paths.py
   常量处补注释。

运行：cd /mnt/e/fortune-agent-deploy && OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k9_r2_minors.py -q \
  -p no:cacheprovider（import 冷启动 1-2 分钟正常）
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

# ── 校准常量（问真逐字；1999-05-13 长春，真太阳时修正后仍巳/午时域）─────
Qiyun_9H = "出生后2年4月12天0时起运"   # hour=9
Qiyun_11H = "出生后2年4月22天0时起运"  # hour=11（问真逐字，R2-6 北极星）


# ================================================================
# 装配（object.__new__ + 真实 DAO/PersonDAO/ChartDAO/引擎，R2-5/R2-6 同款）
# ================================================================

class _RecordingEngine:
    """记录 calculate 实参并透传真实 BaziEngine 结果。"""

    def __init__(self, real=None):
        if real is None:
            from src.engines.bazi import BaziEngine
            real = BaziEngine()
        self.real = real
        self.calls = []
        self.results = []

    def calculate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        r = self.real.calculate(*args, **kwargs)
        self.results.append(r)
        return r


def _make_handler(tmp_path, uid, engine=None, downgraded=True, memory=False):
    """真实 DAO/chart_dao/PersonDAO 装配；downgraded=True → 确定性 0 LLM 主链。"""
    from src.bot.handler import MessageHandler
    from src.storage.dao import UserDAO
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(str(tmp_path / "p.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.memory_system = None
    h.memory = None
    h._analysis_facts = {}
    h._citations = {}
    h._downgraded = {uid: True} if downgraded else {}
    h._deep_night = {}
    h._gender_acks = {}
    h.session_dao = None
    h.llm = None
    h.engine = engine
    if memory:
        from src.memory.user_memory import UserMemory
        h.memory_system = UserMemory(base_dir=str(tmp_path / "mem"))
    return h


def _create_person(pdao, uid, y, m, d, hour=9, cal="lunar", city="吉林省长春市",
                   gender="男"):
    pdao.create_person(
        uid, name="我", relation="自己", is_default=True,
        birth={"gender": gender, "birth_year": y, "birth_month": m,
               "birth_day": d, "birth_hour": hour, "birth_minute": None,
               "calendar": cal, "city": city})


# ================================================================
# 1) format_birth_line 公历口径（单点：渲染=存储原始输入事实源→公历）
# ================================================================

class TestFormatBirthLineCalendar:
    def _fmt(self, bazi):
        from src.memory.user_memory import format_birth_line
        return format_birth_line(bazi)

    def test_lunar_archive_renders_solar_date(self):
        """lunar 档案（原始 1999-3-28 + calendar 标记）→ 出生行日期 = 公历
        5/13（与画像公历行同口径，修复前直渲染 3/28 → 断言必失败）。"""
        out = self._fmt({"year": 1999, "month": 3, "day": 28, "hour": 9,
                         "minute": None, "city": "吉林省长春市", "gender": "男",
                         "calendar": "lunar"})
        assert "出生:1999年5月13日 巳时 吉林省长春市 男(来自用户档案)" == out, out
        assert "3月28日" not in out

    def test_lunar_illegal_date_keeps_raw_with_marker(self):
        """lunar 但转换失败（1999-03-30 农历三月仅 29 天）→ 保留原始日期 +
        显式「农历」前缀——值仍是农历时按需标注，绝不冒充公历。"""
        out = self._fmt({"year": 1999, "month": 3, "day": 30, "hour": 9,
                         "city": "吉林省长春市", "gender": "男",
                         "calendar": "lunar"})
        assert out == "出生:农历1999年3月30日 巳时 吉林省长春市 男(来自用户档案)", out

    def test_solar_byte_identical(self):
        """solar 档案 → 逐字节不变（零行为回退）。"""
        out = self._fmt({"year": 1990, "month": 5, "day": 20, "hour": 15,
                         "minute": 30, "city": "北京", "gender": "男",
                         "calendar": "solar"})
        assert out == "出生:1990年5月20日 申时 北京 男(来自用户档案)", out

    def test_legacy_no_calendar_defaults_solar_byte_identical(self):
        """旧行无 calendar 键 → 缺省 solar 语义 → 原样渲染（含农历原值也不转，
        与存储读取口径一致：无标记=公历）。"""
        out = self._fmt({"year": 1990, "month": 5, "day": 20, "hour": 15,
                         "city": "北京", "gender": "男"})
        assert out == "出生:1990年5月20日 申时 北京 男(来自用户档案)", out

    def test_memory_layer_rows_unchanged(self):
        """记忆层 bazi_info 行（17e0961 后=引擎实收公历、无 calendar 键）→
        零变化（residual 测试 section7 的 summary 断言依赖此语义）。"""
        out = self._fmt({"year": 1999, "month": 5, "day": 13, "hour": 11,
                         "minute": None, "city": "吉林省长春市", "gender": "男",
                         "bazi": ["己卯", "己巳", "乙丑", "壬午"]})
        assert out == "出生:1999年5月13日 午时 吉林省长春市 男(来自用户档案)", out

    def test_empty_and_none(self):
        from src.memory.user_memory import format_birth_line
        assert format_birth_line(None) == ""
        assert format_birth_line({}) == ""


class TestCollectKeyFactsLunarProfile:
    """_collect_key_facts 集成：LLM 上下文出生行 = 公历，与画像公历行不再同框矛盾。"""

    def test_lunar_person_birth_line_solarized(self, tmp_path):
        from src.storage.person_dao import PersonDAO
        h = _make_handler(tmp_path, "u-cf1")
        _create_person(PersonDAO(h.dao.db_path), "u-cf1", 1999, 3, 28)
        facts = h._collect_key_facts("u-cf1")
        assert any("出生:1999年5月13日 巳时 吉林省长春市 男(来自用户档案)" == f
                   for f in facts), facts
        assert not any("1999年3月28日" in f for f in facts), facts

    def test_solar_person_unchanged(self, tmp_path):
        from src.storage.person_dao import PersonDAO
        h = _make_handler(tmp_path, "u-cf2")
        _create_person(PersonDAO(h.dao.db_path), "u-cf2", 1990, 5, 20,
                       hour=15, cal="solar", city="北京")
        facts = h._collect_key_facts("u-cf2")
        assert any("出生:1990年5月20日 申时 北京 男(来自用户档案)" == f
                   for f in facts), facts


# ================================================================
# 3) 记忆层写侧锁定（核实=已闭环；本测试堵 memory_system=None 盲区）
# ================================================================

class TestToolBaziMemoryLayerWrites:
    def test_lunar_archive_memory_bazi_info_solar_and_l3_solar(self, tmp_path):
        """_tool_bazi lunar 档案流 → 记忆画像层 bazi_info/L3 = 引擎实收公历
        5/13（无 lunar 标记——记忆层=LLM 上下文、恒公历口径）；档案三处
        （dao/persons/chart）原始 3/28 + calendar lunar 不动（复核）。"""
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        h = _make_handler(tmp_path, "u-mw1", engine=rec, memory=True)
        _create_person(PersonDAO(h.dao.db_path), "u-mw1", 1999, 3, 28)

        tr = h._tool_bazi("帮我排个盘", "u-mw1")
        assert tr.ok, tr.text
        args = rec.calls[0][0]
        assert args[:3] == (1999, 5, 13)
        # 记忆画像层 bazi_info：公历 5/13，calendar != lunar（缺省 solar 语义）
        data = h.memory_system._load("u-mw1")
        bi = data.get("bazi_info") or {}
        assert (bi.get("year"), bi.get("month"), bi.get("day")) == (1999, 5, 13), bi
        assert bi.get("calendar") != "lunar"
        # L3 profile 条目：公历文本
        entries = h.memory_system.list_entries("u-mw1", entry_type="profile")
        assert entries and any("1999年5月13日" in (e.get("content") or "")
                               for e in entries), entries
        assert not any("1999年3月28日" in (e.get("content") or "")
                       for e in entries), entries
        # 存储层三处原始值 + 标记（复核铁律不动）
        bazi = h.dao.get_user_bazi("u-mw1")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1999, 3, 28)
        assert bazi.get("calendar") == "lunar"

    def test_solar_archive_memory_unchanged(self, tmp_path):
        """solar 档案对照：记忆层 = 原值，无 lunar 标记，零行为回退。"""
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        h = _make_handler(tmp_path, "u-mw2", engine=rec, memory=True)
        _create_person(PersonDAO(h.dao.db_path), "u-mw2", 1990, 5, 20,
                       hour=15, cal="solar", city="北京")
        tr = h._tool_bazi("帮我排个盘", "u-mw2")
        assert tr.ok, tr.text
        assert rec.calls[0][0][:3] == (1990, 5, 20)
        bi = (h.memory_system._load("u-mw2") or {}).get("bazi_info") or {}
        assert (bi.get("year"), bi.get("month"), bi.get("day")) == (1990, 5, 20), bi
        assert bi.get("calendar") != "lunar"


# ================================================================
# 5) 扩展点行为测试
# ================================================================

class TestFastpathLunarChartMatch:
    """_should_fastpath（R2-6 后 chart 行 lunar 原始值比对前转公历）语义。"""

    def _h(self, chart=None, dao_bazi=None):
        from src.bot.handler import MessageHandler
        h = object.__new__(MessageHandler)
        h.chart_dao = Mock()
        h.chart_dao.get_latest_chart.return_value = chart
        h.dao = Mock()
        h.dao.get_user_bazi.return_value = dao_bazi
        return h

    def test_lunar_chart_row_matches_solar_birth_true(self):
        """chart 行 birth = lunar 原始 1999-3-28（带标记）vs 本轮引擎生辰公历
        (1999,5,13) → 转公历比对相等 → True（同生辰 lunar 用户不白跑 RAG）。"""
        h = self._h(chart={"id": 1, "birth": {"year": 1999, "month": 3, "day": 28,
                                              "hour": 9, "calendar": "lunar"},
                           "bazi_json": {}, "created_at": "2026-09-01"},
                    dao_bazi=None)
        assert h._should_fastpath("u1", {"year": 1999, "month": 5, "day": 13,
                                         "hour": 9, "minute": 0, "city": "长春",
                                         "gender": "男"}) is True

    def test_lunar_chart_mismatch_falls_to_dao(self):
        """chart lunar 1999-3-28 vs 生辰公历 (2000,2,1)（不同盘）→ 不由 chart
        走快路径；dao 无档案 → False（走原检索路径）。"""
        h = self._h(chart={"id": 2, "birth": {"year": 1999, "month": 3, "day": 28,
                                              "calendar": "lunar"},
                           "bazi_json": {}, "created_at": "2026-09-01"},
                    dao_bazi=None)
        assert h._should_fastpath("u2", {"year": 2000, "month": 2, "day": 1,
                                         "hour": 9, "minute": 0}) is False

    def test_lunar_chart_mismatch_dao_archive_true(self):
        """chart 不匹配但 dao 有档案 → 仍 True（既有语义回归）。"""
        h = self._h(chart={"id": 3, "birth": {"year": 1999, "month": 3, "day": 28,
                                              "calendar": "lunar"},
                           "bazi_json": {}, "created_at": "2026-09-01"},
                    dao_bazi={"year": 2000, "bazi": ["庚辰"]})
        assert h._should_fastpath("u3", {"year": 2000, "month": 2, "day": 1}) is True


class TestMapUserBaziForZeri:
    """_map_user_bazi_for_zeri：lunar 档案（无四柱）→ 引擎补全前单点转公历。"""

    def _map(self, tmp_path, uid, y, m, d, cal="lunar"):
        from src.storage.person_dao import PersonDAO
        from src.storage.dao import UserDAO
        rec = _RecordingEngine()
        h = _make_handler(tmp_path, uid, engine=rec)
        dao = h.dao
        # 直接落 dao bazi_info（raw + calendar）→ get_user_bazi 无 bazi 键
        dao.save_user_bazi(uid, {"year": y, "month": m, "day": d, "hour": 9,
                                 "minute": None, "city": "吉林省长春市",
                                 "gender": "男", "calendar": cal})
        return h, rec, dao

    def test_lunar_archive_engine_receives_solar(self, tmp_path):
        h, rec, _ = self._map(tmp_path, "u-zi", 1999, 3, 28)
        ub = h._map_user_bazi_for_zeri("u-zi")
        args = rec.calls[0][0]
        assert args[:5] == (1999, 5, 13, 9, 0), \
            f"引擎应收公历 (1999,5,13,9,0)，实收 {args[:5]}"
        # 公历盘 1999-05-13：年柱己卯 → 兔；日干乙；月支巳
        assert ub and ub["shengxiao"] == "兔", ub
        assert ub["day_gan"] == "乙", ub
        assert ub["month_zhi"] == "巳", ub
        assert ub.get("wuxing") and sum(ub["wuxing"].values()) > 0, ub

    def test_lunar_cross_year_solar_flip(self, tmp_path):
        """农历 2000-12-26（腊月廿六）→ 公历 2001-01-20（跨公历年！）→ 引擎
        实收 (2001,1,20)；生肖按立春前年柱（庚辰 → 龙），day_gan=癸 日、月支丑。"""
        h, rec, _ = self._map(tmp_path, "u-zi2", 2000, 12, 26)
        ub = h._map_user_bazi_for_zeri("u-zi2")
        args = rec.calls[0][0]
        assert args[:3] == (2001, 1, 20), f"跨年换算错误 {args[:3]}"
        assert ub["shengxiao"] == "龙", ub   # 立春(2001-02-04)前 → 年支辰
        assert ub["day_gan"] == "癸", ub     # 癸未日
        assert ub["month_zhi"] == "丑", ub   # 己丑月

    def test_solar_archive_no_conversion(self, tmp_path):
        h, rec, _ = self._map(tmp_path, "u-zi3", 1990, 5, 20, cal="solar")
        h._map_user_bazi_for_zeri("u-zi3")
        assert rec.calls[0][0][:3] == (1990, 5, 20)

    def test_no_archive_returns_none(self, tmp_path):
        h = _make_handler(tmp_path, "u-zi4")
        assert h._map_user_bazi_for_zeri("u-zi4") is None

    def test_engine_failure_fallbacks_none(self, tmp_path):
        """引擎异常 → None 兜底不误伤（zeri 引擎无八字兜底 24 分路径）。"""
        h = _make_handler(tmp_path, "u-zi5")
        h.dao.save_user_bazi("u-zi5", {"year": 1999, "month": 3, "day": 28,
                                       "hour": 9, "city": "长春", "gender": "男",
                                       "calendar": "lunar"})
        boom = Mock()
        boom.calculate.side_effect = RuntimeError("boom")
        h.engine = boom
        assert h._map_user_bazi_for_zeri("u-zi5") is None


class TestHandleAdvisor:
    """_handle_advisor：lunar 档案重排盘前单点转公历（引擎契约=公历）+ 失败 fail-open。"""

    def _handler(self, tmp_path, uid, engine=None):
        import src.bot.handler as handler_mod
        h = _make_handler(tmp_path, uid, engine=engine, downgraded=False)
        h._add_feedback_prompt = lambda s: s  # 去耦合末尾模板
        return h, handler_mod

    def _save_lunar(self, dao, uid):
        dao.save_user_bazi(uid, {"year": 1999, "month": 3, "day": 28, "hour": 9,
                                 "minute": None, "city": "吉林省长春市",
                                 "gender": "男", "calendar": "lunar"})

    def test_no_archive_fixed_prompt(self, tmp_path):
        h, _ = self._handler(tmp_path, "u-adv0")
        out = h._handle_advisor("给我点建议", "u-adv0")
        assert "想为你生成专属建议" in out

    def test_lunar_archive_engine_receives_solar_and_reply(self, tmp_path, monkeypatch):
        import src.bot.handler as handler_mod
        class _FakeAdvisor:
            def generate(self, result, user_context="", api_key=""):
                return {
                    "actions": [{"category": "事业", "advice": "稳中求进",
                                 "timing": "秋季", "confidence": "high",
                                 "concrete_steps": "先复盘", "success_metric": "月增"}],
                    "serendipity": "随机一句话", "daily_tip": "今日宜静",
                    "style_notes": "",
                }
        monkeypatch.setattr(handler_mod, "AdaptiveAdvisor", _FakeAdvisor)
        rec = _RecordingEngine()
        h, _ = self._handler(tmp_path, "u-adv1", engine=rec)
        self._save_lunar(h.dao, "u-adv1")
        out = h._handle_advisor("最近工作压力大，给点建议", "u-adv1")
        args = rec.calls[0][0]
        assert args[:3] == (1999, 5, 13), \
            f"引擎应收公历 (1999,5,13)，实收 {args[:3]}（修复前 3/28 → 全链错）"
        assert "稳中求进" in out and "事业" in out

    def test_engine_failure_fail_open(self, tmp_path):
        boom = Mock()
        boom.calculate.side_effect = RuntimeError("engine down")
        h, _ = self._handler(tmp_path, "u-adv2", engine=boom)
        self._save_lunar(h.dao, "u-adv2")
        out = h._handle_advisor("给点建议", "u-adv2")
        assert "命盘重新计算失败" in out

    def test_advisor_failure_fail_open(self, tmp_path, monkeypatch):
        import src.bot.handler as handler_mod
        class _BoomAdvisor:
            def generate(self, *a, **k):
                raise RuntimeError("LLM down")
        monkeypatch.setattr(handler_mod, "AdaptiveAdvisor", _BoomAdvisor)
        h, _ = self._handler(tmp_path, "u-adv3", engine=_RecordingEngine())
        self._save_lunar(h.dao, "u-adv3")
        out = h._handle_advisor("给点建议", "u-adv3")
        assert "AI 建议生成失败" in out


class TestHandleHourly:
    """_handle_hourly：persons-only lunar 档案缺四柱 → 引擎补齐前转公历。"""

    def test_no_profile_fixed_prompt(self, tmp_path):
        h = _make_handler(tmp_path, "u-h0")
        out = h._handle_hourly("今日时辰运势", "u-h0")
        assert "⏰" in out or "想查看今日十二时辰运势" in out

    def test_lunar_person_fill_engine_receives_solar(self, tmp_path):
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        h = _make_handler(tmp_path, "u-h1", engine=rec)
        _create_person(PersonDAO(h.dao.db_path), "u-h1", 1999, 3, 28)
        out = h._handle_hourly("今日时辰运势", "u-h1")
        assert rec.calls, "缺四柱必须引擎补齐"
        args = rec.calls[0][0]
        assert args[:3] == (1999, 5, 13), \
            f"补齐应收公历 (1999,5,13)，实收 {args[:3]}"
        assert args[3] == 9 and args[5] == "吉林省长春市"
        # llm=None → 确定性卡片回复不崩、无 LLM 调用
        assert out and len(out) > 20
        assert "时" in out


# ================================================================
# 6) B1：solar 档案 + 农历月日覆写跨年边角（最后未铺路径）
# ================================================================

class TestB1SolarArchiveLunarMdCrossYear:
    def test_lunar_md_overwrite_crosses_solar_year(self, tmp_path):
        """solar 档案 1999-05-13 9:00 + 消息补「农历腊月廿六」（cur 覆写月日 →
        calendar 随覆写置 lunar，年份按农历年语义 1999）→ to_solar_date(1999,12,26)
        = 2000-02-01 跨公历年 → 引擎实收 (2000,2,1,9,0)，落库保留原始 12/26 +
        calendar lunar，chart 为公历盘（己丑日）。"""
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        h = _make_handler(tmp_path, "u-x1", engine=rec)
        pdao = PersonDAO(h.dao.db_path)
        _create_person(pdao, "u-x1", 1999, 5, 13, cal="solar", city="吉林省长春市")

        out = h._handle_bazi("我是农历腊月廿六出生的", "u-x1")
        assert rec.calls, "md 覆写齐全必须自动排盘"
        args = rec.calls[0][0]
        assert args[:5] == (2000, 2, 1, 9, 0), \
            f"引擎应收 (2000,2,1,9,0)（跨年转换），实收 {args[:5]}"
        assert "排盘" in out or "命局" in out
        # 落库三处保留原始 1999-12-26 + calendar lunar（arch_raw 铁律）
        bazi = h.dao.get_user_bazi("u-x1")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1999, 12, 26)
        assert bazi.get("calendar") == "lunar"
        p = pdao.get_default_person("u-x1")
        assert (p["birth_year"], p["birth_month"], p["birth_day"]) == (1999, 12, 26)
        assert p["calendar"] == "lunar"
        chart = h.chart_dao.get_latest_chart("u-x1")
        assert chart is not None
        assert (chart["birth"]["year"], chart["birth"]["month"],
                chart["birth"]["day"]) == (1999, 12, 26)
        assert chart["birth"]["calendar"] == "lunar"
        # chart 盘 = 公历盘（引擎实收 = 2000-02-01 → 己丑日），非 1999-12-26 盘
        assert chart["bazi_json"]["bazi"] == list(rec.results[0].bazi)
        assert chart["bazi_json"]["bazi"][2] == "己丑"

    def test_repeat_chart_after_update_consistent(self, tmp_path):
        """上述流更新档案为 lunar 1999-12-26 后，再次排盘 → 引擎仍收公历
        2000-02-01（存储原始值+标记不自毁，二次消费同口径）。"""
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        h = _make_handler(tmp_path, "u-x2", engine=rec)
        _create_person(PersonDAO(h.dao.db_path), "u-x2", 1999, 5, 13,
                       cal="solar", city="吉林省长春市")
        h._handle_bazi("我是农历腊月廿六出生的", "u-x2")
        out2 = h._handle_bazi("帮我排个盘", "u-x2")
        assert rec.calls and rec.calls[-1][0][:3] == (2000, 2, 1)
        assert "排盘" in out2 or "命局" in out2


# ================================================================
# 7) 校准注释锚定：9 点 vs 11 点起运关系（问真逐字各自验证）
# ================================================================

class TestQiyunHour9Vs11Calibration:
    def test_calibration_pair(self):
        """同月同日（1999-05-13 长春，问真基准）两时辰：
        - hour=9  → 「2年4月12天0时起运」  （真太阳时修正后仍巳时域）
        - hour=11 → 「2年4月22天0时起运」（R2-6 北极星，午时）
        同为「出生后2年4月」前缀、天数恰差 10 天——两时辰节气距分差导致的正
        常口径差异，两数均已对问真逐字验证，勿互相「修齐」（R2-5 Minor②）。"""
        from src.engines.bazi import BaziEngine
        e = BaziEngine()
        r9 = e.calculate(1999, 5, 13, 9, 0, "长春", "男")
        r11 = e.calculate(1999, 5, 13, 11, 0, "长春", "男")
        assert r9.qiyun_desc == Qiyun_9H, r9.qiyun_desc
        assert r11.qiyun_desc == Qiyun_11H, r11.qiyun_desc
        # 同岁月前缀 + 整 10 天差（自洽复核，非断言引擎实现细节）
        assert r9.qiyun_desc.startswith("出生后2年4月")
        assert r11.qiyun_desc.startswith("出生后2年4月")
