"""排盘结果 API（L4）：POST /api/paipan — 全字段锚点 + 鉴权 + 校验。

锚点盘：闫海洋命例 1999-05-13 11:25 北京 男（与 test_bazi_jiaoyun 问真排盘页
锚点同盘：四柱 己卯 己巳 乙丑 壬午 / 起运 3 岁 / 交运逢辛、丙年 / 司令庚）。
注意：传城市「北京」走真太阳时修正，起运分解与 city="" 的问真原始锚点有
分钟级差异（起运岁数/交运/司令等整值锚点不受影响）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.api import paipan as paipan_api  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402

# 闫海洋命例（问真排盘页锚点盘）
YAN = {
    "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
    "birthHour": 6,          # 时辰序号 6 = 午时（11 点）
    "minute": 25,            # 与问真锚点 11:25 对齐（午时 → 时柱壬午）
    "gender": "male", "city": "北京",
}


def _client():
    paipan_api.setup(BaziEngine())
    set_auth_handler(AuthHandler())
    return TestClient(app)


def _token(uid="u_paipan_1"):
    return JWTHandler("test-secret-key-32-bytes-long!!").create_token(uid)


def _headers():
    return {"Authorization": f"Bearer {_token()}"}


# ---------------------------------------------------------------- 主流程全字段锚点
def test_paipan_full_chart_anchors():
    """闫海洋盘：基础/L1/L2 全字段锚点。"""
    r = _client().post("/api/paipan", json=YAN, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    # ── 基础 ──
    assert body["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert body["day_master"] == "乙木"
    assert body["shishen"] == ["偏财", "偏财", "日主", "正印"]
    assert body["nayin"] == ["城头土", "大林木", "海中金", "杨柳木"]
    assert body["gender"] == "男"

    # pillars 派生：藏干/纳音/长生/日主高亮
    ps = body["pillars"]
    assert [p["name"] for p in ps] == ["年柱", "月柱", "日柱", "时柱"]
    assert [p["ganzhi"] for p in ps] == ["己卯", "己巳", "乙丑", "壬午"]
    assert [p["canggan"][0]["gan"] for p in ps] == ["乙", "丙", "己", "丁"]
    assert [p["canggan"][0]["shishen"] for p in ps] == ["比肩", "伤官", "偏财", "食神"]
    assert ps[2]["is_day"] is True and ps[0]["is_day"] is False
    assert [p["xingyun"] for p in ps] == ["临官", "沐浴", "衰", "长生"]
    assert [p["nayin"] for p in ps] == ["城头土", "大林木", "海中金", "杨柳木"]

    # ── L1：起运 / 交运 / 司令 ──
    assert body["qiyun_desc"] == "出生后2年4月21天2时起运"  # 北京真太阳时口径
    assert body["dayun"][0]["sui"] == 3                     # 起运 3 岁
    jy = body["jiaoyun"]
    assert "交大运" in jy["page_text"] and "白露后" in jy["page_text"]
    assert jy["gan_pair"] in ("辛、丙", "丙、辛")
    assert body["siling"] == "庚"                            # 巳月庚金用事
    assert body["siling_detail"]["days"] == 9
    assert body["siling_detail"]["jie"] == "立夏"

    # ── L1：大运（带十神+年份） ──
    dy = body["dayun"]
    assert len(dy) == 12
    assert dy[0]["ganzhi"] == "戊辰" and dy[0]["sui"] == 3 and dy[0]["end_sui"] == 12
    assert dy[0]["shishen"] == "正财"                        # 戊 vs 乙日主
    assert dy[0]["start_year"] == 2001 and dy[0]["end_year"] == 2010
    assert dy[2]["ganzhi"] == "丙寅" and dy[2]["shishen"] == "伤官"

    # ── L1：流年表 / 流年关系 / 干支关系 ──
    ln = body["liunian_full"]
    assert len(ln) == 30
    assert ln[0] == {"year": 1999, "age": 1, "ganzhi": "己卯", "nayin": "城头土"}
    assert body["liunian_rel"]["ganzhi"] == "丙午"           # 2026 流年
    assert body["liunian_rel"]["year"] == 2026
    assert body["liuyue"]["months"][0] == "庚寅"             # 丙年正月庚寅（丙辛之年庚寅起）
    # 流时 = 今日 12 时辰（日柱随真年变动，锚定结构：子起亥终、12 支齐全）
    assert len(body["liushi"]["hours"]) == 12
    assert body["liushi"]["hours"][0][1] == "子"
    assert body["liushi"]["hours"][-1][1] == "亥"
    assert len({h[1] for h in body["liushi"]["hours"]}) == 12
    gr = body["ganzhi_rel"]
    assert len(gr) >= 4
    assert gr[0]["between"] == "年-日" and gr[0]["type"] == "天克"
    assert len(body["dayun_rel"]) == 12
    assert body["dayun_rel"][0]["sui"] == 3

    # ── L2：五行能量 ──
    we = body["wuxing_energy"]
    assert we["counts"] == {"金": 0, "木": 2, "水": 1, "火": 2, "土": 3}
    assert we["wangshuai"] == {"金": "死", "木": "休", "水": "囚", "火": "旺", "土": "相"}
    assert we["changsheng"]["日"] == "衰"
    assert we["strength"] == "偏弱"
    assert "水为用神" in we["yongshen"]

    # ── L2：称骨 ──
    cg = body["chenggu"]
    assert cg["weight_text"] == "五两五钱"
    assert cg["liang"] == 5 and cg["qian"] == 5
    assert cg["jieci"] and "六亲" in cg["jieci"]
    assert len(cg["parts"]) == 4                             # 年/月/日/时分项骨重
    assert cg["parts"][0]["label"].startswith("年 己卯")
    assert cg["parts"][0]["weight"]

    # ── L2：神煞 ──
    sd = body["shensha_detail"]
    assert len(sd) == 18                                     # 问真 59 种引擎逐项推算
    names = {it["name"] for it in sd}
    assert {"太极贵人", "文昌贵人", "驿马", "孤辰"} <= names
    assert all({"name", "source", "luck"} <= set(it) for it in sd)
    assert body["shensha"] == [it["name"] for it in sd]

    # ── L2：知识索引（六类） ──
    ki = body["knowledge_index"]
    assert set(ki.keys()) == {"shishen", "zhangsheng", "nayin",
                              "shensha", "tiangan", "dizhi"}
    assert sorted(ki["tiangan"]) == sorted({"己", "乙", "壬"})
    assert "日主" not in ki["shishen"]

    # ── meta：命主信息头 ──
    meta = body["meta"]
    assert meta["zodiac"] == "兔"
    assert meta["shichen"] == "午时"
    assert meta["lunar"]["year_ganzhi"] == "己卯"
    assert meta["lunar"]["month_text"] == "三月"
    assert meta["lunar"]["day_text"] == "廿八"
    assert meta["solar_text"].startswith("1999年5月13日")


def test_paipan_native_contract_fields():
    """原生契约字段（year/month/day/hour 时钟小时）同样可用（双契约兼容）。

    注意 normalize_hour 口径：hour 0-11 视为时辰序号（11=亥时 21 点），
    时钟小时须用 12-23（此处 hour=12 午时整点）。
    """
    r = _client().post("/api/paipan", json={
        "year": 1999, "month": 5, "day": 13,
        "hour": 12, "minute": 25, "city": "北京", "gender": "男",
    }, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert body["dayun"][0]["sui"] == 3


# ---------------------------------------------------------------- 鉴权
def test_paipan_401_unauthorized():
    """未登录 → 401（require_user 鉴权红线；生辰为敏感数据）。"""
    r = _client().post("/api/paipan", json=YAN)
    assert r.status_code == 401


# ---------------------------------------------------------------- 校验
@pytest.mark.parametrize("payload,msg", [
    ({}, "出生年/月/日不能为空"),
    ({"birthYear": 1999, "birthMonth": 2, "birthDay": 30}, "出生日期无效"),
    ({"birthYear": 1999, "birthMonth": 13, "birthDay": 1}, "出生月份"),
    ({"birthYear": 1899, "birthMonth": 5, "birthDay": 13}, "出生年份"),
    ({"birthYear": 1999, "birthMonth": 5, "birthDay": 0}, "出生日期"),
])
def test_paipan_400_bad_birth(payload, msg):
    """伪日期 / 越界 / 缺失 → 400（不直通引擎）。"""
    r = _client().post("/api/paipan", json=payload, headers=_headers())
    assert r.status_code == 400
    assert msg in r.json()["detail"]


# ---------------------------------------------------------------- 序列化函数单测
def test_serialize_bazi_handles_tuples():
    """序列化函数：tuple 字段（dayun/qiyun_detail）转 JSON 兼容 list。"""
    r = BaziEngine().calculate(1999, 5, 13, 11, 25, "北京", "男")
    out = paipan_api.serialize_bazi(r, BaziEngine(), paipan_api.BaziInput(
        year=1999, month=5, day=13, hour=11, minute=25, city="北京", gender="男"))
    assert isinstance(out["dayun"], list) and isinstance(out["dayun"][0], dict)
    assert isinstance(out["qiyun_detail"], list)
    assert out["qiyun_detail"][0] == r.qiyun_detail[0]
    # 全字段集合（任务验收清单）
    required = {"bazi", "pillars", "day_master", "shishen", "nayin", "gender",
                "jiaoyun", "siling", "qiyun_desc", "qiyun_detail", "dayun",
                "liunian_full", "liunian_rel", "liuyue", "liushi",
                "dayun_rel", "ganzhi_rel", "wuxing_energy", "chenggu",
                "shensha_detail", "knowledge_index", "meta"}
    assert required <= set(out.keys())
