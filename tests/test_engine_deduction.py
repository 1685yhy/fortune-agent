# tests/test_engine_deduction.py
"""推演链多体系分支测试（阶段3 Task 6）：deduce 的 system 参数按体系路由。

约束（计划 Task 6）：
- 默认 system="bazi" 行为与阶段2 完全一致（回归钉死 7 段步骤序列）
- 紫微/六爻/奇门/六壬各 2 条链构建测试（真实引擎输出）+ 1 条"未覆盖明示"测试
- 非八字体系不加证据检索（evidence.py 八字专属），coverage 明示"未举证"
- 未覆盖/降级处 add_coverage("未覆盖", ...)，绝不装懂
"""
import pytest

from src.engine.deduction import deduce
from src.engine.rules.liuyao import YAO_TERMS, liuqin_of
from src.engines.liuren import LiurenEngine
from src.engines.liuyao import LiuyaoEngine
from src.engines.qimen import QimenEngine
from src.engines.ziwei import ZiweiEngine
from lunar_python import Solar


# ---------- 工具 ----------

def _pills_of(year, month, day, hour):
    """公历 → 四柱（与排盘引擎同一 lunar-python 口径）。"""
    bazi = Solar.fromYmdHms(year, month, day, hour, 0, 0).getLunar().getEightChar()
    return [bazi.getYear(), bazi.getMonth(), bazi.getDay(), bazi.getTime()]


def _fake_bazi_engine_result():
    from types import SimpleNamespace
    return SimpleNamespace(
        bazi=["庚午", "辛巳", "乙酉", "甲申"], day_master="乙木",
        wuxing={"金": 2, "木": 2, "火": 2, "土": 1, "水": 1},
        shishen=["正官", "伤官", "日主", "劫财"],
        dayun=[(4, "壬午"), (14, "癸未"), (24, "甲申")],
        liunian={"2026": "丙午", "2027": "丁未"},
        geju="伤官格", yongshen="水木", shensha=["天乙贵人", "驿马"],
        nayin=["路旁土", "白蜡金", "泉中水", "井泉水"], gender="男",
        raw_data={},
    )


# ---------- bazi 默认行为回归（阶段2 完全一致） ----------

def test_deduce_bazi_default_exact_seven_steps():
    """默认 system="bazi"：7 段步骤序列与阶段2 完全一致（回归钉死）。"""
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], engine_result=_fake_bazi_engine_result(),
                   question="今年财运如何？")
    rules = [s.rule for s in chain.steps]
    assert rules == [
        "排盘引擎.calculate",
        "shishen.detect_combos",
        "geju.determine_geju",
        "qiongtong_table[乙][巳]",
        "shensha.shensha_of",
        "大运流年.engine",
        "断语要点.compose",
    ]
    assert chain.steps[-1].output  # 断语要点非空
    # 八字体系不新增"未举证"标注（证据检索是八字专属，正常走）
    assert "未举证" not in chain.coverage


def test_deduce_bazi_pills_only_coverage_unchanged():
    """pills-only 路径：未覆盖标注（大运流年）保持阶段2 行为。"""
    chain = deduce(["辛未", "乙未", "庚辰", "丁亥"], question="")
    assert chain.coverage.get("未覆盖"), "pills-only 必须明示未覆盖(大运流年等)"
    assert "排盘引擎.calculate" in [s.rule for s in chain.steps]
    assert "断语要点.compose" == chain.steps[-1].rule


# ---------- 紫微（ziwei） ----------

ZIWEI_RULES = ["ziwei.排盘.calculate", "ziwei.wuxing_ju_of", "ziwei.sihua_of",
               "ziwei.palace_order", "ziwei.断语要点.compose"]


def test_deduce_ziwei_chain_from_engine():
    """真实 ZiweiEngine 输出构建链：排盘→五行局→四化→命宫/十二宫→断语要点。"""
    result = ZiweiEngine().calculate(1990, 5, 20, 16, 30, "北京", "女")
    pills = _pills_of(1990, 5, 20, 16)
    chain = deduce(pills, engine_result=result, question="今年运势", system="ziwei")
    rules = [s.rule for s in chain.steps]
    assert rules == ZIWEI_RULES
    # 排盘事实步骤
    assert "命宫酉" in chain.steps[0].output
    # 五行局步骤（水二局：命宫乙酉泉中水）
    assert "水二局" in chain.steps[1].output
    # 四化步骤（庚年：阳武阴同）
    assert "太阳化禄" in chain.steps[2].output
    # 命宫/十二宫步骤
    assert "命宫酉" in chain.steps[3].output
    # 断语要点=规则要点组装，且含五行局/四化
    assert "五行局：水二局" in chain.steps[4].output
    assert "太阳化禄" in chain.steps[4].output
    # 非八字体系不举证
    assert chain.coverage.get("未举证")


def test_deduce_ziwei_chain_second_case():
    """第二链：不同生日 → 命宫/四化随输入变化（戊年贪阴弼机）。"""
    result = ZiweiEngine().calculate(1988, 8, 8, 10, 0, "北京", "男")
    pills = _pills_of(1988, 8, 8, 10)
    chain = deduce(pills, engine_result=result, question="", system="ziwei")
    assert [s.rule for s in chain.steps] == ZIWEI_RULES
    assert "命宫寅" in chain.steps[0].output
    assert "贪狼化禄" in chain.steps[2].output
    assert chain.steps[3].output.startswith("命宫寅")


def test_deduce_ziwei_without_engine_marks_uncovered():
    """未提供排盘结果：走 add_coverage("未覆盖", ...)，不装懂不造假。"""
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], system="ziwei", question="")
    assert chain.coverage.get("未覆盖"), "未提供排盘结果必须明示未覆盖"
    assert chain.coverage.get("未举证")
    assert chain.steps == [], "无排盘结果不得伪造任何步骤"


# ---------- 六爻（liuyao） ----------

LIUYAO_RULES = ["liuyao.起卦.cast", "liuyao.liuqin_of", "liuyao.shiying_positions",
                "liuyao.bian_hexagram", "liuyao.断语要点.compose"]


def test_deduce_liuyao_chain_from_engine():
    """真实 LiuyaoEngine 输出（seed=42 固定）构建链：起卦→六亲→世应→动变→断语要点。
    K1 位序修复：seed=42 同爻象本卦由雷火丰更正为山火贲。"""
    result = LiuyaoEngine().cast(method="random", question="财运如何", seed=42)
    pills = ["庚午", "辛巳", "乙酉", "甲申"]  # 日干乙为"我"
    chain = deduce(pills, engine_result=result, question="财运如何", system="liuyao")
    rules = [s.rule for s in chain.steps]
    assert rules == LIUYAO_RULES
    # 起卦步骤：注明固定 seed 可复现
    assert "山火贲" in chain.steps[0].output
    assert "固定 seed" in chain.steps[0].fact
    # 六亲步骤：以日干乙为"我"，世爻地支判六亲（与规则库同口径交叉验证）
    expected_lq = liuqin_of(pills[2][0], result.lines[result.shi_yao]["dizhi"])
    assert f"世爻六亲：{expected_lq}" in chain.steps[1].output
    # 世应步骤
    assert f"世在{YAO_TERMS[result.shi_yao]}" in chain.steps[2].output
    assert f"应在{YAO_TERMS[result.ying_yao]}" in chain.steps[2].output
    # 动变步骤
    assert result.changed_hexagram in chain.steps[3].output
    # 断语要点=规则要点组装
    assert "本卦：山火贲" in chain.steps[4].output
    assert chain.coverage.get("未举证")


def test_deduce_liuyao_chain_second_case():
    """第二链：seed=7 另一卦（天山遁），动爻/变卦随起卦结果变化。
    K1 位序修复：seed=7 同爻象（少阴/老阴/少阳×4）本卦由天雷无妄更正为天山遁。"""
    result = LiuyaoEngine().cast(method="random", question="事业", seed=7)
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], engine_result=result, question="事业", system="liuyao")
    assert [s.rule for s in chain.steps] == LIUYAO_RULES
    assert "天山遁" in chain.steps[0].output
    assert "天风姤" in chain.steps[3].output


def test_deduce_liuyao_without_engine_marks_uncovered():
    """未提供起卦结果：明示未覆盖，不装懂。"""
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], system="liuyao", question="")
    assert chain.coverage.get("未覆盖")
    assert chain.coverage.get("未举证")
    assert chain.steps == []


# ---------- 奇门（qimen） ----------

QIMEN_RULES = ["qimen.排盘.calculate", "qimen.men_attribute", "qimen.star_attribute",
               "qimen.值符值使", "qimen.断语要点.compose"]


def test_deduce_qimen_chain_from_engine():
    """真实 QimenEngine 输出构建链：排盘(遁局)→八门→九星→值符值使→断语要点。"""
    result = QimenEngine().calculate(2024, 7, 11, 13, 30)
    pills = _pills_of(2024, 7, 11, 13)
    chain = deduce(pills, engine_result=result, question="出行", system="qimen")
    rules = [s.rule for s in chain.steps]
    assert rules == QIMEN_RULES
    # 排盘步骤：遁局/局数
    assert "阴遁2局" in chain.steps[0].output
    # 八门步骤：八门五行属性查表
    assert "生属土" in chain.steps[1].output
    # 九星步骤：九星五行属性查表
    assert "天任属土" in chain.steps[2].output
    # 值符值使步骤
    assert "值符星 天任" in chain.steps[3].output
    assert "值使门 生" in chain.steps[3].output
    # 断语要点=规则要点组装
    assert "遁局：阴遁2局" in chain.steps[4].output
    assert chain.coverage.get("未举证")


def test_deduce_qimen_chain_second_case():
    """第二链：冬季排盘 → 阳遁，与夏至后阴遁互斥。"""
    result = QimenEngine().calculate(2024, 1, 5, 9, 0)
    pills = _pills_of(2024, 1, 5, 9)
    chain = deduce(pills, engine_result=result, question="", system="qimen")
    assert [s.rule for s in chain.steps] == QIMEN_RULES
    assert "阳遁4局" in chain.steps[0].output
    assert "值符星 天英" in chain.steps[3].output
    assert "遁局：阳遁4局" in chain.steps[4].output


def test_deduce_qimen_without_engine_marks_uncovered():
    """未提供排盘结果：明示未覆盖，不装懂。"""
    chain = deduce(["甲辰", "辛未", "丙子", "乙未"], system="qimen", question="")
    assert chain.coverage.get("未覆盖")
    assert chain.coverage.get("未举证")
    assert chain.steps == []


# ---------- 六壬（liuren） ----------

LIUREN_RULES = ["liuren.排盘.calculate", "liuren.sipan", "liuren.classify_zongmen",
                "liuren.kongwang", "liuren.断语要点.compose"]


def test_deduce_liuren_chain_from_engine():
    """真实 LiurenEngine 输出构建链：排盘(月将/天地盘)→四课→三传(宗门)→旬空/贵人→断语要点。"""
    result = LiurenEngine().calculate(1990, 8, 16, 14, 30)
    pills = _pills_of(1990, 8, 16, 14)
    chain = deduce(pills, engine_result=result, question="问事", system="liuren")
    rules = [s.rule for s in chain.steps]
    assert rules == LIUREN_RULES
    # 排盘步骤：月将/天地盘
    assert "月将午" in chain.steps[0].output
    assert "天地盘" in chain.steps[0].output
    # 四课步骤
    assert "第一课(干上)" in chain.steps[1].output
    # 三传（宗门）步骤：贼克课
    assert "三传 ['子', '亥', '戌']" in chain.steps[2].output
    assert "贼克" in chain.steps[2].output
    # 旬空/贵人步骤
    assert "旬空 ['寅', '卯']" in chain.steps[3].output
    assert "贵人" in chain.steps[3].output
    # 断语要点=规则要点组装
    assert "三传['子', '亥', '戌']" in chain.steps[4].output
    assert chain.coverage.get("未举证")


def test_deduce_liuren_chain_degrade_marks_uncovered():
    """涉害课排盘降级（孟仲季简便法）：如实记入 coverage 未覆盖，绝不装懂。"""
    result = LiurenEngine().calculate(2023, 1, 1, 9, 30)
    assert result.raw_data.get("降级"), "该命例应触发涉害降级（测试前提）"
    pills = _pills_of(2023, 1, 1, 9)
    chain = deduce(pills, engine_result=result, question="", system="liuren")
    assert [s.rule for s in chain.steps] == LIUREN_RULES
    assert "涉害" in chain.steps[2].output
    uncovered = "；".join(chain.coverage.get("未覆盖", []))
    assert "六壬排盘降级" in uncovered, "排盘降级必须明示未覆盖"
    assert chain.coverage.get("未举证")


def test_deduce_liuren_without_engine_marks_uncovered():
    """未提供排盘结果：明示未覆盖，不装懂。"""
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], system="liuren", question="")
    assert chain.coverage.get("未覆盖")
    assert chain.coverage.get("未举证")
    assert chain.steps == []


# ---------- 未知体系 ----------

def test_deduce_unknown_system_raises():
    with pytest.raises(ValueError):
        deduce(["庚午", "辛巳", "乙酉", "甲申"], system="nobody_knows")
