# -*- coding: utf-8 -*-
"""k51（P0）：裸城市名不识别 → 引擎缺省"北京"覆盖档案城市。

现象：识别面原为「`XX市` 尾缀」∪「37 城 `COMMON_CITIES` 裸名」——31 个省会里
**只有"长春"落空**，另有 80 个 `CITY_LONGLAT` 地级市裸名不识别。经度差 → 真太阳时
差 ~36min → **时柱不同**（1999-05-13 10:55 长春=壬午 / 缺省北京=辛巳）。

修法（两条一起）：
① 识别面：复用既有 `CITY_LONGLAT`（**不扩经纬度表**——k50-2 红线：库只供经纬度）；
② 采纳面：裸名（无尾缀）要过 `_bare_city_adoptable` 门——
   a. 同小句出生地谓语（`_BIRTH_PLACE_WORD_RE` 经 `person_dao.birth_ctx_near` 小句
      作用域；**去掉**居住/祖籍词 老家/户籍/籍贯/来自）；
   b. 行程形态（相对未来锚 / 全未来日期）一律不采纳；
   c. 四要素齐（日期+时间+地名+性别）；
   d. 整条消息就是这个地名（F2 会话式应答，与日期侧 ⑤d 同源判据）。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零网络零 LLM（降级档）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler, _city_name_re  # noqa: E402
from src.engines.bazi import CITY_LONGLAT, BaziEngine  # noqa: E402
from src.engines.message_analyzer import MessageAnalyzer  # noqa: E402

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")


def _ext(msg):
    return object.__new__(MessageHandler)._extract_partial_birth(msg)


def _info(msg):
    return object.__new__(MessageHandler)._extract_bazi_info(msg)


def _mk_archive(db_path, user_id="u1", **over):
    from src.storage.person_dao import PersonDAO
    b = {"gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
         "birth_hour": 10, "birth_minute": 55, "calendar": "solar", "city": "长春"}
    b.update(over)
    return PersonDAO(db_path).create_person(
        user_id, name="我", relation="自己", is_default=True, birth=b)


def _h(tmp_path, seed=ARCHIVE, user="u1"):
    from src.storage.chart_dao import ChartDAO
    from src.storage.dao import UserDAO
    db = str(tmp_path / "p.db")
    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.ziwei_engine = None
    h.llm = Mock()
    h.llm.api_key = ""
    h.dao = UserDAO(db)
    h.session_dao = None
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.memory = None
    h.memory_system = None
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.member_dao = None
    h._downgraded = {user: True}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._citations = {}
    h._fact_ctx = {}
    h._pregen_instant = {}
    h._consume_pregen_instant = Mock(return_value="")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="[复用档案]")
    if seed:
        _mk_archive(db, user, **seed)
    return h, db


def _snap(db, user_id="u1"):
    from src.storage.person_dao import PersonDAO
    p = PersonDAO(db).get_default_person(user_id)
    return None if not p else (p["birth_year"], p["birth_month"],
                               p["birth_day"], p["city"])


# ════════════════════════════════════════════════════════════════
# ① 识别面：117 城穷举（裸名；不扩经纬度表）
# ════════════════════════════════════════════════════════════════
class TestBareCityRecognition:
    def test_all_117_recognized_in_birth_clause(self):
        """`我出生在{city}` → 117 城**零落空**（改前：长春等 81 城不识别）。"""
        miss = [c for c in CITY_LONGLAT if _ext("我出生在%s" % c).get("city") != c]
        assert not miss, miss

    def test_all_common_cities_recognized(self):
        h = object.__new__(MessageHandler)
        miss = [c for c in sorted(h.COMMON_CITIES) if _ext("我出生在%s" % c).get("city") != c]
        assert not miss, miss

    def test_recognizer_covers_both_tables(self):
        """识别面 = COMMON_CITIES ∪ CITY_LONGLAT（单一来源，无第二套词表）。"""
        h = object.__new__(MessageHandler)
        rx = _city_name_re(h.COMMON_CITIES)
        missing = [c for c in list(CITY_LONGLAT) + sorted(h.COMMON_CITIES)
                   if not rx.search(c)]
        assert not missing, missing

    def test_longest_first(self):
        """最长优先：`乌鲁木齐` 不被前 3 字抢先。"""
        h = object.__new__(MessageHandler)
        assert _ext("我出生在乌鲁木齐").get("city") == "乌鲁木齐"

    def test_no_new_longlat_entries(self):
        """红线（k50-2）：不扩经纬度表——117 城仍是 117。"""
        assert len(CITY_LONGLAT) == 117

    def test_all_117_cleaning_untouched(self):
        """k50-2 口径不回退：库内名字不因形态判据被改。"""
        from src.bot.handler import _clean_city_name
        bad = [c for c in CITY_LONGLAT if _clean_city_name(c) != c]
        assert not bad, bad


# ════════════════════════════════════════════════════════════════
# ② 采纳门 a：同小句出生地谓语
# ════════════════════════════════════════════════════════════════
class TestBirthClauseAdoption:
    @pytest.mark.parametrize("msg,expect", [
        ("我1999年5月13日10点55分在长春生的 男", "长春"),   # brief 现象原文
        ("我出生在长春", "长春"),                            # brief 点名必取
        ("我生在沈阳", "沈阳"),                              # 既有支持形态
        ("我是长春人", None),                                # 无出生地谓语（不取）
        ("我出生于乌鲁木齐", "乌鲁木齐"),
        ("我出生地在三亚", "三亚"),
        ("我出生在长春市", "长春市"),                        # 尾缀形态零回退
        ("我在长春市", "长春市"),
    ])
    def test_adoption(self, msg, expect):
        assert _ext(msg).get("city") == expect, msg

    def test_bazi_extractor_same(self):
        """单发排盘路径同判据（`_extract_bazi_info`）：出生小句形态 + 四要素形态。"""
        assert _info("我1999年5月13日10点55分在长春生的 男")[5] == "长春"
        assert _info("1999年5月13日 10:55 长春 男")[5] == "长春"
        assert _info("1999年5月13日 10:55 三亚 男")[5] == "三亚"


# ════════════════════════════════════════════════════════════════
# ③ 采纳门 b：行程形态（相对未来锚 / 全未来日期）
# ════════════════════════════════════════════════════════════════
class TestTripNotBirthCity:
    @pytest.mark.parametrize("msg", [
        "我下个月去三亚", "我想去中山", "他来自临沂", "我老家在保定",
        "我打算去丽江旅游", "下周去佛山出差", "我来自吉林长春",
        "我在中山路", "我妈来自临沂", "我明年去东莞",
    ])
    def test_reverse_blocked(self, msg):
        """审查实测反例组：只做①会立刻引入 → 必须被门挡住。"""
        assert _ext(msg).get("city") is None, msg

    @pytest.mark.parametrize("msg", [
        "下个月3月8日 10点 三亚 男",        # 四要素齐但是未来行程（brief 点名联测）
        "下个月3月8日，10点 三亚 男",
        "明年3月8日 10点 三亚 男",
        "2027年3月8日 10点 三亚 男",        # 绝对未来 → 既有 birth_dates_all_future 挡
    ])
    def test_future_trip_blocked(self, msg):
        """四要素门 × `birth_dates_all_future` 联测：行程/给他人排盘不得当出生地。"""
        assert _ext(msg).get("city") is None, msg

    def test_relative_anchor_scoped_to_clause(self):
        """相对未来锚**只在小句内**生效：`…，帮我看看明年的流年运势` 不挡出生地。"""
        msg = "1990年5月20日 15:30 北京 男，帮我看看明年的流年运势"
        assert _ext(msg).get("city") == "北京", msg

    def test_predicate_direct(self):
        """结构直证：四要素齐 + 非未来 → 采纳；行程锚 → 不采纳。"""
        h = object.__new__(MessageHandler)
        pos = 0
        assert h._extract_partial_birth("1999年5月13日 10:55 三亚 男").get("city") == "三亚"
        assert MessageAnalyzer.birth_dates_all_future("2027年3月8日 10点 三亚 男") is True
        assert MessageAnalyzer.birth_dates_all_future("下个月3月8日 10点 三亚 男") is False
        del pos


# ════════════════════════════════════════════════════════════════
# ④ 采纳门 c/d：四要素齐 / 会话式应答
# ════════════════════════════════════════════════════════════════
class TestFourElementAndBareReply:
    @pytest.mark.parametrize("msg,expect", [
        ("1999年5月13日 10:55 长春 男", "长春"),      # 四要素（k11c golden 形态）
        ("1999年5月13日10点55分 长春 男", "长春"),
        ("我是1990年5月20日10点 广州市 男", "广州市"),  # 尾缀零回退
        ("1990-05-20 15:00 深圳 女", "深圳"),          # 既有四要素形态
        ("长春", "长春"),                              # F2 会话式应答（只答城市）
        ("，长春", "长春"),
        ("5月13日，长春", "长春"),                      # 部分信息 + 城市
        ("1999年3月28日 早上十点 长春", "长春"),        # 日期+时间+城市（缺性别）
        ("1999年5月13日 10:55 长春", "长春"),
    ])
    def test_adoption(self, msg, expect):
        assert _ext(msg).get("city") == expect, msg

    @pytest.mark.parametrize("msg", ["今天北京晴。", "上海天气", "北京东路怎么走",
                                     "北京烤鸭真好吃", "北京的房价", "上海和北京天气"])
    def test_non_birth_mentions_not_taken(self, msg):
        """地名裸提（天气/地址/品牌）本就不是出生地 → 门必须挡住（改前误取）。"""
        assert _ext(msg).get("city") is None, msg


# ════════════════════════════════════════════════════════════════
# ⑤ P0 本体：时柱正确性（经度 → 真太阳时 → 时柱）
# ════════════════════════════════════════════════════════════════
class TestPillarCorrectness:
    def test_hour_pillar_differs_by_city(self):
        e = BaziEngine()
        cs = e.calculate(1999, 5, 13, 10, 55, "长春", "男")
        bj = e.calculate(1999, 5, 13, 10, 55, "北京", "男")
        assert cs.bazi[3] == "壬午" and bj.bazi[3] == "辛巳", (cs.bazi, bj.bazi)
        assert cs.bazi[:3] == bj.bazi[:3] == ["己卯", "己巳", "乙丑"]

    def test_e2e_archive_gets_changchun_not_beijing(self, tmp_path):
        """E2E：`我1999年5月13日10点55分在长春生的 男` → 建档 长春（**不是**缺省北京）。"""
        h, db = _h(tmp_path, seed=None)
        h._handle_bazi("我1999年5月13日10点55分在长春生的 男", "u1")
        snap = _snap(db)
        assert snap is not None and snap[3] == "长春", snap

    def test_e2e_three_stores_consistent(self, tmp_path):
        """三源同值：persons / users.bazi_info / chart_records 都是 长春。"""
        from src.storage.dao import UserDAO
        h, db = _h(tmp_path, seed=None, user="u2")
        h._handle_bazi("我1999年5月13日10点55分在长春生的 男", "u2")
        from src.storage.person_dao import PersonDAO
        p = PersonDAO(db).get_default_person("u2")
        bi = UserDAO(db).get_user_bazi("u2") or {}
        ch = (h.chart_dao.get_latest_chart("u2") or {}).get("birth", {})
        assert p["city"] == bi.get("city") == ch.get("city") == "长春", (
            p["city"], bi.get("city"), ch.get("city"))

    def test_tool_path_same(self, tmp_path):
        """工具路径同判据（brief：`_tool_bazi("1999年5月13日 10:55 长春 男")`）。"""
        h, db = _h(tmp_path, seed=None, user="u3")
        h._tool_bazi("1999年5月13日 10:55 长春 男", "u3")
        assert _snap(db, "u3")[3] == "长春"

    def test_archive_city_not_overwritten_by_residence(self, tmp_path):
        """既有口径不回退：现居句的城市不改写档案。"""
        h, db = _h(tmp_path)
        h._handle_bazi("我出生在长春，我现在住广州", "u1")
        assert _snap(db)[3] == "长春", _snap(db)


# ════════════════════════════════════════════════════════════════
# ⑥ 表单/哨兵路径不受影响
# ════════════════════════════════════════════════════════════════
class TestFormPathUnaffected:
    def test_form_sentinel_path_untouched(self, tmp_path):
        """表单哨兵（`FORM_EXPLICIT_CTX`）不受裸城市门影响：仍走显式写入。"""
        from src.storage.person_dao import FORM_EXPLICIT_CTX, is_correction_text
        assert is_correction_text(FORM_EXPLICIT_CTX) is True
        h, db = _h(tmp_path, seed=None)
        h._handle_bazi(FORM_EXPLICIT_CTX + " 1999年5月13日 10:55 长春 男", "u1")
        snap = _snap(db)
        assert snap is None or snap[3] == "长春", snap

    def test_city_gate_is_not_applied_to_suffix_form(self):
        """尾缀形态（`XX市`）不受裸名门限制（既有口径）。"""
        for msg, want in [("我1991年7月8日在酒泉市人民医院出生", "酒泉市"),
                          ("三月初三，吉林省长春市榆树市出生，男", "榆树市"),
                          ("我来自吉林，长春市", "长春市")]:
            assert _ext(msg).get("city") == want, msg
