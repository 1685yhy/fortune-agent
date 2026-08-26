"""六壬排盘引擎测试（src/engines/liuren.py）。

手算基准命例：1990-08-16 14:30 北京（庚午年甲申月癸丑日己未时）
  - 日柱癸丑属甲辰旬 → 旬空寅卯（甲辰乙巳丙午丁未戊申己酉庚戌辛亥壬子癸丑）
  - 中气定将：1990-08-16 处于大暑(7/23)~处暑(8/23)之间 → 月将午
  - 月将加时：午加未（14:30 属未时）
  - 天地盘：地盘未上神=午、地盘子上神=亥、地盘丑上神=子、地盘亥上神=戌
  - 四课：第一课 癸/子（干癸寄丑，丑上神子）；第二课 子/亥；第三课 丑/子；第四课 子/亥
  - 克情：第三课 下贼上（丑土克子水），唯一克 → 重审课（贼克宗门）
  - 三传：初传=子上神子，中传=地盘子位天盘神亥，末传=地盘亥位天盘神戌 → [子,亥,戌]
  - 贵人：癸日 未时(昼) → 旦贵巳（前字昼贵说，壬癸兔蛇藏），天盘巳加地盘午(丁午)，午阳支顺行
"""
from lunar_python import Solar

from src.engines.liuren import LiurenEngine, LiurenResult, DIZHI, TIANGAN


def _chart():
    return LiurenEngine().calculate(1990, 8, 16, 14, 30)


# ============================================================
# 基础结构与类型
# ============================================================

def test_liuren_basic_structure():
    result = _chart()
    assert isinstance(result, LiurenResult)
    assert result.year_gan == "庚"
    assert result.year_zhi == "午"
    assert result.month_gan == "甲"
    assert result.month_zhi == "申"
    assert result.day_gan == "癸"
    assert result.day_zhi == "丑"
    assert result.hour_zhi == "未"


def test_liuren_field_types():
    result = _chart()
    assert isinstance(result.yuejiang, str)
    assert isinstance(result.tianpan, dict)
    assert isinstance(result.sipan, list) and len(result.sipan) == 4
    assert isinstance(result.sanchuan, list) and len(result.sanchuan) == 3
    assert isinstance(result.guiren, str)
    assert isinstance(result.xunkong, list)
    assert isinstance(result.raw_data, dict)


def test_liuren_tianpan_covers_all_branches():
    """天盘十二宫齐全且将无重复（月将加时顺布一圈）."""
    result = _chart()
    assert set(result.tianpan.keys()) == set(DIZHI)
    assert set(result.tianpan.values()) == set(DIZHI)


def test_liuren_sipan_format():
    """四课每课为 {"位置": "干支"} 两字段, 干支二字."""
    result = _chart()
    for ke in result.sipan:
        assert set(ke.keys()) == {"位置", "干支"}
        assert len(ke["干支"]) == 2


# ============================================================
# 手算已知值（1990-08-16 14:30 北京）
# ============================================================

def test_known_yuejiang():
    """中气定将: 大暑(7/23)后处暑(8/23)前 → 午将."""
    result = _chart()
    assert result.yuejiang == "午"


def test_known_tianpan():
    """月将加时: 午加未, 顺布十二宫.

    地盘 -> 天盘: 子->亥 丑->子 寅->丑 卯->寅 辰->卯 巳->辰 午->巳
                  未->午 申->未 酉->申 戌->酉 亥->戌
    """
    result = _chart()
    assert result.tianpan["未"] == "午"  # 月将落占时
    assert result.tianpan["子"] == "亥"
    assert result.tianpan["丑"] == "子"
    assert result.tianpan["亥"] == "戌"
    assert result.tianpan["午"] == "巳"
    assert result.tianpan["巳"] == "辰"


def test_known_sipan():
    """四课: 干癸寄丑; 课1=癸/子 课2=子/亥 课3=丑/子 课4=子/亥.

    干支格式 = 上神+下神（天盘神在前）.
    """
    result = _chart()
    assert result.sipan[0]["干支"] == "子癸"  # 第一课 干上神
    assert result.sipan[1]["干支"] == "亥子"
    assert result.sipan[2]["干支"] == "子丑"  # 第三课 支上神
    assert result.sipan[3]["干支"] == "亥子"


def test_known_sanchuan():
    """三传: 第三课下贼上(丑土克子水)唯一克 → 重审课; 发用取上神子.

    中传 = 地盘子位天盘神(亥), 末传 = 地盘亥位天盘神(戌) → [子,亥,戌].
    """
    result = _chart()
    assert result.sanchuan == ["子", "亥", "戌"]
    # 三传追踪口径: 中传 = 初传地支位上的天盘神
    assert result.sanchuan[1] == result.tianpan[result.sanchuan[0]]
    assert result.sanchuan[2] == result.tianpan[result.sanchuan[1]]


def test_known_zongmen():
    """宗门与课名: 唯一克为下贼上 → 贼克/重审课."""
    result = _chart()
    assert result.raw_data["宗门"] == "贼克"
    assert result.raw_data["课名"] == "重审课"
    # 克情: 仅第三课下贼上
    ke_qing = {k["课"]: k["克"] for k in result.raw_data["四课克情"]}
    assert ke_qing[1] == "无克"
    assert ke_qing[3] == "下贼上"


def test_known_xunkong():
    """癸丑日属甲辰旬 → 旬空寅卯."""
    result = _chart()
    assert result.xunkong == ["寅", "卯"]
    # 与 lunar-python 交叉验证
    lunar = Solar.fromYmdHms(1990, 8, 16, 14, 30, 0).getLunar()
    assert sorted(result.xunkong) == sorted(lunar.getDayXunKong())


def test_known_guiren():
    """贵人: 癸日未时(昼)旦贵巳; 天盘巳加地盘午; 午阳支 → 顺行.

    口诀: 甲戊庚牛羊, 乙己鼠猴乡, 丙丁猪鸡位, 壬癸兔蛇藏, 六辛逢马虎（前字昼贵说）.
    """
    result = _chart()
    assert result.guiren == "丁午"          # 贵人所在宫位干支（地盘午宫，午宫地盘干丁）
    assert result.raw_data["guiren_shen"] == "巳"
    assert result.raw_data["guiren_day_night"] == "昼贵"
    assert result.raw_data["guiren_direction"] == "顺行"
    # 十二天将: 贵人在午宫顺布（螣蛇在未…天后在巳）
    tianjiang = result.raw_data["天将"]
    assert tianjiang["午"] == "贵人"
    assert tianjiang["未"] == "螣蛇"
    assert tianjiang["巳"] == "天后"


# ============================================================
# 确定性
# ============================================================

def test_liuren_deterministic():
    engine = LiurenEngine()
    r1 = engine.calculate(1990, 8, 16, 14, 30)
    r2 = engine.calculate(1990, 8, 16, 14, 30)
    assert r1 == r2
    assert r1.tianpan == r2.tianpan
    assert r1.sipan == r2.sipan
    assert r1.sanchuan == r2.sanchuan


# ============================================================
# 月将边界（中气定将，以中气日换将）
# ============================================================

def test_yuejiang_boundary():
    engine = LiurenEngine()
    # 2024-02-19 雨水 → 亥将；雨水后 2024-03-01 仍亥将
    assert engine.calculate(2024, 2, 19, 12, 0).yuejiang == "亥"
    assert engine.calculate(2024, 3, 1, 12, 0).yuejiang == "亥"
    # 2024-03-20 春分 → 戌将
    assert engine.calculate(2024, 3, 20, 12, 0).yuejiang == "戌"
    # 2024-08-16 大暑后 → 午将
    assert engine.calculate(2024, 8, 16, 12, 0).yuejiang == "午"
    # 1990-08-22 处暑前一日 → 午将; 1990-08-23 处暑当日 → 巳将
    assert engine.calculate(1990, 8, 22, 12, 0).yuejiang == "午"
    assert engine.calculate(1990, 8, 23, 12, 0).yuejiang == "巳"
    # 2024-12-21 冬至 → 丑将; 2025-01-20 大寒 → 子将
    assert engine.calculate(2024, 12, 21, 12, 0).yuejiang == "丑"
    assert engine.calculate(2025, 1, 20, 12, 0).yuejiang == "子"


# ============================================================
# 时辰边界
# ============================================================

def test_hour_boundary():
    engine = LiurenEngine()
    # 14:30 未时; 13:00 未时起始; 12:59 午时
    assert engine.calculate(1990, 8, 16, 14, 30).hour_zhi == "未"
    assert engine.calculate(1990, 8, 16, 13, 0).hour_zhi == "未"
    assert engine.calculate(1990, 8, 16, 12, 59).hour_zhi == "午"
    # 23:30 子时
    assert engine.calculate(1990, 8, 16, 23, 30).hour_zhi == "子"


# ============================================================
# 九宗门已知值（手算核实）
# ============================================================

def test_known_bazhuan():
    """八专：己未日（己寄未=日支未，干支同宫）丑将子时，四课无克.

    阴日取第四课上神酉逆数三位（含本位：酉申未）得未，中末传=干上神申.
    """
    result = LiurenEngine().calculate(2023, 1, 1, 0, 30)
    assert result.raw_data["宗门"] == "八专"
    assert result.raw_data["课名"] == "八专课"
    assert result.sanchuan == ["未", "申", "申"]


def test_known_bieze():
    """别责：辛未日丑将辰时，干上神未==日支未（课2=课3实三课）.

    阴日取支三合前一位（未→亥）上神=申，中末传=干上神未.
    """
    result = LiurenEngine().calculate(2023, 1, 13, 8, 30)
    assert result.raw_data["宗门"] == "别责"
    assert result.raw_data["课名"] == "别责课"
    assert result.sanchuan == ["申", "未", "未"]


def test_known_fuyin_ziren():
    """伏吟自任：庚申日丑将丑时（干支同宫+伏吟），无克阳日取干上神.

    初传申，中传=申刑寅，末传=寅刑巳.
    """
    result = LiurenEngine().calculate(2023, 1, 2, 1, 30)
    assert result.raw_data["盘型"] == "伏吟"
    assert result.raw_data["宗门"] == "伏吟"
    assert result.raw_data["课名"] == "自任"
    assert result.sanchuan == ["申", "寅", "巳"]


def test_known_fanyin_wuyi():
    """返吟无依：庚申日丑将未时（丑未冲），四课互克取克贼.

    下贼上优先，初传=下贼上之上神寅，中末传=天盘递推.
    """
    result = LiurenEngine().calculate(2023, 1, 2, 14, 30)
    assert result.raw_data["盘型"] == "返吟"
    assert result.raw_data["宗门"] == "返吟"
    assert result.raw_data["课名"] == "无依"
    assert result.sanchuan == ["寅", "申", "寅"]


def test_known_jinglan():
    """返吟井栏格：己未日丑将未时（六日无克之一）.

    初传=支上驿马（未→巳），中传=支上神丑，末传=干上神丑.
    """
    result = LiurenEngine().calculate(2023, 1, 1, 14, 30)
    assert result.raw_data["宗门"] == "返吟"
    assert result.raw_data["课名"] == "井栏格"
    assert result.sanchuan == ["巳", "丑", "丑"]


def test_liuren_other_dates():
    """多日期不崩溃且结构合法."""
    engine = LiurenEngine()
    for year, month, day, hour in [
        (2024, 7, 11, 13), (2023, 1, 15, 10), (2025, 6, 21, 8),
        (1995, 12, 22, 0), (2000, 2, 29, 23),
    ]:
        result = engine.calculate(year, month, day, hour)
        assert len(result.sanchuan) == 3
        assert len(result.sipan) == 4
        assert len(result.xunkong) == 2
        for zhi in result.sanchuan:
            assert zhi in DIZHI
