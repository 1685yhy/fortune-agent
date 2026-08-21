"""万年历 API（P1-1，问真吉真万年历同款）：月视图完整性 + 节气锚点 + 单日详情锚点
+ 宜忌规则确定性 + 鉴权 401 + 参数校验 400。

锚点（lunar-python 历法，与问真万年历同源口径）:
- 2026-08-07 = 立秋（节气当日，农历六月廿五，癸丑日）
- 2026-08-19 = 农历七月初七（七夕节），乙丑日，海中金，星期三，
  黄道 · 值神明堂（吉），冲(己未)羊 · 煞东
- 2026-08-23 = 处暑
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

BJT = timezone(timedelta(hours=8))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402


def _client():
    set_auth_handler(AuthHandler())
    return TestClient(app)


def _token(uid="u_wannianli_1"):
    return JWTHandler("test-secret-key-32-bytes-long!!").create_token(uid)


def _headers():
    return {"Authorization": f"Bearer {_token()}"}


# ---------------------------------------------------------------- 月视图完整性
def test_month_view_complete():
    """2026-08 月视图：31 天、首日偏移、每日字段齐全非空。"""
    r = _client().get("/api/wannianli?year=2026&month=8", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["year"] == 2026 and body["month"] == 8
    assert body["days_in_month"] == 31
    assert body["first_weekday"] == 5              # 2026-08-01 是周六（0=周日）
    # P1-1 审查 I1: today 断言用相对时间（服务端"今日"随缓存 24h 过期，测试不可硬编码）
    assert body["today"] == datetime.now(BJT).strftime("%Y-%m-%d")

    days = body["days"]
    assert len(days) == 31
    assert days[0]["date"] == "2026-08-01"
    assert days[30]["date"] == "2026-08-31"

    for d in days:
        assert d["date"].startswith("2026-08-")
        assert d["cell_lunar"], d          # 农历小字（节气日显示节气名）
        assert d["lunar_day"], d
        assert len(d["day_ganzhi"]) == 2   # 干支日（如 癸丑/乙丑）
        assert d["huanghedao"] in ("黄道", "黑道")
        assert d["tianshen"] in (
            "青龙", "明堂", "金匮", "天德", "玉堂", "司命",
            "天刑", "朱雀", "白虎", "天牢", "玄武", "勾陈",
        )
        assert d["jianchu"] in ("建", "除", "满", "平", "定", "执", "破", "危",
                                "成", "收", "开", "闭")
        assert d["quality"] in ("吉", "平", "凶")
        assert isinstance(d["yi_short"], list) and d["yi_short"]
        assert isinstance(d["ji_short"], list) and d["ji_short"]
        assert "jieqi" in d and "festival" in d and "is_today" in d

    # 全月有黄道也有黑道日（2026-01-01 为黑道·朱雀，2026-08-19 为黄道·明堂）
    types = {d["huanghedao"] for d in days}
    assert types == {"黄道", "黑道"}


# ---------------------------------------------------------------- 节气锚点
def test_month_jieqi_anchors():
    """2026-08-07 立秋 / 2026-08-23 处暑，节气日 cell_lunar 显示节气名。"""
    r = _client().get("/api/wannianli?year=2026&month=8", headers=_headers())
    body = r.json()
    by_date = {d["date"]: d for d in body["days"]}

    d7 = by_date["2026-08-07"]
    assert d7["jieqi"] == "立秋"
    assert d7["cell_lunar"] == "立秋"          # 节气日朱砂标注（取代农历日）
    assert d7["lunar_day"] == "廿五"           # 农历小字仍保留完整日
    assert d7["day_ganzhi"] == "癸丑"

    d23 = by_date["2026-08-23"]
    assert d23["jieqi"] == "处暑"
    assert d23["cell_lunar"] == "处暑"

    # 非节气日 jieqi 为空串
    assert by_date["2026-08-19"]["jieqi"] == ""
    assert by_date["2026-08-19"]["cell_lunar"] == "初七"


def test_day_detail_jieqi():
    """单日详情节气：2026-08-07 立秋。"""
    r = _client().get("/api/wannianli/day?date=2026-08-07", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["jieqi"] == "立秋"


# ---------------------------------------------------------------- 单日详情锚点
def test_day_detail_anchors():
    """2026-08-19（七月初七·七夕）：干支/纳音/黄黑道/冲煞全字段锚点。"""
    r = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["date"] == "2026-08-19"
    assert body["weekday"] == "三"
    assert body["jieqi"] == ""
    assert "七夕节" in body["festivals"]

    # 农历
    assert body["lunar"]["month"] == "七月"
    assert body["lunar"]["day"] == "初七"
    assert body["lunar"]["leap"] is False
    assert "七月初七" in body["lunar"]["full"]

    # 干支 + 纳音
    assert body["ganzhi"] == {"year": "丙午", "month": "丙申", "day": "乙丑"}
    assert body["nayin"]["day"] == "海中金"
    assert body["nayin"]["year"] == "天河水"

    # 黄黑道十二值神
    assert body["huanghedao"]["type"] == "黄道"
    assert body["huanghedao"]["tianshen"] == "明堂"
    assert body["huanghedao"]["luck"] == "吉"

    # 建除 + 二十八宿
    assert body["jianchu"]["name"] in (
        "建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭")
    assert body["jianchu"]["quality"] in ("吉", "平", "凶")
    assert body["jianchu"]["desc"]
    assert body["ershibaxiu"]["name"] and body["ershibaxiu"]["jixiong"] in ("吉", "凶", "平")

    # 宜忌 + 吉神凶煞
    assert body["yi"] and body["ji"]
    assert body["jishen"] and "明堂" in body["jishen"]   # 吉神宜趋含值神
    assert body["xiongsha"] is not None

    # 冲煞: 乙丑日冲(己未)羊 · 煞东
    assert body["chong"]["zodiac"] == "羊"
    assert body["chong"]["ganzhi"] == "己未"
    assert body["chong"]["sha"] == "东"

    # 旬空 + 方位
    assert body["xunkong"] == ["戌", "亥"]
    assert body["positions"]["cai"] == "东北"
    assert body["positions"]["xi"] == "西北"
    assert body["positions"]["fu"] == "西南"


# ---------------------------------------------------------------- 二十八宿锚点（P1-1 审查 C1）
def test_day_detail_ershibaxiu_anchors():
    """二十八宿值日（lunar-python getXiu 口径，三源验证）：
    2026-08-19 → 轸（旧锚点错误实现出"星"，差 3 天）；2000-01-01 → 胃（基准锚点）。"""
    for date, name, jixiong in [
        ("2026-08-19", "轸", "吉"),
        ("2000-01-01", "胃", "吉"),
        ("2026-08-07", "娄", "吉"),
    ]:
        r = _client().get(f"/api/wannianli/day?date={date}", headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ershibaxiu"]["name"] == name, \
            f"{date} 二十八宿应为{name}, 实际{body['ershibaxiu']['name']}"
        assert body["ershibaxiu"]["jixiong"] == jixiong


# ---------------------------------------------------------------- 节气日建除锚点（P1-1 审查 I2）
def test_jieqi_day_jianchu_anchor():
    """节气日建除口径（交节日即新月令，对齐主流通书）：
    2026-08-07 立秋 = 十二节 → 月支顺推一位（未月→申月）→ 执日
    （旧口径未月起建为破日）；2026-08-23 处暑 = 十二气 → 不换月令（收日）。"""
    # 月视图
    mv = _client().get("/api/wannianli?year=2026&month=8", headers=_headers()).json()
    by_date = {d["date"]: d for d in mv["days"]}
    assert by_date["2026-08-07"]["jieqi"] == "立秋"
    assert by_date["2026-08-07"]["jianchu"] == "执"
    assert by_date["2026-08-23"]["jieqi"] == "处暑"
    assert by_date["2026-08-23"]["jianchu"] == "收"

    # 日详情（与月视图同一规则源）
    d7 = _client().get("/api/wannianli/day?date=2026-08-07", headers=_headers()).json()
    assert d7["jieqi"] == "立秋"
    assert d7["jianchu"]["name"] == "执"
    assert d7["jianchu"]["quality"] == "平"

    # 非节气日不位移：2026-08-19（申月起建）执日不变
    d19 = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers()).json()
    assert d19["jianchu"]["name"] == "执"


# ---------------------------------------------------------------- 宜忌规则确定性
def test_yi_ji_rule_consistency():
    """宜忌规则确定性：建除宜忌（zeri 同源表）+ 当日黄历宜忌合并，同日期多次一致。"""
    r1 = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers()).json()
    r2 = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers()).json()
    r3 = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers()).json()
    assert r1 == r2 == r3                          # 确定性：无随机/LLM 成分

    # 建除宜忌（"执"日）在前 + 当日黄历宜忌合并、去重、保序
    # 2026-08-19 建除=执: 宜 [捕猎,断壁,建房,...] 忌 [出行,嫁娶,开市,入宅]
    assert r1["yi"][0] == "捕猎"
    assert r1["yi"][1] == "断壁"
    assert len(r1["yi"]) == len(set(r1["yi"]))     # 无重复项
    assert len(r1["ji"]) == len(set(r1["ji"]))
    assert "出行" in r1["ji"] and "嫁娶" in r1["ji"]

    # 月视图同日期字段与日详情一致（同一规则源）
    mv = _client().get("/api/wannianli?year=2026&month=8", headers=_headers()).json()
    d19 = next(d for d in mv["days"] if d["date"] == "2026-08-19")
    assert d19["day_ganzhi"] == r1["ganzhi"]["day"]
    assert d19["huanghedao"] == r1["huanghedao"]["type"]
    assert d19["jianchu"] == r1["jianchu"]["name"]
    assert d19["quality"] == r1["jianchu"]["quality"]
    assert d19["yi_short"] == r1["yi"][:3]
    assert d19["ji_short"] == r1["ji"][:3]


def test_month_view_deterministic():
    """月视图确定性：同月两次调用结果完全一致。"""
    c = _client()
    a = c.get("/api/wannianli?year=2026&month=8", headers=_headers()).json()
    b = c.get("/api/wannianli?year=2026&month=8", headers=_headers()).json()
    assert a == b


# ---------------------------------------------------------------- 鉴权 + 校验
def test_requires_auth():
    """无 token 一律 401（安全红线）。"""
    c = _client()
    assert c.get("/api/wannianli?year=2026&month=8").status_code == 401
    assert c.get("/api/wannianli/day?date=2026-08-19").status_code == 401


def test_validation_400():
    """非法参数 400：越界年份/月份/日期、坏格式。"""
    c = _client()
    assert c.get("/api/wannianli?year=1899&month=8", headers=_headers()).status_code == 400
    assert c.get("/api/wannianli?year=2026&month=13", headers=_headers()).status_code == 400
    assert c.get("/api/wannianli?year=2026&month=0", headers=_headers()).status_code == 400
    assert c.get("/api/wannianli?year=2026&month=2", headers=_headers()).status_code == 200
    assert c.get("/api/wannianli/day?date=2026-02-30", headers=_headers()).status_code == 400
    assert c.get("/api/wannianli/day?date=2026-13-01", headers=_headers()).status_code == 400
    assert c.get("/api/wannianli/day?date=20260819", headers=_headers()).status_code == 400
    # 缺 date 参数 → FastAPI 校验 422
    assert c.get("/api/wannianli/day", headers=_headers()).status_code == 422
    # 边界：闰年 2024-02-29 合法；2100-12-31 上界合法
    assert c.get("/api/wannianli/day?date=2024-02-29", headers=_headers()).status_code == 200
    assert c.get("/api/wannianli/day?date=2100-12-31", headers=_headers()).status_code == 200
    assert c.get("/api/wannianli/day?date=2101-01-01", headers=_headers()).status_code == 400


# ---------------------------------------------------------------- 闰月覆盖
def test_leap_month():
    """闰月：2025-07-25 = 闰六月初一，lunar.leap=True 且月名带"闰"。"""
    r = _client().get("/api/wannianli/day?date=2025-07-25", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lunar"]["leap"] is True
    assert body["lunar"]["month"] == "闰六月"
    assert body["lunar"]["day"] == "初一"
