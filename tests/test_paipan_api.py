"""排盘结果 API（L4）：POST /api/paipan — 全字段锚点 + 鉴权 + 校验。

锚点盘：闫海洋命例 1999-05-13 11:25 北京 男（与 test_bazi_jiaoyun 问真排盘页
锚点同盘：四柱 己卯 己巳 乙丑 壬午 / 起运 3 岁 / 交运逢辛、丙年 / 司令庚）。
注意：R2-4（2026-09-03 产品裁决）真太阳时默认开——传「北京」12:25 修正
12:14 后排盘（起运分解分钟级差异为修正固有；时柱壬午等整值锚点双口径一致）。
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
    # R2-4（真太阳时默认开=产品口径）：午时序号 6 → 时钟小时 12 → 12:25
    # 修正 12:14 排盘 → 起运 2年4月26天1时（R1-3 校准值回归；R2-1 期默认关
    # 口径 27天0时 随默认反转退役；时柱壬午锚点双口径一致）。
    assert body["qiyun_desc"] == "出生后2年4月26天1时起运"
    assert body["dayun"][0]["sui"] == 3                     # 起运 3 岁
    jy = body["jiaoyun"]
    # R1-3 午时校准后交运时刻 ≈2001-10-09（原 11 点口径 ≈2001-10-04 白露后），
    # 跨过寒露（10-08）→ 寒露后1天；交运年 2001 辛巳 → 五合对 辛、丙 不变。
    assert "交大运" in jy["page_text"] and "寒露后" in jy["page_text"]
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
    assert {k: ln[0][k] for k in ("year", "age", "ganzhi", "nayin")} == \
        {"year": 1999, "age": 1, "ganzhi": "己卯", "nayin": "城头土"}
    # 批1 流年详解：每项带 流年神煞/与原局关系/所在大运（含大运vs流年关系）
    assert {"shensha", "rel", "dayun"} <= set(ln[0])
    assert {"sui", "ganzhi", "rel"} == set(ln[0]["dayun"])
    assert ln[27]["shensha"] and isinstance(ln[27]["rel"], list)
    assert ln[27]["dayun"]["ganzhi"]  # 2026 虚岁 28 ≥ 起运 3 岁 → 有所在大运
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


def test_paipan_xiaoyun_dyshensha():
    """小运/大运神煞序列化（对比报告 P3 补全）：字段存在、与盘面同口径。

    YAN 盘（己卯 己巳 乙丑 壬午 男）：阴年男 → 大运/小运均逆排——
    时柱壬午 逆推 → 小运前 3 = [辛巳, 庚辰, 己卯]；dyshensha 与 dayun
    12 步同序同位（每步干支 == 大运干支，神煞为名称列表）。
    """
    r = _client().post("/api/paipan", json=YAN, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    xy = body["xiaoyun"]
    assert isinstance(xy, list) and len(xy) == 110
    assert xy[:3] == ["辛巳", "庚辰", "己卯"]          # 时柱壬午 逆排（阴男）
    assert len(set(xy)) == 60                           # 六十甲子循环（110 条含 1 圈余 50）

    ds = body["dyshensha"]
    assert isinstance(ds, list) and len(ds) == 12       # 与 12 步大运同序同位
    assert [g for g, _ in ds] == [d["ganzhi"] for d in body["dayun"]]
    assert ds[0][0] == "戊辰" and ds[0][1]              # 首步神煞非空
    assert all(isinstance(n, str) for n in ds[0][1])


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


# ---------------------------------------------------------------- 晚子时称骨自洽
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}


def _weight_to_qian(w: str) -> int:
    """分项骨重文本（如 "一两九钱"/"八钱"/"一两"）→ 钱整数。"""
    total, i = 0, 0
    while i < len(w):
        if w[i] == "两":
            total += _CN_NUM[w[i - 1]] * 10
        elif w[i] == "钱":
            total += _CN_NUM[w[i - 1]]
        i += 1
    return total


def test_paipan_late_zi_chenggu_consistent():
    """晚子时 23:50：称骨分项合计 == 总重，meta 农历日与日柱自洽（归日口径）。

    1999-05-13 23:50 北京 男：真太阳时修正后仍属晚子时 → 引擎按次日 5/14 排盘
    （日柱丙寅），称骨/农历信息必须同为次日口径，不得用原始日期重算。
    """
    r = _client().post("/api/paipan", json={
        "year": 1999, "month": 5, "day": 13,
        "hour": 23, "minute": 50, "city": "北京", "gender": "男",
    }, headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    # 晚子时归日：日柱 = 次日 5/14 丙寅（原始日 5/13 为乙丑）
    assert body["bazi"][2] == "丙寅"

    # 分项合计 == 总重（此前分项用原始日期算成六两一钱，与总重六两九钱矛盾）
    cg = body["chenggu"]
    total_qian = cg["liang"] * 10 + cg["qian"]
    parts_sum = sum(_weight_to_qian(p["weight"]) for p in cg["parts"])
    assert parts_sum == total_qian == 69
    assert cg["weight_text"] == "六两九钱"
    # 分项月/日同为归日口径（次日 3/29）
    assert cg["parts"][1]["label"] == "月 三月"
    assert cg["parts"][2]["label"] == "日 廿九"

    # meta 农历日与日柱自洽（丙寅 = 5/14 农历三月廿九，而非原始日 5/13 的廿八）
    meta_lunar = body["meta"]["lunar"]
    assert meta_lunar["day_ganzhi"] == "丙寅"
    assert meta_lunar["month_text"] == "三月"
    assert meta_lunar["day_text"] == "廿九"
    assert body["meta"]["zodiac"] == "兔"  # 年柱己卯 → 兔（年柱亦为归日口径）


def test_paipan_shichen_all_hours():
    """SHICHEN_NAME 全 24 小时覆盖：偶数小时（原生契约 hour=12/22）非空且正确。

    此前 SHICHEN_NAME 只覆盖奇数小时 + 23/0，hour=12 等偶数小时 shichen 为空串。
    """
    # 时钟小时 12（午时整点）→ 午时
    r = _client().post("/api/paipan", json={
        "year": 1999, "month": 5, "day": 13,
        "hour": 12, "minute": 25, "city": "北京", "gender": "男",
    }, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["shichen"] == "午时"

    # 时钟小时 22（亥时末）→ 亥时
    r = _client().post("/api/paipan", json={
        "year": 1999, "month": 5, "day": 13,
        "hour": 22, "minute": 25, "city": "北京", "gender": "男",
    }, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["shichen"] == "亥时"

    # 全部 24 小时均有映射（按时辰起点：子23-1/丑1-3/…/亥21-23）
    expect = {23: "子时", 0: "子时", 1: "丑时", 2: "丑时", 3: "寅时", 4: "寅时",
              5: "卯时", 6: "卯时", 7: "辰时", 8: "辰时", 9: "巳时", 10: "巳时",
              11: "午时", 12: "午时", 13: "未时", 14: "未时", 15: "申时", 16: "申时",
              17: "酉时", 18: "酉时", 19: "戌时", 20: "戌时", 21: "亥时", 22: "亥时"}
    assert paipan_api.SHICHEN_NAME == expect


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
                "xiaoyun", "dyshensha",
                "liunian_full", "liunian_rel", "liuyue", "liushi",
                "dayun_rel", "ganzhi_rel", "wuxing_energy", "chenggu",
                "shensha_detail", "knowledge_index", "meta"}
    assert required <= set(out.keys())
