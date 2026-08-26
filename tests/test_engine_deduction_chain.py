from src.engine.deduction import DeductionStep, DeductionChain

def test_chain_append_and_fields():
    chain = DeductionChain(input={"birth": "test"}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    step = DeductionStep(1, "geju.determine_geju", "月支=巳，藏干=丙庚戊",
                         "建禄格", "子平真诠·八格", "巳本气丙为比肩，不透比劫取本气")
    chain.append(step)
    assert chain.steps[0].rule == "geju.determine_geju"
    assert chain.steps[0].output == "建禄格"
    assert len(chain.steps) == 1

def test_chain_to_text_roundtrip_readable():
    chain = DeductionChain(input={}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    chain.append(DeductionStep(1, "geju.determine_geju", "月支=巳", "建禄格",
                               "子平真诠·八格", "巳本气丙为比肩"))
    chain.add_coverage("未覆盖", "六壬体系(阶段3)")
    text = chain.to_text()
    assert "第1步" in text and "建禄格" in text and "子平真诠·八格" in text
    assert "未覆盖" in text and "六壬体系" in text

def test_chain_to_json_roundtrip():
    import json
    chain = DeductionChain(input={"year": 1974}, pills=["庚午", "辛巳", "乙酉", "甲申"])
    chain.append(DeductionStep(1, "shishen.detect_combos", "天干=庚辛乙甲",
                               "伤官见官", "子平真诠·十神", "伤官正官同现"))
    data = json.loads(chain.to_json())
    assert data["pills"] == ["庚午", "辛巳", "乙酉", "甲申"]
    assert data["steps"][0]["output"] == "伤官见官"
