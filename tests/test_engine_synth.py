# tests/test_engine_synth.py
"""多体系合成层测试（阶段4 Task 1/3）：共识/分歧/不可比较三分类 + 合成考卷 + 真实链合成。

口径（与 synth.py 文档一致）：
- 可比较键：五行（八字用神五行/紫微五行局/奇门值符星五行/六壬三传五行众数/六爻世爻地支五行）、
  时间（八字流年年份/紫微大限起始年份，统一取起始年份）
- 每体系每键取首个断言为规范值；无公共键的体系对如实列入不可比较
"""
import json
from pathlib import Path

from src.engine.deduction import DeductionChain, DeductionStep, deduce
from src.engine.synth import SystemResult, SynthResult, extract_facts, synthesize

DIVERGE_NOTE = "两说并存，各带出处，由用户结合实际情况权衡"
PILLS = ["庚午", "辛巳", "乙酉", "甲申"]


# ---------- 工具 ----------

def _chain(steps):
    """steps: list of (rule, fact, output, source) → DeductionChain。"""
    chain = DeductionChain(input={}, pills=list(PILLS))
    for i, (rule, fact, output, source) in enumerate(steps, 1):
        chain.append(DeductionStep(i, rule, fact, output, source, ""))
    return chain


def _res(system, steps):
    return SystemResult(system=system, chain=_chain(steps))


# ---------- Task 1：三分类判定 ----------

def test_synth_wuxing_consensus_two_systems():
    """两体系五行一致（八字用神癸水 vs 紫微水二局）→ 共识含该点（含参与体系与出处）。"""
    bazi = _res("bazi", [
        ("qiongtong_table[乙][巳]", "日干乙 × 月支巳",
         "四月乙木，专用癸水为尊，丙火酌用", "穷通宝鉴·乙·巳月"),
    ])
    ziwei = _res("ziwei", [
        ("ziwei.wuxing_ju_of", "命宫酉干支纳音定局（乙酉）", "水二局", "紫微斗数全书·安星诀"),
    ])
    synth = synthesize([bazi, ziwei])
    assert synth.systems == ["bazi", "ziwei"]
    assert synth.consensus == [{
        "point": "五行一致：水",
        "systems": ["bazi", "ziwei"],
        "evidence": [
            {"system": "bazi", "text": "四月乙木，专用癸水为尊，丙火酌用",
             "source": "穷通宝鉴·乙·巳月"},
            {"system": "ziwei", "text": "水二局", "source": "紫微斗数全书·安星诀"},
        ],
    }]
    assert synth.divergences == []
    assert synth.unresolved == []


def test_synth_wuxing_divergence_two_systems():
    """两体系五行不同（八字用神水 vs 紫微金四局）→ 分歧两说并存各带 source。"""
    bazi = _res("bazi", [
        ("qiongtong_table[乙][巳]", "日干乙 × 月支巳",
         "四月乙木，专用癸水为尊", "穷通宝鉴·乙·巳月"),
    ])
    ziwei = _res("ziwei", [
        ("ziwei.wuxing_ju_of", "命宫申干支纳音定局（壬申）", "金四局", "紫微斗数全书·安星诀"),
    ])
    synth = synthesize([bazi, ziwei])
    assert synth.consensus == []
    assert synth.divergences == [{
        "topic": "五行不同：水、金",
        "views": [
            {"system": "bazi", "view": "水", "source": "穷通宝鉴·乙·巳月"},
            {"system": "ziwei", "view": "金", "source": "紫微斗数全书·安星诀"},
        ],
        "note": DIVERGE_NOTE,
    }]


def test_synth_liuyao_no_wuxing_unresolved():
    """六爻链无世爻地支（无五行可抽取）vs 八字 → 不可比较如实说明，不硬造共识。"""
    bazi = _res("bazi", [
        ("qiongtong_table[乙][巳]", "日干乙 × 月支巳",
         "四月乙木，专用癸水为尊", "穷通宝鉴·乙·巳月"),
    ])
    liuyao = _res("liuyao", [
        ("liuyao.起卦.cast", "起卦方法 random", "本卦 雷火丰，动爻 3 爻", "六爻排盘引擎"),
        ("liuyao.liuqin_of", "日干乙为「我」，世爻地支 ",
         "世爻六亲：（世爻无地支，无法判六亲）", "火珠林·六亲"),
        ("liuyao.shiying_positions", "本卦 雷火丰", "世在五爻、应在二爻", "卜筮正宗·安世应"),
    ])
    synth = synthesize([bazi, liuyao])
    assert synth.consensus == []
    assert synth.divergences == []
    assert synth.unresolved == [
        "八字与六爻无公共比较维度（bazi:五行；liuyao:无事实要点），不硬造共识"]


def test_synth_empty_input_not_crash():
    """空输入 → 空结果不崩。"""
    synth = synthesize([])
    assert synth.systems == []
    assert synth.consensus == []
    assert synth.divergences == []
    assert synth.unresolved == []


def test_synth_time_consensus():
    """时间一致：八字流年 2026 与紫微大限 2026 起 → 共识（归一化为起始年份）。"""
    bazi = _res("bazi", [
        ("大运流年.engine", "近期大运 4岁起壬午；流年 2026:丙午，2027:丁未",
         "大运流年已列", "排盘引擎·大运流年"),
    ])
    ziwei = _res("ziwei", [
        ("ziwei.大限.daxian", "五行局水二局，大限依阴阳顺逆",
         "大限：2026-2035 丙午大限", "紫微斗数全书·大限"),
    ])
    synth = synthesize([bazi, ziwei])
    assert synth.consensus == [{
        "point": "时间一致：2026", "systems": ["bazi", "ziwei"],
        "evidence": [
            {"system": "bazi", "text": "流年 2026:丙午",
             "source": "排盘引擎·大运流年"},
            {"system": "ziwei", "text": "大限：2026-2035 丙午大限",
             "source": "紫微斗数全书·大限"},
        ],
    }]
    assert synth.divergences == []


def test_synth_time_divergence():
    """时间不同：八字流年 2026 vs 紫微大限 2036 起 → 分歧。"""
    bazi = _res("bazi", [
        ("大运流年.engine", "流年 2026:丙午", "大运流年已列", "排盘引擎·大运流年"),
    ])
    ziwei = _res("ziwei", [
        ("ziwei.大限.daxian", "五行局水二局", "大限：2036-2045 丁亥大限", "紫微斗数全书·大限"),
    ])
    synth = synthesize([bazi, ziwei])
    assert synth.divergences == [{
        "topic": "时间不同：2026、2036",
        "views": [
            {"system": "bazi", "view": "2026", "source": "排盘引擎·大运流年"},
            {"system": "ziwei", "view": "2036", "source": "紫微斗数全书·大限"},
        ],
        "note": DIVERGE_NOTE,
    }]


def test_synth_three_systems_consensus_plus_divergence():
    """三体系两说并存：八字/紫微同水 + 六爻金 → 共识与分歧并存（不挑一弃一）。"""
    bazi = _res("bazi", [
        ("qiongtong_table[乙][巳]", "日干乙 × 月支巳",
         "四月乙木，专用癸水为尊", "穷通宝鉴·乙·巳月"),
    ])
    ziwei = _res("ziwei", [
        ("ziwei.wuxing_ju_of", "命宫酉干支纳音定局（乙酉）", "水二局", "紫微斗数全书·安星诀"),
    ])
    liuyao = _res("liuyao", [
        ("liuyao.liuqin_of", "日干乙为「我」，世爻地支 申",
         "世爻六亲：官鬼", "火珠林·六亲"),
    ])
    synth = synthesize([bazi, ziwei, liuyao])
    assert synth.consensus == [{
        "point": "五行一致：水", "systems": ["bazi", "ziwei"],
        "evidence": [
            {"system": "bazi", "text": "四月乙木，专用癸水为尊",
             "source": "穷通宝鉴·乙·巳月"},
            {"system": "ziwei", "text": "水二局", "source": "紫微斗数全书·安星诀"},
        ],
    }]
    assert synth.divergences == [{
        "topic": "五行不同：水、金",
        "views": [
            {"system": "bazi", "view": "水", "source": "穷通宝鉴·乙·巳月"},
            {"system": "ziwei", "view": "水", "source": "紫微斗数全书·安星诀"},
            {"system": "liuyao", "view": "金", "source": "火珠林·六亲"},
        ],
        "note": DIVERGE_NOTE,
    }]


def test_extract_facts_ziwei_points_and_wuxing():
    """单体系事实抽取：五行局要点 + 可比较键标注（抽取器口径钉死）。"""
    facts = extract_facts("ziwei", _chain([
        ("ziwei.排盘.calculate", "出生信息→紫微排盘", "命宫酉、身宫丑、五行局水二局",
         "lunar-python+紫微排盘引擎"),
        ("ziwei.wuxing_ju_of", "命宫酉干支纳音定局（乙酉）", "水二局", "紫微斗数全书·安星诀"),
    ]))
    wuxing = [f for f in facts if f["key"] == "五行"]
    assert wuxing == [{
        "key": "五行", "value": "水", "text": "水二局", "source": "紫微斗数全书·安星诀",
    }]
    # 排盘步骤作为普通事实要点保留（key 为空串，不参与比较）
    assert any(f["key"] == "" and "五行局水二局" in f["text"] for f in facts)


# ---------- Task 3：合成考卷（synth_cases.jsonl） ----------

CASES_PATH = Path(__file__).parent.parent / "src" / "engine" / "cases" / "synth_cases.jsonl"


def _load_synth_cases():
    cases = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _case_results(case):
    """按考卷 systems 构建 SystemResult（字典序即参与顺序）。"""
    results = []
    for sys_name, steps in case["systems"].items():
        chain = DeductionChain(input={}, pills=list(PILLS))
        for i, st in enumerate(steps, 1):
            chain.append(DeductionStep(i, st["rule"], st.get("fact", ""),
                                       st["output"], st.get("source", ""), ""))
        results.append(SystemResult(system=sys_name, chain=chain))
    return results


def _synth_shape(synth):
    """合成结果的可断言形态（与考卷 expected 逐项对应）。"""
    return {
        "consensus": [(c["point"], c["systems"]) for c in synth.consensus],
        "divergences": [
            (d["topic"], [(v["system"], v["view"]) for v in d["views"]], d["note"])
            for d in synth.divergences],
        "unresolved": list(synth.unresolved),
    }


def test_synth_cases_all_pass():
    """合成考卷 ≥8 条全过：expected 三分类与 synthesize 输出逐项一致。"""
    cases = _load_synth_cases()
    assert len(cases) >= 8, f"合成考卷必须 ≥8 条，当前 {len(cases)}"
    for case in cases:
        exp = case["expected"]
        expected_shape = {
            "consensus": [(c["point"], c["systems"]) for c in exp["consensus"]],
            "divergences": [
                (d["topic"], [(v["system"], v["view"]) for v in d["views"]], d["note"])
                for d in exp["divergences"]],
            "unresolved": list(exp["unresolved"]),
        }
        synth = synthesize(_case_results(case))
        assert _synth_shape(synth) == expected_shape, f"{case['id']} 三分类与考卷不符"


def test_synth_cases_types_cover_three_categories():
    """考卷类型覆盖：共识≥3 / 分歧≥3 / 不可比较≥2。"""
    types = [c["type"] for c in _load_synth_cases()]
    assert sum(t.startswith("共识") for t in types) >= 3
    assert sum(t.startswith("分歧") for t in types) >= 3
    assert sum(t.startswith("不可比较") for t in types) >= 2


# ---------- Task 3：e2e 真实体系链合成 ----------

def _pills_of(year, month, day, hour):
    from lunar_python import Solar
    bazi = Solar.fromYmdHms(year, month, day, hour, 0, 0).getLunar().getEightChar()
    return [bazi.getYear(), bazi.getMonth(), bazi.getDay(), bazi.getTime()]


def test_e2e_real_chains_synthesize():
    """真实体系链合成（1990-05-20 16:30 北京 女）：
    bazi(用神水)+ziwei(水二局) 共识水；liuyao(世爻申金) 分歧金；
    qimen 未提供排盘结果（空链）→ 不可比较。三分类非空且无硬造共识。"""
    from src.engines.bazi import BaziEngine
    from src.engines.liuyao import LiuyaoEngine
    from src.engines.ziwei import ZiweiEngine

    pills = _pills_of(1990, 5, 20, 16)
    bz = BaziEngine().calculate(1990, 5, 20, 16, 30, "北京", "女")
    chain_bazi = deduce(bz.bazi, engine_result=bz, question="", system="bazi")
    zw = ZiweiEngine().calculate(1990, 5, 20, 16, 30, "北京", "女")
    chain_ziwei = deduce(pills, engine_result=zw, question="", system="ziwei")
    ly = LiuyaoEngine().cast(method="random", question="财运如何", seed=42)
    chain_liuyao = deduce(pills, engine_result=ly, question="财运如何", system="liuyao")
    chain_qimen_empty = deduce(pills, engine_result=None, question="", system="qimen")

    synth = synthesize([
        SystemResult(system="bazi", chain=chain_bazi),
        SystemResult(system="ziwei", chain=chain_ziwei),
        SystemResult(system="liuyao", chain=chain_liuyao),
        SystemResult(system="qimen", chain=chain_qimen_empty),
    ])
    assert synth.consensus, "真实链必须产生共识（八字用神水 vs 紫微水二局）"
    assert synth.divergences, "真实链必须产生分歧（水 vs 六爻世爻金）"
    assert synth.unresolved, "空链成员必须产生不可比较说明"

    # 钉死真实共识：五行一致：水，参与 bazi+ziwei
    c0 = next(c for c in synth.consensus if c["point"] == "五行一致：水")
    assert c0["systems"] == ["bazi", "ziwei"]
    # 无硬造共识：每个共识的证据必须来自真实链步骤且参与体系 ≥2
    for c in synth.consensus:
        assert len(c["systems"]) >= 2
        assert all(ev["text"] for ev in c["evidence"])
        assert all(ev["source"] for ev in c["evidence"])
    # 分歧如实：五行不同（水 vs 金）
    assert any("五行不同" in d["topic"] for d in synth.divergences)
    # 不可比较如实：qimen 空链与各体系均无公共维度
    assert any("qimen:无事实要点" in u for u in synth.unresolved)


def test_e2e_real_pair_ziwei_qimen_same_birth():
    """真实体系对（同一生日 1990-05-20 16:30）：紫微水二局 vs 奇门值符天英(火) → 五行分歧。

    两体系仅有五行公共键且断言不同 → 分歧；无共识、无不可比较（如实，不硬造）。"""
    from src.engines.qimen import QimenEngine
    from src.engines.ziwei import ZiweiEngine

    pills = _pills_of(1990, 5, 20, 16)
    zw = ZiweiEngine().calculate(1990, 5, 20, 16, 30, "北京", "女")
    qm = QimenEngine().calculate(1990, 5, 20, 16, 30)
    chain_zw = deduce(pills, engine_result=zw, question="", system="ziwei")
    chain_qm = deduce(pills, engine_result=qm, question="", system="qimen")

    synth = synthesize([
        SystemResult(system="ziwei", chain=chain_zw),
        SystemResult(system="qimen", chain=chain_qm),
    ])
    assert synth.consensus == []
    assert [d["topic"] for d in synth.divergences] == ["五行不同：水、火"]
    assert synth.unresolved == []
