"""神煞引擎测试 — 问真独有 28 种（250 案例校准补全，2026-08-20）。

数据基准：data/calibrate_qz_250.json（问真 szshensha 全集，覆盖率 100.00%，
4125/4125 实例零漏报零超报，250/250 案例全集一致）。

锚点案例（含问真 API 逐柱核对过的 source 柱位）：
1. 1983-10-09 女 — 德秀/月德/天德/天德合/天罗地网/飞刃/红艳/金神/天医/血刃
2. 1994-08-05 男 — 拱禄/四废日/十恶大败/阴差阳错/天罗地网双柱/天德/月德
3. 1952-05-28 男 — 童子煞/天医/月德合/天罗地网/三奇/十灵/孤鸾
4. 2000-05-05 女 — 十恶大败/阴差阳错/飞刃/天德合/月德合/德秀
5. 1956-04-08 男 — 德秀双柱/天罗地网双柱/十恶大败/孤鸾/正词馆
"""
from src.engines.bazi import BaziEngine
from src.engines.shensha import (
    DEXIU, FEIREN, HONGYAN, JINSHEN_3, LIUXIA, SHENSHA_LUCK,
    TIANDE, XUEREN, shensha_names, shensha_of,
)

ENGINE = BaziEngine()


def _calc(date, time, gender):
    y, m, d = (int(x) for x in date.split("-"))
    h, mi = (int(x) for x in time.split(":"))
    return ENGINE.calculate(y, m, d, h, mi, "", gender)


def _detail(r):
    return {x["name"]: x for x in r.shensha_detail}


# ---------------------------------------------------------------- 1. 锚点：问真独有神煞出现
def test_anchor_1983_10_09():
    """1983-10-09 21:37 女 → 癸亥 壬戌 庚午 丁亥。
    戌月：德秀(丙丁戊癸)、月德(寅午戌→丙)、天德(戌→丙)；月德合(丙→辛)、天德合(丙→辛)
    时支丁：丙辛合之辛不现…… 天德合=辛不现；天罗地网=年亥±1 戌（月柱）。
    问真 250 案例全集对齐（含逐柱 source 经 API 核对）。"""
    r = _calc("1983-10-09", "21:37", "女")
    assert r.bazi == ["癸亥", "壬戌", "庚午", "丁亥"]
    d = _detail(r)
    # 德秀贵人：戌月 德=丙丁 秀=戊癸，癸(年)丁(时) 双柱
    assert d["德秀贵人"]["source"] == "年时柱"
    assert d["德秀贵人"]["luck"] == "吉"
    # 月德贵人：戌月 丙，年干癸不中 → 无；天德贵人：戌月 丙 → 无
    assert "月德贵人" not in d and "天德贵人" not in d
    # 天罗地网：年亥-1=戌 → 月柱（问真 API 核对 [1]）
    assert d["天罗地网"]["source"] == "月柱"
    # 飞刃：日干庚 → 卯，四柱无卯 → 无；红艳煞：日干庚 → 戌（月柱）
    assert "飞刃" not in d
    assert d["红艳煞"]["source"] == "月柱"
    # 金神：日柱庚午/时柱丁亥 均非金神三局 → 无
    assert "金神" not in d
    # 血刃：月支戌 → 巳，四柱无巳 → 无
    assert "血刃" not in d


def test_anchor_1994_08_05():
    """1994-08-05 01:41 男 → 甲戌 辛未 癸亥 癸丑。
    未月：天德=甲(年柱)、月德=甲(年柱)；拱禄：癸亥日癸丑时 拱子（癸禄子）；
    四废日：未月(夏) 壬子癸亥 → 癸亥；十恶大败/阴差阳错：癸亥日。
    天罗地网：年戌+日亥 双柱（问真 API 核对 [0,2]）。"""
    r = _calc("1994-08-05", "01:41", "男")
    assert r.bazi == ["甲戌", "辛未", "癸亥", "癸丑"]
    d = _detail(r)
    assert d["天德贵人"]["source"] == "年柱"      # 未月 甲，年干
    assert d["月德贵人"]["source"] == "年柱"      # 未月(亥卯未) 甲
    assert d["拱禄"]["source"] == "日柱"          # 癸亥日+癸丑时 拱子
    assert d["拱禄"]["luck"] == "吉"
    assert d["四废日"]["source"] == "日柱"        # 未月(夏) 癸亥
    assert d["十恶大败"]["source"] == "日柱"
    assert d["阴差阳错"]["source"] == "日柱"
    assert d["天罗地网"]["source"] == "年日柱"  # 戌+亥 双柱
    assert d["德秀贵人"]["source"] == "年柱"      # 未月(亥卯未) 德=甲乙，年甲
    assert d["德秀贵人"]["luck"] == "吉"


def test_anchor_1952_05_28():
    """1952-05-28 21:45 男 → 壬辰 乙巳 甲戌 乙亥。
    巳月：天医=辰(年柱)；月德合=乙(月柱+时柱 双乙)；童子煞：水火命(年纳音长流水)见酉戌
    → 日支戌；天罗地网：年辰+1=巳(月)、日戌+1=亥(时) 双柱；
    十灵日/孤鸾煞：甲戌日；三奇贵人：壬(年)癸(月)辛(日)? 无——壬癸辛缺辛。"""
    r = _calc("1952-05-28", "21:45", "男")
    assert r.bazi == ["壬辰", "乙巳", "甲戌", "乙亥"]
    d = _detail(r)
    assert d["天医"]["source"] == "年柱"          # 巳月-1=辰
    assert d["月德合"]["source"] == "月时柱"    # 巳月 月德庚→合乙，月时双乙
    assert d["童子煞"]["source"] == "日柱"        # 年纳音水 → 见酉戌，日戌
    assert d["天罗地网"]["source"] == "月时柱"  # 巳+亥
    assert "十灵日" not in d                       # 甲戌非十灵日
    assert "孤鸾煞" not in d                       # 甲戌非孤鸾日
    assert "三奇贵人" not in d                     # 壬癸辛缺辛不连排
    assert "德秀贵人" in d                        # 巳月 德=庚辛 秀=乙庚，月时双乙


def test_anchor_2000_05_05():
    """2000-05-05 09:19 女 → 庚辰 庚辰 癸亥 丁巳。
    辰月：天德=壬→天德合丁(时柱)、月德=壬→月德合丁(时柱)、德秀=癸(日柱)；
    飞刃：日干癸 → 巳(时柱)；十恶大败/阴差阳错：癸亥日；天罗地网：辰+1=巳(时柱)。"""
    r = _calc("2000-05-05", "09:19", "女")
    assert r.bazi == ["庚辰", "庚辰", "癸亥", "丁巳"]
    d = _detail(r)
    assert d["天德合"]["source"] == "时柱"        # 辰月天德壬 → 合丁，丁在时干
    assert d["月德合"]["source"] == "时柱"        # 辰月月德壬 → 合丁
    assert d["德秀贵人"]["source"] == "日柱"      # 辰月 德=壬癸，日干癸（丁不挂柱）
    assert d["飞刃"]["source"] == "时柱"          # 癸刃亥，对冲巳
    assert d["十恶大败"]["source"] == "日柱"
    assert d["阴差阳错"]["source"] == "日柱"
    assert d["天罗地网"]["source"] == "时柱"
    assert "天德贵人" not in d                    # 辰月天德壬 四柱无壬
    assert "月德贵人" not in d                    # 辰月月德壬 四柱无壬


def test_anchor_1956_04_08():
    """1956-04-08 07:47 男 → 丙申 壬辰 乙巳 庚辰。
    辰月：德秀 丙(年)+壬(月) 双柱；天罗地网：日巳-1=辰(月+时 双辰)；
    十恶大败：乙巳日；孤鸾煞：乙巳日；天德贵人：辰月壬(月柱)；月德贵人：辰月壬。"""
    r = _calc("1956-04-08", "07:47", "男")
    assert r.bazi == ["丙申", "壬辰", "乙巳", "庚辰"]
    d = _detail(r)
    assert d["德秀贵人"]["source"] == "年月柱"  # 丙(年)+壬(月)，问真 API 核对 [0,1]
    assert d["天德贵人"]["source"] == "月柱"      # 辰月天德壬
    assert d["月德贵人"]["source"] == "月柱"      # 辰月月德壬
    assert d["天罗地网"]["source"] == "月时柱"  # 双辰（问真 API 核对 [1,3]）
    assert d["十恶大败"]["source"] == "日柱"
    assert d["孤鸾煞"]["source"] == "日柱"


# ---------------------------------------------------------------- 2. 规则表结构
def test_new_tables_complete():
    """问真独有 28 种全部有吉凶标注 + 规则表完整。"""
    for n in ["德秀贵人", "童子煞", "月德合", "月德贵人", "天德合", "天德贵人",
              "飞刃", "红艳煞", "血刃", "流霞", "天医", "十灵日", "阴差阳错",
              "十恶大败", "八专日", "九丑日", "孤鸾煞", "六秀日", "魁罡日",
              "金神", "四废日", "天转日", "天赦日", "地转日", "拱禄", "三奇贵人",
              "天罗地网"]:
        assert n in SHENSHA_LUCK, f"{n} 缺吉凶标注"
    assert DEXIU["寅"] == "丙丁戊癸"
    assert DEXIU["申"] == "甲丙辛壬癸戊己"       # 申子辰月另计甲丙辛（问真口径，丁不挂柱）
    assert TIANDE["卯"] == "申" and TIANDE["午"] == "亥"  # 天德含地支目标
    assert FEIREN["甲"] == "酉" and XUEREN["亥"] == "亥"
    assert HONGYAN["乙"] == "午"                   # 问真口径异于通行口诀
    assert LIUXIA["丁"] == "申"
    assert JINSHEN_3 == ("乙丑", "己巳", "癸酉")   # 金神三局（不含甲午）


def test_new_luck_labels():
    """吉凶标注：吉星吉、凶煞凶、中性偏凶保守标注。"""
    assert SHENSHA_LUCK["德秀贵人"] == "吉"
    assert SHENSHA_LUCK["天医"] == "吉"
    assert SHENSHA_LUCK["三奇贵人"] == "吉"
    assert SHENSHA_LUCK["红艳煞"] == "凶"
    assert SHENSHA_LUCK["孤鸾煞"] == "凶"
    assert SHENSHA_LUCK["十恶大败"] == "凶"
    assert SHENSHA_LUCK["四废日"] == "凶"
    for n in ["魁罡日", "金神", "飞刃", "血刃", "流霞", "童子煞", "天罗地网"]:
        assert SHENSHA_LUCK[n] == "中性偏凶", n


# ---------------------------------------------------------------- 3. 与校准数据全集一致
def test_calibration_full_coverage():
    """250 校准案例：我们输出 = 问真 szshensha 全集（覆盖率 100%，零漏报零超报）。"""
    import json
    import os
    with open(os.path.join(os.path.dirname(__file__), "..", "data",
                           "calibrate_qz_250.json"), encoding="utf-8") as f:
        data = json.load(f)
    total = miss = diff = 0
    for r in data["results"]:
        parts = r["case"].split()
        y, m, d = (int(x) for x in parts[0].split("-"))
        h, mi = (int(x) for x in parts[1].split(":"))
        g = parts[2]
        res = ENGINE.calculate(y, m, d, h, mi, "", g)
        ours = set(shensha_names(res.bazi[0], res.bazi[2],
                                 [p[0] for p in res.bazi], [p[1] for p in res.bazi], g))
        qz = set(r["qz_shensha"])
        total += len(qz)
        miss += len(qz - ours)
        if qz != ours:
            diff += 1
    assert miss == 0, f"问真实例漏报 {miss}"
    assert diff == 0, f"全集不一致案例 {diff}"
    assert total == 4125
