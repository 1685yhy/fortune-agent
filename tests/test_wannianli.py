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

from src.engines.zeri import JIANCHU_QUALITY  # noqa: E402
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
        # k27d: 建除吉凶 `quality` **仍在载荷内**（老客户端兼容, 值与 k27c 之前逐字
        # 相同）—— 用户面不展示由**前端不渲染**达成, 见 test_jianchu_quality_kept_k27d
        assert d["quality"] == JIANCHU_QUALITY[d["jianchu"]]
        assert isinstance(d["yi_short"], list) and d["yi_short"]
        # k27: ji_short 允许为空（空忌日 —— 2026 仅 2026-02-10 一天, 由
        # test_month_view_empty_ji_day_2026_02_10 锚点钉住）; 2026-08 无空忌日,
        # 但不得再断言"每月每日 ji_short 恒非空"（与 A 口径语义矛盾）。
        assert isinstance(d["ji_short"], list)
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
    # k27d: 建除吉凶字段（quality）保留（老客户端兼容）—— 用户面不展示由前端不渲染
    # 达成; 值与建除表一致（2026-08-19 = 执 → 平）
    assert body["jianchu"]["quality"] == JIANCHU_QUALITY[body["jianchu"]["name"]] == "平"
    assert body["jianchu"]["desc"]
    # 二十八宿: 权威历法数据（lunar-python getXiu 吉凶）仍在载荷内（既有权威锚点
    # 依赖, 见 test_day_detail_ershibaxiu_anchors）, 但用户面不再渲染该吉凶标签
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
    # 建除表对「执」的吉凶判定 = 平（k27d: 字段恢复输出, 老客户端兼容; 前端不渲染）
    assert d7["jianchu"]["quality"] == "平"

    # 非节气日不位移：2026-08-19（申月起建）执日不变
    d19 = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers()).json()
    assert d19["jianchu"]["name"] == "执"


# ------------------------- k27d: 建除吉凶 quality 字段「后端保留 / 前端不渲染」
def test_jianchu_quality_kept_k27d():
    """k27d: `quality` **后端仍在载荷内**（月视图 + 日详情）, 用户面不展示由**前端
    不渲染**达成 —— 两侧同时锁住。

    背景: k27c 把 `quality` 从后端删掉（产品口径: 万年历面不出现吉/凶标签）; 但
    线上仍有老客户端构建（1.36.0 体验版/正式版）会渲染该字段 → 删除会渲染出空
    括号「闭日（）」用户可见回归, 而我们无法控制老客户端何时更新。k27d 恢复后端
    输出（值与 k27c 之前逐字相同）, 前端保持不渲染 → 新客户端无标签（目标态）,
    老客户端与今天完全一致（无回归）。

    覆盖两侧:
    1. 后端: 月视图逐日（2026-08 全月）与日详情（含 10-01 / 05-02 / 节气锚点）
       `quality` 存在且 == `JIANCHU_QUALITY[jianchu]`（建除表取值, 逐日逐值）;
       值日名/说明/值宿一并保留;
    2. 前端（源码级 tripwire, 运行时裁决在 node 测试
       miniprogram/tests/wannianli.test.js「无 qCls / quality / jcCls」）:
       页面不得绑定/透传该字段 —— wxml 无 `quality` / `qCls` / `jixiong` 绑定,
       wxss 无 `.q-` 吉凶配色, js 无 `qCls`/`jcCls`/`.quality` 代码引用（注释除外）。
    """
    # ---- 1) 后端: 字段仍在, 取值与建除表一致
    mv = _client().get("/api/wannianli?year=2026&month=8", headers=_headers()).json()
    assert len(mv["days"]) == 31
    for d in mv["days"]:
        assert "quality" in d, f"{d['date']} 月视图缺建除吉凶字段（老客户端兼容）"
        assert d["quality"] == JIANCHU_QUALITY[d["jianchu"]], d
        assert d["quality"] in ("吉", "平", "凶"), d

    for ds in ("2026-10-01", "2026-05-02", "2026-08-07", "2026-08-19"):
        body = _client().get(f"/api/wannianli/day?date={ds}", headers=_headers()).json()
        assert body["jianchu"]["quality"] == JIANCHU_QUALITY[body["jianchu"]["name"]], ds
        assert body["jianchu"]["name"] and body["jianchu"]["desc"], ds
        assert body["ershibaxiu"]["name"], ds          # 值宿名保留

    # ---- 2) 前端: 不渲染（源码级 tripwire; 页面代码不得引用该字段）
    import re
    from pathlib import Path
    page_dir = (Path(__file__).resolve().parent.parent
                / "miniprogram" / "pages" / "wannianli")

    def _code_only(text: str) -> str:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)     # wxml 注释
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)      # 块注释
        return "\n".join(ln for ln in text.splitlines()
                         if not ln.strip().startswith("//"))   # 行注释

    js = _code_only((page_dir / "wannianli.js").read_text(encoding="utf-8"))
    wxml = _code_only((page_dir / "wannianli.wxml").read_text(encoding="utf-8"))
    wxss = _code_only((page_dir / "wannianli.wxss").read_text(encoding="utf-8"))
    for token in ("qCls", "jcCls", ".quality"):
        assert token not in js, f"页面仍消费建除吉凶（{token}）: 用户面应无吉/凶标签"
    for token in ("quality", "qCls", "jixiong"):
        assert token not in wxml, f"wxml 仍绑定建除/宿吉凶（{token}）"
    assert not any(c in wxss for c in (".q-ji", ".q-ping", ".q-xiong")), "wxss 仍留吉凶配色"


# ---------------------------------------------------------------- 宜忌规则确定性
def test_yi_ji_rule_consistency():
    """宜忌规则确定性：建除宜忌（zeri 同源表）+ 当日黄历宜忌合并，同日期多次一致。

    k27：合并口径 = `zeri.ZeriEngine._day_yi_ji`（A 口径双向消解）。本锚点日
    （2026-08-19 建除「执」）建除表忌 出行/嫁娶/开市/入宅 中, 出行 不在当日黄历宜
    （权威忌 嫁娶/入宅/开市/交易）→ 依 A 口径保留于忌; 出行 亦不在权威忌中,
    属「建除表粗粒度近似保留项」。
    """
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
    # k27d: 建除吉凶同源断言（月视图 quality == 日详情 jianchu.quality, 两侧同表取值）
    assert d19["quality"] == r1["jianchu"]["quality"] == JIANCHU_QUALITY[d19["jianchu"]]
    assert d19["yi_short"] == r1["yi"][:3]
    assert d19["ji_short"] == r1["ji"][:3]


# ---------------------------------------------------------------- 宜忌冲突消解（k27 A 口径）
def test_yi_ji_conflict_resolution_day_detail():
    """宜忌冲突消解（k27 A 口径 = 神煞级优先, **双向**）: 同日 宜∩忌=∅,
    建除表与当日黄历冲突的词一律以黄历为准 —— 黄历宜 → 归宜（从忌剔除）,
    黄历忌 → 归忌（从宜剔除）。

    2026-08-21（建除「危」）: 建除表忌 {出行,嫁娶,开市,移徙} 全被当日黄历宜覆盖
    （xzw 08-21 宜 结婚/出行/搬家/开业…）→ 归宜; 旧「忌优先」口径把四词压入忌。
    2025-01-15（建除「危」）: 出行/嫁娶/开市/移徙/动土 黄历宜 → 归宜;
    安床/纳畜 黄历忌（且建除表宜列有）→ 归忌。
    （旧断言「冲突项保留于忌、从宜中剔除」= 忌优先, 与权威相反, k27 作废。）
    """
    from lunar_python import Solar
    from src.engines.zeri import JIANCHU_YI_JI
    for date in ("2026-08-21", "2025-01-15"):
        r = _client().get(f"/api/wannianli/day?date={date}", headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        yi, ji = body["yi"], body["ji"]
        assert set(yi) & set(ji) == set(), f"{date} 宜忌仍有交集: {set(yi) & set(ji)}"
        lunar = Solar.fromYmd(*(int(x) for x in date.split("-"))).getLunar()
        auth_yi = {x for x in lunar.getDayYi() if x != "无"}
        auth_ji = {x for x in lunar.getDayJi() if x != "无"}
        # 建除表两列词逐一核位：黄历宜 → 必在宜且不在忌; 黄历忌 → 必在忌且不在宜
        for w in JIANCHU_YI_JI[body["jianchu"]["name"]]["yi"] \
                + JIANCHU_YI_JI[body["jianchu"]["name"]]["ji"]:
            if w in auth_yi:
                assert w in yi and w not in ji, f"{date} 黄历宜 {w} 应归宜"
            if w in auth_ji:
                assert w in ji and w not in yi, f"{date} 黄历忌 {w} 应归忌"
    # 锚点: 2026-08-21 旧「忌优先」压入忌的四词, 现归宜（= xzw 权威宜表）
    b = _client().get("/api/wannianli/day?date=2026-08-21", headers=_headers()).json()
    for w in ("出行", "嫁娶", "开市", "移徙"):
        assert w in b["yi"] and w not in b["ji"], f"2026-08-21 {w} 应归宜: {b['yi']}"


def test_yi_ji_conflict_resolution_month_view():
    """月视图 yi_short/ji_short 与日详情同一消解口径：无交集, 且简表=全表前 3 项。"""
    c = _client()
    for date in ("2026-08-21", "2025-01-15"):
        y, m, d = (int(x) for x in date.split("-"))
        mv = c.get(f"/api/wannianli?year={y}&month={m}", headers=_headers()).json()
        day = next(x for x in mv["days"] if x["date"] == date)
        assert set(day["yi_short"]) & set(day["ji_short"]) == set(), \
            f"{date} 月视图简表宜忌仍有交集"
        # 与日详情全表同源：简表 = 消解后全表前 3 项
        det = c.get(f"/api/wannianli/day?date={date}", headers=_headers()).json()
        assert day["yi_short"] == det["yi"][:3]
        assert day["ji_short"] == det["ji"][:3]


# ---------------------------------------------------------------- k27 三面单一事实源
def test_day_detail_single_source_matches_day_yi_ji_whole_2026():
    """k27 单一事实源（验收 3）: 2026 全年 365 天, 万年历 `day_detail` 的 宜/忌
    与择吉引擎 `ZeriEngine._day_yi_ji`（chat/工具/计划路径同源）**逐日逐项相等**,
    且 `set(yi) & set(ji) == ∅`（修复前万年历自身靠「忌优先」消解自洽但方向反权威,
    zeri 面 75 天既宜又忌 —— 两面各修一半）。

    模块级直测（不占 API/DB）, 月视图 yi_short/ji_short = 同一列表前 3 项
    （由 test_yi_ji_rule_consistency 与 test_yi_ji_conflict_resolution_month_view
    在 API 面钉住）。
    """
    import calendar as _c

    from lunar_python import Solar

    from src.engines.wannianli import WannianliEngine, _zeri as wz
    from src.engines.zeri import ZeriEngine

    wl, ze = WannianliEngine(), ZeriEngine()
    checked = 0
    for month in range(1, 13):
        for d in range(1, _c.monthrange(2026, month)[1] + 1):
            det = wl.day_detail(2026, month, d)
            solar = Solar.fromYmd(2026, month, d)
            lunar = solar.getLunar()
            jc = wz._calc_jianchu_with_jieqi(
                lunar.getEightChar().getMonth()[1],
                lunar.getEightChar().getDay()[1],
                lunar.getJieQi() or "")
            yi, ji = ze._day_yi_ji(jc, lunar)
            assert det["jianchu"]["name"] == jc
            assert det["yi"] == yi and det["ji"] == ji, \
                f"{det['date']} 万年历与 _day_yi_ji 不一致"
            assert set(det["yi"]) & set(det["ji"]) == set(), f"{det['date']} 既宜又忌"
            checked += 1
    assert checked == 365


def test_month_view_empty_ji_day_2026_02_10():
    """k27 空忌日: 2026-02-10 权威忌=哨兵「无」且建除「除」表忌全被黄历宜覆盖
    → 万年历 ji 为空列表（2026 全年仅此 1 天）, 月视图 ji_short 同为空 ——
    文本层整行不渲染（handler._yi_ji_render_lines）, 万年历不新造文案。
    旧「忌优先」口径下该日 ji=[嫁娶,出行,开市,入宅,安床]（与权威 xzw「忌无」相反）。"""
    mv = _client().get("/api/wannianli?year=2026&month=2", headers=_headers()).json()
    day = next(x for x in mv["days"] if x["date"] == "2026-02-10")
    assert day["ji_short"] == []
    det = _client().get("/api/wannianli/day?date=2026-02-10", headers=_headers()).json()
    assert det["ji"] == [], det["ji"]
    assert {"嫁娶", "出行", "开市", "入宅", "安床"} <= set(det["yi"])   # = 权威宜
    # 对照: 空忌不是普遍现象 —— 同月其余日期 ji 非空
    assert all(x["ji_short"] for x in mv["days"] if x["date"] != "2026-02-10"), \
        "2026-02 仅 02-10 应为空忌日"


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


# ---------------------------------------------------------------- 吉时/彭祖百忌/胎神（B5-2 L 三字段）
def test_day_detail_jishi_pengzu_taishen():
    """day_detail 三字段（B5-2）：吉时 13 时辰（子时早/晚分列）、彭祖百忌两行、胎神占方。

    2026-08-19（乙丑日，既有锚点日）:
    - jishi: lunar.getTimes() 13 项 = 早子时 00:00-00:59 起 至 晚子时 23:00-23:59
      （子时按传统早/晚拆分，晚子时换日 → 丙寅日戊子），每项含
      time/range/ganzhi/luck/tianshen/type/yi/ji；一日吉凶时辰兼有
    - pengzu: {gan: 乙不栽植千株不长, zhi: 丑不冠带主不还乡}
    - taishen: {desc: 碓磨厕 外东南}
    """
    r = _client().get("/api/wannianli/day?date=2026-08-19", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()

    # 既有字段零回归（三字段为纯增量）
    assert body["ganzhi"]["day"] == "乙丑"
    assert body["yi"] and body["ji"]

    # 吉时：13 时辰结构
    jishi = body["jishi"]
    assert isinstance(jishi, list) and len(jishi) == 13
    assert jishi[0]["time"] == "早子时"
    assert jishi[0]["range"] == "00:00-00:59"
    assert jishi[0]["ganzhi"] == "丙子"      # 乙日 日上起时 → 丙子
    assert jishi[11]["time"] == "亥时"
    assert jishi[11]["range"] == "21:00-22:59"
    assert jishi[12]["time"] == "晚子时"
    assert jishi[12]["range"] == "23:00-23:59"
    assert jishi[12]["ganzhi"] == "戊子"      # 晚子时换日 → 丙寅日戊子
    for t in jishi:
        assert t["time"] and t["range"] and t["ganzhi"]
        assert t["luck"] in ("吉", "凶")
        assert t["type"] in ("黄道", "黑道")
        assert t["tianshen"]
        assert isinstance(t["yi"], list) and isinstance(t["ji"], list)
    lucks = {t["luck"] for t in jishi}
    assert lucks == {"吉", "凶"}               # 一日兼有吉凶时辰

    # 时辰锚点: 2026-08-19 巳时(09:00-10:59) = 辛巳 · 黄道玉堂吉
    si = next(t for t in jishi if t["time"] == "巳时")
    assert si["range"] == "09:00-10:59" and si["ganzhi"] == "辛巳"
    assert si["luck"] == "吉" and si["type"] == "黄道" and si["tianshen"] == "玉堂"

    # 彭祖百忌
    assert body["pengzu"] == {
        "gan": "乙不栽植千株不长",
        "zhi": "丑不冠带主不还乡",
    }

    # 胎神占方
    assert body["taishen"] == {"desc": "碓磨厕 外东南"}


def test_day_detail_jishi_pengzu_taishen_other_dates():
    """三字段在其他日期同样存在且结构正确（立秋日/下界 1900-01-01）。"""
    for date, pengzu_gan, pengzu_zhi, tai in [
        ("2026-08-07", "癸不词讼理弱敌强", "丑不冠带主不还乡", "房床厕 外东北"),
        ("1900-01-01", "甲不开仓财物耗散", "戌不吃犬作怪上床", "占门栖 外西南"),
    ]:
        r = _client().get(f"/api/wannianli/day?date={date}", headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["jishi"]) == 13, date
        assert all(t["time"] and t["range"] and t["luck"] in ("吉", "凶")
                   for t in body["jishi"]), date
        assert body["pengzu"]["gan"] == pengzu_gan, date
        assert body["pengzu"]["zhi"] == pengzu_zhi, date
        assert body["taishen"]["desc"] == tai, date


# ---------------------------------------------------------------- 闰月覆盖
def test_leap_month():
    """闰月：2025-07-25 = 闰六月初一，lunar.leap=True 且月名带"闰"。"""
    r = _client().get("/api/wannianli/day?date=2025-07-25", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lunar"]["leap"] is True
    assert body["lunar"]["month"] == "闰六月"
    assert body["lunar"]["day"] == "初一"
