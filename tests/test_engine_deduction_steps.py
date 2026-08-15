from src.engine.deduction import deduce

def _fake_engine_result():
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

def test_deduce_with_engine_result_steps_complete():
    chain = deduce(["庚午", "辛巳", "乙酉", "甲申"], engine_result=_fake_engine_result(),
                   question="今年财运如何？")
    rules = [s.rule for s in chain.steps]
    assert "geju.determine_geju" in rules
    assert "shishen.detect_combos" in rules
    assert "shensha.shensha_of" in rules
    assert any("大运" in s.rule for s in chain.steps)
    assert chain.steps[-1].rule.startswith("断语要点")
    assert len(chain.steps) >= 6

def test_deduce_pills_only_marks_coverage():
    chain = deduce(["辛未", "乙未", "庚辰", "丁亥"], question="")
    assert any(s.rule == "geju.determine_geju" for s in chain.steps)
    assert chain.coverage.get("未覆盖"), "pills-only 必须明示未覆盖(大运流年等)"
    assert not any("大运" in s.rule for s in chain.steps)

def test_deduce_invalid_pills_raises():
    import pytest
    with pytest.raises(ValueError):
        deduce(["甲子", "乙丑", "丙寅"])
