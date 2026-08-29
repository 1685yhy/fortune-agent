"""名人命盘测试（batch5 B5-3，M：命盘卡 + 大运×大事对照）。

- 出生数据：src/data/mingren_birth.json（≥20 位出生日期可靠的名人，公历，随 src/ 部署）
- 详情接口：有出生数据 + 引擎注入 → chart（四柱/十神/藏干/纳音/meta/时辰标注）+
  timeline（事件年份→大运归属分组：起运前=未起运）；无出生数据 → has_chart=false 无 chart
- 门控：chart 随详情接口既有门控（未解锁 403 在前，不泄露 chart）
- 纯计算不落库：断言全程只读（不调 POST /api/paipan）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.api import mingren as mingren_api  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402

FREE_LIMIT = 35
LIBRARY_TOTAL = 2807


class _FakeMemberDAO:
    """测试桩：get_membership 返回指定档位。"""

    def __init__(self, plan: str = "free"):
        self.plan = plan

    def get_membership(self, uid: str) -> dict:
        return {"user_id": uid, "plan": self.plan, "plan_label": "会员"}


@pytest.fixture(scope="module")
def client():
    from src.security.auth import AuthHandler, JWTHandler, set_auth_handler
    from src.main import app
    set_auth_handler(AuthHandler())
    mingren_api.setup(member_dao=_FakeMemberDAO("free"),
                      data_path=str(mingren_api._DATA_FILE), engine=BaziEngine())
    return TestClient(app), JWTHandler("test-secret-key-32-bytes-long!!")


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    """默认关闭体验模式（项目 .env 为 true，门控用例须显式关掉）——同 test_mingren 口径。"""
    monkeypatch.setattr(mingren_api, "is_experience_mode", lambda: False)


def _get(client, token, path, params=None):
    headers = {"Authorization": "Bearer %s" % token} if token else {}
    return client.get(path, params=params or {}, headers=headers)


def _setup_engine(plan: str = "free"):
    """状态复位：member_dao + 真实命例库路径 + 引擎。

    必须显式传 data_path —— test_mingren.py 的 503 用例
    (setup(data_path=tmp/nope.json)) 不还原全局 _data_path，
    本文件按字母序跑在其后，不复位会 503（全量回归全绿的关键）。"""
    mingren_api.setup(
        member_dao=_FakeMemberDAO(plan),
        data_path=str(mingren_api._DATA_FILE),
        engine=BaziEngine(),
    )


# ── 出生数据完整性（B5-3 重头：宁缺毋滥） ─────────────────────────

def test_birth_data_integrity():
    """≥20 位入选；姓名全部存在于 2807 命例库；公历日期可解析且为真实日期；
    双源交叉验证（产品要求）：每个名字 sources ≥2 个独立来源。"""
    _setup_engine()  # 复位命例库路径（防 test_mingren 503 用例残留污染）
    birth = mingren_api._load_birth()
    assert len(birth) >= 20
    data = mingren_api._load_data()
    for name, entry in birth.items():
        assert name in data, "出生数据人名不在命例库: %s" % name
        assert entry.get("birth"), name
        # 双源交叉验证：≥2 个独立来源（史书/百科/命理文献任意组合）
        assert isinstance(entry.get("sources"), list) and len(entry["sources"]) >= 2, \
            "来源不足 2 个: %s" % name
        assert entry.get("gender") in ("男", "女"), name
        ymd = mingren_api._parse_birth_text(entry["birth"])
        assert ymd, "出生日期无法解析: %s = %r" % (name, entry["birth"])


def test_source_charts_match_engine():
    """源四柱反证（产品硬性要求）：每个带 source_chart 的名人，用 BaziEngine
    按出生日期+时辰重算，四柱与源四柱逐字比对一致（源数据自带四柱 = 第一源，
    引擎重算 = 第二源；不一致者一律不入 source_chart）。"""
    _setup_engine()
    birth = mingren_api._load_birth()
    engine = mingren_api._bazi_engine
    with_src = [(n, e) for n, e in birth.items() if e.get("source_chart")]
    assert len(with_src) >= 5, "源四柱反证名额过少（产品要求批量取源数据反证）"
    for name, entry in with_src:
        assert entry.get("shichen"), "source_chart 必须带 shichen: %s" % name
        sc = mingren_api._parse_shichen(entry["shichen"])
        assert sc, "时辰无法解析: %s = %r" % (name, entry.get("shichen"))
        ymd = mingren_api._parse_birth_text(entry["birth"])
        assert ymd
        r = engine.calculate(ymd[0], ymd[1], ymd[2], sc[0], sc[1], "北京",
                             mingren_api.normalize_gender(entry.get("gender")))
        got = " ".join(r.bazi)
        assert got == entry["source_chart"], \
            "四柱反证不一致（剔除/复核）: %s 引擎=%s 源=%s" % (
                name, got, entry["source_chart"])


def test_birth_data_parse_rules():
    """解析：精确「YYYY年M月D日」（支持 3-4 位年份如 624 年）；非法输入 → None。"""
    assert mingren_api._parse_birth_text("1893年12月26日") == (1893, 12, 26)
    assert mingren_api._parse_birth_text("624年2月17日") == (624, 2, 17)
    assert mingren_api._parse_birth_text("1328年10月21日") == (1328, 10, 21)
    assert mingren_api._parse_birth_text("1328年2月30日") is None   # 伪日期
    assert mingren_api._parse_birth_text("1328年13月1日") is None   # 越界月
    assert mingren_api._parse_birth_text("农历1328年九月十八") is None  # 只认公历数字
    assert mingren_api._parse_birth_text("") is None


def test_birth_data_all_entries_calculate():
    """33 位全部可排盘：四柱齐、大运齐（引擎覆盖古历年份 624-1906）。"""
    _setup_engine()
    birth = mingren_api._load_birth()
    for name, entry in birth.items():
        ymd = mingren_api._parse_birth_text(entry["birth"])
        chart = mingren_api._chart_of(name)
        assert chart, "排盘失败: %s" % name
        assert len(chart["bazi"]) == 4
        assert len(chart["pillars"]) == 4
        assert chart["dayun"], name
        assert chart["qiyun"]["start_sui"] > 0
        assert ymd is not None


# ── 详情接口：有出生数据 → chart + timeline ───────────────────────

def test_detail_zhuyuanzhang_chart(client):
    """朱元璋（免费可见第 3 位）：chart 四柱/十神/藏干/纳音/meta 断言 + 时辰默认午时标注。"""
    _setup_engine()
    app, jwt = client
    r = _get(app, jwt.create_token("u_free"), "/api/mingren/朱元璋")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["has_chart"] is True
    chart = d["chart"]
    # 四柱（天历元年九月十八 → 1328-10-21，午时）：戊辰 壬戌 丁丑 丙午
    assert chart["bazi"] == ["戊辰", "壬戌", "丁丑", "丙午"]
    assert chart["day_master"] == "丁火"
    p0 = chart["pillars"][0]
    assert p0["name"] == "年柱" and p0["ganzhi"] == "戊辰"
    assert p0["is_day"] is False
    assert chart["pillars"][2]["is_day"] is True
    # 藏干（辰藏戊乙癸，各带十神）
    assert [c["gan"] for c in p0["canggan"]] == ["戊", "乙", "癸"]
    assert all(c["shishen"] for c in p0["canggan"])
    # 纳音
    assert chart["nayin"] == ["大林木", "大海水", "涧下水", "天河水"]
    # meta：公历/生肖（戊辰→龙）/农历干支
    assert chart["meta"]["solar_text"] == "1328年10月21日"
    assert chart["meta"]["zodiac"] == "龙"
    assert chart["meta"]["lunar"]["year_ganzhi"] == "戊辰"
    assert chart["meta"]["shichen"] == "午时"
    # 时辰缺失 → 默认午时标注
    assert chart["hour_note"] == "时辰无考，以午时推演"
    # 起运：出生后2年11月…起运，4 岁起运（1331 年）
    assert chart["qiyun"]["start_sui"] == 4
    assert chart["qiyun"]["start_year"] == 1331
    assert "起运" in chart["qiyun"]["desc"]
    # 大运：癸亥（1331-1340）十神七杀（丁日主见癸）
    dy0 = chart["dayun"][0]
    assert dy0 == {"sui": 4, "end_sui": 13, "ganzhi": "癸亥",
                   "shishen": "七杀", "start_year": 1331, "end_year": 1340}
    # 溯源
    assert d["birth_text"] == "1328年10月21日"
    assert d["birth_source"]


def test_detail_zhuyuanzhang_timeline(client):
    """朱元璋 timeline：事件年份→大运归属（起运 1331 前=未起运；1351 北伐=乙丑；
    1398 崩=己巳）。"""
    _setup_engine()
    app, jwt = client
    d = _get(app, jwt.create_token("u_free"), "/api/mingren/朱元璋").json()
    tl = d["timeline"]
    assert isinstance(tl, list) and tl
    # 事件总条数 = flist 中四位数年份条目数
    assert sum(len(g["events"]) for g in tl) == len(d["flist"])
    # 未起运组：1328（出生）在起运年 1331 之前
    before = next(g for g in tl if g["status"] == "before")
    assert before["label"] == "未起运"
    assert before["dayun"] is None
    assert [e["year"] for e in before["events"]] == [1328]
    # 乙丑（1351-1360）：1351 参加红巾军、1353 募兵
    yichou = next(g for g in tl if g["status"] == "dayun"
                  and g["dayun"]["ganzhi"] == "乙丑")
    assert yichou["dayun"]["start_year"] == 1351
    assert yichou["dayun"]["end_year"] == 1360
    years = [e["year"] for e in yichou["events"]]
    assert 1351 in years and 1353 in years
    # 己巳（1391-1400）：1398 崩
    jisi = next(g for g in tl if g["status"] == "dayun"
                and g["dayun"]["ganzhi"] == "己巳")
    assert [e["year"] for e in jisi["events"]] == [1398]
    # 甲子（1341-1350）：1343 旱灾（事件文本随行）
    jiazi = next(g for g in tl if g["status"] == "dayun"
                 and g["dayun"]["ganzhi"] == "甲子")
    ev = jiazi["events"][0]
    assert ev["year"] == 1343 and ev["event"]


def test_detail_wuzetian_female(client):
    """武则天（女，624 年）：gender=女，四柱 甲申 丙寅 甲午 庚午。"""
    _setup_engine()
    app, jwt = client
    d = _get(app, jwt.create_token("u_free"), "/api/mingren/武则天").json()
    assert d["has_chart"] is True
    chart = d["chart"]
    assert chart["gender"] == "女"
    assert chart["bazi"] == ["甲申", "丙寅", "甲午", "庚午"]
    assert chart["meta"]["zodiac"] == "猴"


def test_detail_kangxi_qiyun(client):
    """康熙：起运 1 岁（1654 年起运），首运 己巳（1654-1663）——史载生于
    顺治十一年三月十八（1654-05-04），起运极早的抽样断言。"""
    _setup_engine()
    app, jwt = client
    d = _get(app, jwt.create_token("u_free"), "/api/mingren/康熙").json()
    q = d["chart"]["qiyun"]
    assert q["start_sui"] == 1 and q["start_year"] == 1654
    dy0 = d["chart"]["dayun"][0]
    assert dy0["ganzhi"] == "己巳"
    assert dy0["start_year"] == 1654 and dy0["end_year"] == 1663


def test_detail_qianlong_chart(client):
    """乾隆：清宫档案八字 辛卯 丁酉 庚午 丙子（子时）逐字呈现——
    时辰有史载 → 无 hour_note，meta 带乾造/子时；首运 丙申（1717-1726）。"""
    _setup_engine()
    app, jwt = client
    d = _get(app, jwt.create_token("u_free"), "/api/mingren/乾隆").json()
    chart = d["chart"]
    assert chart["bazi"] == ["辛卯", "丁酉", "庚午", "丙子"]
    assert "hour_note" not in chart          # 时辰有考，不推演
    assert chart["meta"]["shichen"] == "子时"
    assert chart["meta"]["gankun"] == "乾造"
    dy0 = chart["dayun"][0]
    assert dy0["ganzhi"] == "丙申" and dy0["shishen"] == "七杀"
    assert dy0["start_year"] == 1717 and dy0["end_year"] == 1726
    assert chart["qiyun"]["start_sui"] == 7 and chart["qiyun"]["start_year"] == 1717


def test_detail_zengguofan_chart(client):
    """曾国藩：《穷通宝鉴》徐乐吾评注命例 辛未 己亥 丙辰 己亥（亥时）逐字一致；
    首运 戊戌（1817-1826）。曾国藩在库中排免费 35 之外 → pro 会员访问。"""
    _setup_engine("pro")
    app, jwt = client
    d = _get(app, jwt.create_token("u_pro"), "/api/mingren/曾国藩").json()
    chart = d["chart"]
    assert chart["bazi"] == ["辛未", "己亥", "丙辰", "己亥"]
    assert chart["meta"]["shichen"] == "亥时"
    assert chart["dayun"][0]["ganzhi"] == "戊戌"
    assert chart["qiyun"]["start_sui"] == 8


def test_detail_zuozongtang_kongwang(client):
    """左宗棠：壬申 辛亥 丙午 庚寅（寅时）；空亡 寅卯（丙午日→甲辰旬），
    时支寅空 → 时柱 kong='寅'（其余柱为空串）。左宗棠也在免费 35 之外 → pro。"""
    _setup_engine("pro")
    app, jwt = client
    d = _get(app, jwt.create_token("u_pro"), "/api/mingren/左宗棠").json()
    chart = d["chart"]
    assert chart["bazi"] == ["壬申", "辛亥", "丙午", "庚寅"]
    assert [p["kong"] for p in chart["pillars"]] == ["", "", "", "寅"]
    assert chart["pillars"][3]["shensha"]  # 神煞归柱非空
    assert chart["meta"]["gankun"] == "乾造"


# ── 详情接口：无出生数据 / 门控 / 引擎缺失 ───────────────────────

def test_detail_no_birth_no_chart(client):
    """孔子（无出生数据）：只生平，无 chart/timeline 字段，has_chart=false。"""
    _setup_engine()
    app, jwt = client
    d = _get(app, jwt.create_token("u_free"), "/api/mingren/孔子").json()
    assert d["has_chart"] is False
    assert "chart" not in d and "timeline" not in d
    assert d["info"] and d["flist"]


def test_detail_gate_403_no_chart(client):
    """未解锁（免费用户第 36 位赵文华）：403 VIP_REQUIRED，无 chart 泄露。"""
    _setup_engine()
    app, jwt = client
    r = _get(app, jwt.create_token("u_free"), "/api/mingren/赵文华")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "VIP_REQUIRED"
    assert "chart" not in r.text


def test_detail_pro_beyond35_chart(client):
    """高级会员第 36 位（无出生数据）→ 200 但无 chart；出生数据门控跟随详情接口。"""
    _setup_engine("pro")
    app, jwt = client
    d = _get(app, jwt.create_token("u_pro"), "/api/mingren/赵文华").json()
    assert d["has_chart"] is False


def test_detail_engine_missing_no_chart(client):
    """引擎未注入 → 有出生数据也不返回 chart（不 503，主生平不受影响）。"""
    mingren_api.setup(member_dao=_FakeMemberDAO("free"),
                      data_path=str(mingren_api._DATA_FILE), engine=None)
    app, jwt = client
    d = _get(app, jwt.create_token("u_free"), "/api/mingren/朱元璋").json()
    assert d["has_chart"] is False
    assert "chart" not in d
    assert d["info"]


# ── 列表：has_chart 徽标透出 ─────────────────────────────────────

def test_list_has_chart_badge(client):
    """列表条目 has_chart：有出生数据者 true（忽必烈），无者 false（孔子）。"""
    _setup_engine()
    app, jwt = client
    body = _get(app, jwt.create_token("u_free"), "/api/mingren",
                {"page": 1, "size": 35}).json()
    by_name = {i["name"]: i for i in body["items"]}
    assert by_name["忽必烈"]["has_chart"] is True
    assert by_name["孔子"]["has_chart"] is False
    assert len(by_name) == FREE_LIMIT  # 列表结构/门控不回归


# ── 纯计算不落库：图数据确定性（重复请求同结果，无写库副作用） ────

def test_chart_deterministic(client):
    """两次请求同图：纯计算无状态（不落库不缓存外部副作用）。"""
    _setup_engine()
    app, jwt = client
    d1 = _get(app, jwt.create_token("u_free"), "/api/mingren/乾隆").json()
    d2 = _get(app, jwt.create_token("u_free"), "/api/mingren/乾隆").json()
    assert d1["chart"]["bazi"] == d2["chart"]["bazi"]
    assert d1["timeline"] == d2["timeline"]
