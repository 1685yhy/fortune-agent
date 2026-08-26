import json
from src.engine.deduction import deduce

def test_deduce_includes_qiongtong_step():
    chain = deduce(["庚午", "乙酉", "甲午", "丁卯"], question="")
    steps = [s for s in chain.steps if s.rule.startswith("qiongtong_table")]
    assert len(steps) == 1
    s = steps[0]
    assert s.source == "穷通宝鉴·甲·酉月"
    assert s.output, "调候要点非空"
    assert len(s.output) <= 120

def test_qiongtong_step_uses_real_table():
    table = json.load(open("src/engine/cases/qiongtong_table.json", encoding="utf-8"))
    assert "甲" in table and "寅" in table["甲"]  # 阶段1 产物在位
