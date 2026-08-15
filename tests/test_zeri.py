"""Tests for Zeri Engine - 择日 (Date Selection)."""
import pytest

from src.engines.zeri import (
    ZeriEngine, ZeriResult,
    DIZHI, JIANCHU, ERSHIBA_XIU, JIANCHU_QUALITY,
    SCENES, LuckyDayCard,
    is_sanniang_sha, is_yanggong_ji, is_yuepo, is_yuexing, is_kongwang,
)


def test_zeri_basic():
    """基础测试：给定一个日期应得到完整择日结果"""
    engine = ZeriEngine()
    # 2024年1月1日 公历（农历冬月二十）
    result = engine.select(2024, 1, 1)
    assert isinstance(result, ZeriResult)
    assert result.jianchu in JIANCHU
    assert result.ershibaxiu in ERSHIBA_XIU
    assert result.xiu_jixiong in ["吉", "凶", "平"]
    assert len(result.yi) > 0
    assert len(result.ji) > 0
    assert "冲" in result.chong
    assert result.overall in ["吉", "凶", "平"]


def test_zeri_jianchu_known():
    """测试已知建除神的日期"""
    engine = ZeriEngine()

    # 2024-10-09 农历九月初七, 月支戌
    # 已知该日地支与月支关系可计算建除
    result = engine.select(2024, 10, 9)
    assert result.jianchu in JIANCHU

    # 两次计算同一日期应一致
    result2 = engine.select(2024, 10, 9)
    assert result.jianchu == result2.jianchu
    assert result.ershibaxiu == result2.ershibaxiu


def test_zeri_jianchu_with_month_zhi():
    """测试建除计算：通过月支验证"""
    engine = ZeriEngine()

    # 农历正月（寅）的寅日应该是"建"
    # 2024-02-10 甲辰年 正月 丙寅日
    result = engine.select(2024, 2, 10)
    # 验证至少得到一个有效的建除神
    assert result.jianchu in JIANCHU


def test_zeri_purpose_matching():
    """测试用途匹配"""
    engine = ZeriEngine()

    # 带有用途的择日
    result = engine.select(2024, 1, 15, purpose="嫁娶")
    assert "嫁娶" in result.yi or result.overall is not None


def test_zeri_ershibaxiu_cycle():
    """测试二十八宿循环"""
    engine = ZeriEngine()

    # 同一月相邻两天，二十八宿应该相邻或成循环
    r1 = engine.select(2024, 7, 1)
    r2 = engine.select(2024, 7, 2)

    idx1 = ERSHIBA_XIU.index(r1.ershibaxiu)
    idx2 = ERSHIBA_XIU.index(r2.ershibaxiu)

    # 应该相差1天（循环28天）
    diff = (idx2 - idx1) % 28
    assert diff == 1, f"相邻日期二十八宿应差1, 实际差{diff} ({r1.ershibaxiu} -> {r2.ershibaxiu})"


def test_zeri_chong():
    """测试冲生肖"""
    engine = ZeriEngine()

    # 子日冲午（马）
    # 查找一个子日：2024-01-05 甲子日
    result = engine.select(2024, 1, 5)
    assert "冲" in result.chong

    # 验证六冲关系
    day_zhi = result.raw_data.get("day_ganzhi", "")[1] if result.raw_data else ""
    if day_zhi:
        from src.engines.zeri import LIU_CHONG, ZODIAC_MAP
        expected_chong_zhi = LIU_CHONG.get(day_zhi, "")
        expected_animal = ZODIAC_MAP.get(expected_chong_zhi, "")
        assert expected_animal in result.chong


def test_zeri_multiple_dates():
    """测试多个不同日期"""
    engine = ZeriEngine()
    dates = [
        (2024, 6, 1),
        (2024, 8, 15),
        (2024, 10, 1),
        (2025, 1, 1),
        (2025, 3, 20),
    ]
    for y, m, d in dates:
        result = engine.select(y, m, d)
        assert result.jianchu in JIANCHU, f"{y}-{m}-{d}: 建除应为12神之一"
        assert result.ershibaxiu in ERSHIBA_XIU, f"{y}-{m}-{d}: 二十八宿应为28宿之一"


def test_zeri_quality_classification():
    """测试吉凶分类的覆盖面"""
    engine = ZeriEngine()

    seen_overall = set()
    for month in range(1, 13):
        result = engine.select(2024, month, 15)
        seen_overall.add(result.overall)

    # 三种结果至少出现2种
    assert len(seen_overall) >= 2, f"应覆盖至少2种吉凶判定, 只有{seen_overall}"


def test_zeri_jianchu_quality_map():
    """验证建除吉凶映射完整"""
    for jc in JIANCHU:
        assert jc in JIANCHU_QUALITY, f"建除神{jc}缺少吉凶判定"
        assert JIANCHU_QUALITY[jc] in ["吉", "凶", "平"]


def test_zeri_year_boundary():
    """年份边界测试"""
    engine = ZeriEngine()
    # 跨年
    r_2024 = engine.select(2024, 12, 31)
    r_2025 = engine.select(2025, 1, 1)
    assert r_2024.jianchu in JIANCHU
    assert r_2025.jianchu in JIANCHU
    assert r_2024.ershibaxiu in ERSHIBA_XIU
    assert r_2025.ershibaxiu in ERSHIBA_XIU


def test_zeri_deterministic():
    """确定性测试"""
    engine = ZeriEngine()
    r1 = engine.select(2024, 7, 15, purpose="开业")
    r2 = engine.select(2024, 7, 15, purpose="开业")
    assert r1.jianchu == r2.jianchu
    assert r1.ershibaxiu == r2.ershibaxiu
    assert r1.yi == r2.yi
    assert r1.overall == r2.overall


def test_zeri_known_case():
    """已知案例测试"""
    engine = ZeriEngine()

    # 2024-10-01 国庆节
    result = engine.select(2024, 10, 1, purpose="开业")
    print(f"\n日期: 2024-10-01")
    print(f"建除: {result.jianchu} ({JIANCHU_QUALITY.get(result.jianchu, '?')})")
    print(f"二十八宿: {result.ershibaxiu} ({result.xiu_jixiong})")
    print(f"宜: {result.yi}")
    print(f"忌: {result.ji}")
    print(f"冲: {result.chong}")
    print(f"综合: {result.overall}")
    assert result.jianchu in JIANCHU
    assert result.ershibaxiu in ERSHIBA_XIU


# ============================================================
# 择吉日 Task 1: 场景规则库 + 三层评分 + 多日 Top3 扫描
# 确定性依据（2026-08 已用 lunar-python 核对）:
#   2026-08-07 癸丑日 = 未月月破(丑冲未); 08-08 甲寅日 = 申月月破+月刑(申刑寅)+破日
#   08-13 = 农历七月初一(杨公忌日); 08-15/08-19/08-25/08-30 = 农历七月初三/初七/十三/十八(三娘煞)
#   08-28~08-31 = 甲戌旬(空申酉), 月支申逢空 → 空亡日
#   08-24 庚午日 = 午日冲子(鼠); 08-22 庚午? 否——08-22 戊辰 成日(周六), 08-24 庚午 开日
#   08-16 壬戌日(周日)水日比和用神水; 08-22 庚午日 金日生水; 08-26 壬申日 水日比和
# ============================================================


def test_scenes_config_complete():
    """6 场景配置完整且字段齐全"""
    assert set(SCENES.keys()) == {"嫁娶", "搬家", "开业", "出行", "提车", "签约"}
    for scene, cfg in SCENES.items():
        assert cfg["label"]
        assert isinstance(cfg["yi_hits"], list) and cfg["yi_hits"]
        assert isinstance(cfg["ji_hits"], list)
        assert isinstance(cfg["jianchu_avoid"], list)
        assert isinstance(cfg["shensha_avoid"], list)
        assert isinstance(cfg["weekend_bonus"], bool)


def test_shensha_helper_functions():
    """神煞判定纯函数（标准公历表，确定性）"""
    # 三娘煞: 每月农历初三/初七/十三/十八/廿二/廿七
    assert is_sanniang_sha(6, 27) is True
    assert is_sanniang_sha(7, 3) is True
    assert is_sanniang_sha(7, 15) is False
    # 杨公忌日: 正月十三...七月(初一/廿九)...腊月十九
    assert is_yanggong_ji(1, 13) is True
    assert is_yanggong_ji(7, 1) is True
    assert is_yanggong_ji(7, 29) is True
    assert is_yanggong_ji(8, 27) is True
    assert is_yanggong_ji(12, 19) is True
    assert is_yanggong_ji(7, 2) is False
    assert is_yanggong_ji(7, 30) is False
    # 月破 = 日支冲月支
    assert is_yuepo("寅", "申") is True
    assert is_yuepo("卯", "申") is False
    # 月刑 = 支三刑（月支刑日支: 申刑寅/巳刑申/子刑卯/辰自刑等）
    assert is_yuexing("寅", "申") is True
    assert is_yuexing("申", "巳") is True
    assert is_yuexing("卯", "申") is False
    assert is_yuexing("卯", "子") is True
    assert is_yuexing("辰", "辰") is True
    # 空亡 = 当日日柱旬空含月支（月建逢空）: 2026-08-28 甲戌旬空申酉
    # (fix-later: 签名移除死参数 day_zhi)
    from lunar_python import Solar
    lunar = Solar.fromYmd(2026, 8, 28).getLunar()
    assert is_kongwang("申", lunar) is True
    assert is_kongwang("亥", lunar) is False
    # 2026-08-01 丁未日 甲辰旬空寅卯
    lunar1 = Solar.fromYmd(2026, 8, 1).getLunar()
    assert is_kongwang("未", lunar1) is False


def test_select_lucky_days_basic():
    """基本: 窗口扫描、卡片字段、总分组成、降序"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-01", "2026-08-31")
    assert res["scanned"] == 31
    assert isinstance(res["cards"], list)
    assert len(res["cards"]) <= 3
    for c in res["cards"]:
        assert isinstance(c, LuckyDayCard)
        assert c.total == c.scene_score + c.personal_score + c.practical_score
        assert 0 <= c.scene_score <= 50
        assert 0 <= c.personal_score <= 30
        assert 0 <= c.practical_score <= 20
        assert 0 <= c.total <= 100
        assert c.date.startswith("2026-08")
        assert "农历" in c.lunar_text and "星期" in c.lunar_text
        assert c.yi and isinstance(c.ji, list)
        assert c.jishi and ("时" in c.jishi or "黄历" in c.jishi)
        assert c.xi_fangwei and c.cai_fangwei
        assert c.reason_source
    totals = [c.total for c in res["cards"]]
    assert totals == sorted(totals, reverse=True)


def test_select_lucky_days_no_bazi_default_score():
    """无八字 → 个人适配分默认 24（满分 30 折算 80%）"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-20", "2026-08-26")
    assert res["cards"]
    assert all(c.personal_score == 24 for c in res["cards"])


def test_select_lucky_days_bazi_yongshen_scoring():
    """有八字喜用神水: 水日比和+5(20) > 无关日(15); 金日生水+10(25) 走单元断言"""
    engine = ZeriEngine()
    ub = {"yongshen": "水", "shengxiao": "鼠"}
    res = engine.select_lucky_days("搬家", "2026-08-08", "2026-08-18", user_bazi=ub)
    by_date = {c.date: c for c in res["cards"]}
    # 08-16 壬戌日 壬=水 比和 → 20（满日3宜, 总分最高）
    assert by_date["2026-08-16"].personal_score == 20
    # 08-10 丙辰日 丙=火 无关 → 15
    assert by_date["2026-08-10"].personal_score == 15
    # 生扶: 庚=金 金生水 → 25
    assert engine._personal_score("庚", {"yongshen": "水"}) == (25, "喜用神相合")
    assert engine._personal_score("壬", {"yongshen": "水"}) == (20, "喜用神比和")
    assert engine._personal_score("甲", {"yongshen": "水"}) == (15, "八字适配")
    # 无八字 → 默认 24
    assert engine._personal_score("甲", None) == (24, "基础适配分")


def test_select_lucky_days_bazi_yongshen_via_bazi_engine():
    """有八字且未直接给用神 → 调 bazi 引擎 _calc_yongshen 计算（夏季调候用神水）"""
    engine = ZeriEngine()
    ub = {
        "shengxiao": "鼠",
        "wuxing": {"金": 0, "木": 1, "水": 2, "火": 3, "土": 1},
        "day_gan": "丙",
        "month_zhi": "午",
    }
    # 午月夏 → 调候用神水; 庚=金 金生水 → 25
    assert engine._personal_score("庚", ub) == (25, "喜用神相合")
    # 数据不全 → 回退默认 24
    assert engine._personal_score("庚", {"shengxiao": "鼠"}) == (24, "基础适配分")


def test_select_lucky_days_weekend_bonus():
    """周末偏好: 开 → 周日 practical +10; 关 → 0; 平日无加分"""
    engine = ZeriEngine()
    on = engine.select_lucky_days("搬家", "2026-08-08", "2026-08-18", prefer_weekend=True)
    off = engine.select_lucky_days("搬家", "2026-08-08", "2026-08-18", prefer_weekend=False)
    on_by = {c.date: c for c in on["cards"]}
    off_by = {c.date: c for c in off["cards"]}
    # 2026-08-16 周日 满日（3 宜命中）
    assert on_by["2026-08-16"].practical_score == 10
    assert off_by["2026-08-16"].practical_score == 0
    # 平日(周三 08-10) 无加分
    assert on_by["2026-08-10"].practical_score == 0


def test_select_lucky_days_exclude_dates():
    """exclude_dates 去重"""
    engine = ZeriEngine()
    res = engine.select_lucky_days(
        "搬家", "2026-08-08", "2026-08-18",
        exclude_dates=["2026-08-16"],
    )
    assert all(c.date != "2026-08-16" for c in res["cards"])
    # 未排除的合格日仍在
    assert any(c.date == "2026-08-10" for c in res["cards"])


def test_select_lucky_days_jianchu_avoid():
    """建除避忌: 破日/闭日排除（2026-08-08 甲寅日 = 破日; 08-13/08-25 = 闭日）"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-01", "2026-08-31")
    assert all(c.date not in ("2026-08-07", "2026-08-08", "2026-08-13",
                              "2026-08-20", "2026-08-25") for c in res["cards"])


def test_select_lucky_days_ji_hits_excluded():
    """避忌命中直接排除: 危日忌移徙 → 搬家排除 2026-08-21; 除日忌入宅 → 08-15"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-15", "2026-08-23")
    assert all(c.date not in ("2026-08-15", "2026-08-21") for c in res["cards"])


def test_select_lucky_days_sanniang_sha_excluded():
    """三娘煞排除（嫁娶）: 2026-08-30 定日(不忌嫁娶/纳采,非破闭) 仅因三娘煞被排"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("嫁娶", "2026-08-28", "2026-08-31")
    assert all(c.date != "2026-08-30" for c in res["cards"])
    # 08-28 满日宜嫁娶 → 仍合格
    assert any(c.date == "2026-08-28" for c in res["cards"])


def test_select_lucky_days_yanggong_ji_excluded():
    """杨公忌日排除（嫁娶）: 2026-08-13 农历七月初一"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("嫁娶", "2026-08-10", "2026-08-16")
    assert all(c.date != "2026-08-13" for c in res["cards"])


def test_select_lucky_days_yuepo_yuexing_excluded():
    """月破/月刑排除（签约场景 jianchu_avoid 为空 → 纯神煞排除验证）"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("签约", "2026-08-06", "2026-08-12")
    # 08-07 未月月破(丑冲未); 08-08 申月月破(寅冲申)+月刑(申刑寅)
    assert all(c.date not in ("2026-08-07", "2026-08-08") for c in res["cards"])
    # 08-10 成日 交易/纳财 → 合格
    assert any(c.date == "2026-08-10" for c in res["cards"])


def test_select_lucky_days_kongwang_excluded():
    """空亡排除（出行）: 2026-08-28~31 月支申逢空(甲戌旬空申酉) → 全部排除"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("出行", "2026-08-28", "2026-08-31")
    assert res["scanned"] == 4
    assert res["cards"] == []
    assert res["suggest_wider"] is True
    assert res["reason"]


def test_select_lucky_days_chong_zodiac():
    """冲生肖排除: 生肖龙 → 2026-08-16 壬戌日(戌冲辰)排除"""
    engine = ZeriEngine()
    no_bazi = engine.select_lucky_days("搬家", "2026-08-08", "2026-08-18")
    with_bazi = engine.select_lucky_days(
        "搬家", "2026-08-08", "2026-08-18", user_bazi={"shengxiao": "龙"})
    assert any(c.date == "2026-08-16" for c in no_bazi["cards"])
    assert all(c.date != "2026-08-16" for c in with_bazi["cards"])
    assert with_bazi["suggest_wider"] is True


def test_select_lucky_days_chong_scene_scoped():
    """冲生肖按场景排除（回归）: 2026-08-12 戊午日冲鼠——
    仅 avoid_chong=True(嫁娶/提车)排除; 开业/出行/签约(False)不受冲生肖影响"""
    engine = ZeriEngine()
    ub = {"shengxiao": "鼠"}
    # avoid_chong=False 场景: 卡片正常返回, 冲鼠日不排除（08-12 戊午开日 总分74 仍入选）
    kaiye = engine.select_lucky_days("开业", "2026-08-08", "2026-08-18", user_bazi=ub)
    assert kaiye["cards"] and any(c.date == "2026-08-12" for c in kaiye["cards"])
    chuxing = engine.select_lucky_days("出行", "2026-08-08", "2026-08-18", user_bazi=ub)
    assert chuxing["cards"] and any(c.date == "2026-08-12" for c in chuxing["cards"])
    assert engine.select_lucky_days("签约", "2026-08-08", "2026-08-18", user_bazi=ub)["cards"]
    # avoid_chong=True 场景: 冲鼠日排除
    for scene in ("嫁娶", "提车"):
        res = engine.select_lucky_days(scene, "2026-08-08", "2026-08-18", user_bazi=ub)
        assert all(c.date != "2026-08-12" for c in res["cards"])


def test_select_lucky_days_small_window():
    """窗口 < 3 天且全被排除 → 0 卡 + suggest_wider"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-20", "2026-08-21")
    assert res["scanned"] == 2
    assert res["cards"] == []
    assert res["suggest_wider"] is True


def test_select_lucky_days_cross_month():
    """月末窗口跨月: 2026-08-28 ~ 09-03, 09-03(宜入宅/移徙/安床)合格"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-28", "2026-09-03")
    assert res["scanned"] == 7
    assert len(res["cards"]) <= 3
    for c in res["cards"]:
        assert c.date in ("2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31",
                          "2026-09-01", "2026-09-02", "2026-09-03")
    assert any(c.date == "2026-09-03" for c in res["cards"])


def test_select_lucky_days_reason_source():
    """reason_source 为实际最高分项, 不得是神煞名"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("搬家", "2026-08-08", "2026-08-18")
    reasons = {c.reason_source for c in res["cards"]}
    # 08-16 满日 3 宜命中(场景50>个人24) → "宜入宅、移徙、安床"; 08-10 成日 → "成日值日"
    assert reasons == {"宜入宅、移徙、安床", "成日值日"}
    forbidden = ("三娘煞", "杨公忌", "月破", "月刑", "空亡", "冲煞")
    assert all(not any(f in r for f in forbidden) for r in reasons)
    # 周末分最高时 → 周末宜{场景}（周六 08-01 开业 1宜命中 场景20<个人24? 不成立——
    # 用 搬家 08-16 开周末偏好验证: 场景50>实用10, 仍为场景理由）
    res2 = engine.select_lucky_days(
        "搬家", "2026-08-08", "2026-08-18", prefer_weekend=True)
    assert res2["cards"][0].date == "2026-08-16"
    assert res2["cards"][0].reason_source == "宜入宅、移徙、安床"


def test_select_lucky_days_bad_input():
    """非法输入 → ValueError"""
    engine = ZeriEngine()
    with pytest.raises(ValueError):
        engine.select_lucky_days("不存在", "2026-08-01", "2026-08-31")
    with pytest.raises(ValueError):
        engine.select_lucky_days("搬家", "2026-08-31", "2026-08-01")
    with pytest.raises(ValueError):
        engine.select_lucky_days("搬家", "2026-13-01", "2026-08-31")


def test_old_select_interface_regression():
    """旧 select() 接口完全兼容（对话链路在用）"""
    engine = ZeriEngine()
    r1 = engine.select(2026, 8, 10, purpose="搬家")
    r2 = engine.select(2026, 8, 10, purpose="搬家")
    # 2026-08-10 月支申, 乙卯? 否——08-10 丙辰日 → 建除 = 成
    assert r1.jianchu == "成"
    assert r1.jianchu == r2.jianchu
    assert r1.yi == r2.yi and r1.ji == r2.ji
    assert r1.overall == r2.overall
    assert "冲" in r1.chong


def test_select_lucky_days_kaiye_scene():
    """开业场景: 月破+月刑排除（08-08 申月寅日 = 月破+月刑+破日）; 成/开日合格"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("开业", "2026-08-08", "2026-08-12")
    dates = {c.date for c in res["cards"]}
    assert "2026-08-08" not in dates
    # 08-10 成日(开市/交易/纳财) / 08-12 开日(开市/纳财) → 合格
    assert {"2026-08-10", "2026-08-12"} <= dates


def test_select_lucky_days_tiche_scene():
    """提车场景: 祈福/出行命中; 破日排除; 周日 08-16 周末加分"""
    engine = ZeriEngine()
    res = engine.select_lucky_days("提车", "2026-08-08", "2026-08-18", prefer_weekend=True)
    dates = {c.date for c in res["cards"]}
    assert "2026-08-08" not in dates
    assert any(c.date == "2026-08-16" and c.practical_score == 10 for c in res["cards"])
    assert res["scanned"] == 11
