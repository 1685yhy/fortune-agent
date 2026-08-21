"""多盘对比引擎（P1-2）：同一人生日不同时辰的两盘差异对比（确定性规则，0 LLM）。

PM 场景：没出生时辰 / 想对比不同时辰的命局差异（如"辰时 vs 午时"）——
同一生辰不同时辰各排一盘（复用 BaziEngine 全量排盘），逐项对比：
四柱 / 日主（晚子时归日可能变日柱）/ 五行能量 / 用神 / 格局 / 大运（起运岁数+序列）/
神煞，并输出规则模板拼装的差异摘要（compare_summary，不调 LLM）。

口径：
- hour 接受 时辰序号 0-11（子..亥，子=晚子时 23 点，与 BaziEngine 晚子时处理一致）
  或时钟小时 12-23（birth_contract.normalize_hour 契约）；
- 两盘均按分钟 0 排（接口不带 minute：辰时→07:00、午时→11:00，真太阳时修正后可能
  跨日/换时辰——与排盘接口同口径）；
- pan_a/pan_b 复用 paipan.serialize_bazi 全字段序列化（与 /api/paipan 输出一致）；
- diff 各字段均从两盘 BaziResult 直接对比得出（与 pan_a/pan_b 同源，天然自洽）。
"""
from typing import Optional, Tuple

from src.engines.bazi import BaziEngine, BaziResult, WUXING_ORDER
from src.api.birth_contract import normalize_hour, normalize_gender

# 时钟小时 → 时辰名（与 src/api/paipan.py SHICHEN_NAME 完全同口径：按时辰起点映射，
# 子时含晚子时 23 点口径，与 BaziEngine 晚子时处理一致；引擎侧独立持有免 API 依赖）
SHICHEN_NAME = {
    23: "子时", 0: "子时", 1: "丑时", 2: "丑时",
    3: "寅时", 4: "寅时", 5: "卯时", 6: "卯时",
    7: "辰时", 8: "辰时", 9: "巳时", 10: "巳时",
    11: "午时", 12: "午时", 13: "未时", 14: "未时",
    15: "申时", 16: "申时", 17: "酉时", 18: "酉时",
    19: "戌时", 20: "戌时", 21: "亥时", 22: "亥时",
}

_PILLAR_NAMES = ("年柱", "月柱", "日柱", "时柱")


def _extract_yongshen_core(text: str) -> str:
    """用神文本中的核心五行："水为用神（调候优先）（喜水、木）" → "水"。"""
    if "为用神" in text:
        return text.split("为用神", 1)[0].strip()
    return text


def _xiyong(text: str) -> str:
    """用神文本中的喜用五行："…（喜水、木）" → "水、木"。"""
    if "（喜" in text:
        return text.split("（喜", 1)[1].rstrip("）")
    return text


def _pillar_changes(a: BaziResult, b: BaziResult) -> list:
    """两盘四柱逐柱对比 → [{pillar, a, b, a_shishen, b_shishen, a_nayin, b_nayin}]。"""
    changes = []
    for i in range(4):
        if a.bazi[i] != b.bazi[i]:
            changes.append({
                "pillar": _PILLAR_NAMES[i],
                "a": a.bazi[i], "b": b.bazi[i],
                "a_shishen": a.shishen[i], "b_shishen": b.shishen[i],
                "a_nayin": a.nayin[i], "b_nayin": b.nayin[i],
            })
    return changes


def compare_summary(a: BaziResult, b: BaziResult,
                    hours: Optional[Tuple[int, int]] = None) -> str:
    """规则模板拼装两盘差异要点摘要（不调 LLM，确定性规则）。

    :param a/b: BaziResult（compare_pans 排出的两盘）
    :param hours: (时钟小时a, 时钟小时b)（可选）——提供时在日主变化处给出时辰名定位
    :return: 中文要点摘要字符串（"；"分隔要点列表，变化影响大的要点在前）
    """
    parts = []
    if a.bazi == b.bazi:
        return "两盘完全相同：同一生辰、同一时辰排出的两盘结果一致，无差异。"

    sh_a = sh_b = ""
    if hours is not None:
        sh_a = SHICHEN_NAME.get(hours[0], "")
        sh_b = SHICHEN_NAME.get(hours[1], "")

    # 0 关键影响（变化大的要点先行：日主 > 用神 > 格局）
    impacts = []
    if a.day_master != b.day_master:
        impacts.append("日主改变——两盘喜忌基调不同")
    if _extract_yongshen_core(a.yongshen) != _extract_yongshen_core(b.yongshen):
        impacts.append("用神改变——喜忌取向随之变化")
    if a.geju != b.geju:
        impacts.append("格局改变——命局类型不同")
    if impacts:
        parts.append("关键差异：%s" % "、".join(impacts))

    # 1 日主（晚子时归日说明）
    if a.day_master == b.day_master:
        parts.append("两盘日主同为%s" % a.day_master)
    else:
        who = ""
        if hours is not None:
            if hours[0] >= 23:
                who = "前者（%s）" % sh_a
            elif hours[1] >= 23:
                who = "后者（%s）" % sh_b
        late_txt = ("——%s处于晚子时（23点后），按次日排盘，日柱随之改变" % who) if who \
            else "——晚子时（23点后）出生按次日排盘，日柱随之改变"
        parts.append("两盘日主不同：前者为%s、后者为%s%s"
                     % (a.day_master, b.day_master, late_txt))

    # 2 四柱变化（哪柱变、变什么；bazi 全同已提前返回，此处必有变化）
    changes = _pillar_changes(a, b)
    head = ("四柱中仅%s发生变化" % "、".join(c["pillar"] for c in changes)) \
        if len(changes) == 1 \
        else ("四柱中%s发生变化" % "、".join(c["pillar"] for c in changes))
    descs = []
    for c in changes:
        t = ("%s→%s" % (c["a"], c["b"])) if len(changes) == 1 \
            else ("%s%s→%s" % (c["pillar"], c["a"], c["b"]))
        if c["a_shishen"] != c["b_shishen"]:
            t += "（十神由%s变为%s）" % (c["a_shishen"], c["b_shishen"])
        descs.append(t)
    parts.append("%s：%s" % (head, "；".join(descs)))

    # 3 五行能量（counts 差）
    def _counts_txt(counts) -> str:
        return "".join("%s%d" % (w, counts.get(w, 0)) for w in WUXING_ORDER)

    if a.wuxing == b.wuxing:
        parts.append("五行能量相同：%s" % _counts_txt(a.wuxing))
    else:
        deltas = []
        for w in WUXING_ORDER:
            d = b.wuxing.get(w, 0) - a.wuxing.get(w, 0)
            if d > 0:
                deltas.append("%s增%d" % (w, d))
            elif d < 0:
                deltas.append("%s减%d" % (w, -d))
        parts.append("五行能量由%s变为%s——%s"
                     % (_counts_txt(a.wuxing), _counts_txt(b.wuxing), "、".join(deltas)))

    # 4 日主强弱（命局从偏弱转向…）
    sa = a.wuxing_energy.get("strength", "")
    sb = b.wuxing_energy.get("strength", "")
    if sa and sb and sa != sb:
        parts.append("日主强弱由%s转为%s" % (sa, sb))

    # 5 用神（核心 + 喜用）
    core_a, core_b = _extract_yongshen_core(a.yongshen), _extract_yongshen_core(b.yongshen)
    if a.yongshen == b.yongshen:
        parts.append("用神同为「%s」" % a.yongshen)
    elif core_a == core_b:
        parts.append("用神核心同为「%s」，喜用五行变化：「%s」→「%s」"
                     % (core_a, _xiyong(a.yongshen), _xiyong(b.yongshen)))
    else:
        parts.append("用神由「%s」变为「%s」" % (a.yongshen, b.yongshen))

    # 6 格局
    if a.geju == b.geju:
        parts.append("格局同为%s" % a.geju)
    else:
        parts.append("格局由%s变为%s" % (a.geju, b.geju))

    # 7 大运（起运岁数 + 序列）
    start_a = a.dayun[0][0] if a.dayun else 0
    start_b = b.dayun[0][0] if b.dayun else 0
    seq_same = [g for _, g in a.dayun] == [g for _, g in b.dayun]
    if start_a == start_b:
        tail = "，大运序列相同（首步%s）" % (a.dayun[0][1] if a.dayun else "") \
            if seq_same else "，但大运序列不同"
        parts.append("起运岁数同为%d岁%s" % (start_a, tail))
    else:
        parts.append("起运岁数由%d岁变为%d岁（相差%d岁）——出生时刻距交节气时间不同所致；"
                     "大运序列%s" % (start_a, start_b, abs(start_b - start_a),
                                     "相同" if seq_same else "不同"))

    # 8 神煞（新增/消失）
    set_a, set_b = set(a.shensha), set(b.shensha)
    added = [n for n in b.shensha if n not in set_a]
    removed = [n for n in a.shensha if n not in set_b]
    if added or removed:
        parts.append("神煞变化：新增%s%s"
                     % ("、".join(added),
                        ("、消失%s" % "、".join(removed)) if removed else ""))

    return "；".join(parts)


def _compare_results(a: BaziResult, b: BaziResult, ha: int, hb: int,
                     shichen_a: str, shichen_b: str) -> dict:
    """两盘差异对比（确定性规则）→ diff 字典（供 API 返回）。

    字段：same / hours / four_pillars / day_master / wuxing / yongshen /
    geju / dayun / shensha。
    """
    changes = _pillar_changes(a, b)

    # 日主（晚子时归日说明：日柱按次日排盘 —— 仅当日柱实际改变时才会发生，
    # 此时必为时钟 23 点（晚子时）或真太阳时跨日所致）
    day_same = a.day_master == b.day_master
    if day_same:
        if ha >= 23 and hb >= 23:
            day_note = "两盘均处晚子时（23:00-23:59），同按次日日期排盘，日柱不变"
        else:
            day_note = "两时辰均非晚子时（23:00-23:59），日柱不受时辰影响——时辰只改时柱"
    else:
        who = ""
        if ha >= 23 and hb < 23:
            who = "前者（%s）" % shichen_a
        elif hb >= 23 and ha < 23:
            who = "后者（%s）" % shichen_b
        if who:
            day_note = ("日柱随时辰改变：%s处于晚子时（23:00-23:59），排盘按次日日期，"
                        "日柱按次日排" % who)
        else:
            day_note = "日柱随时辰改变（真太阳时修正跨日所致），排盘按修正后日期"

    # 五行能量（counts 差：金-1/水+1…；diff 恒含五键）
    counts_a, counts_b = a.wuxing, b.wuxing
    wuxing_diff = {w: counts_b.get(w, 0) - counts_a.get(w, 0) for w in WUXING_ORDER}

    # 用神（全文本 + 核心五行分离：如"水为用神（调候优先）（喜水、木）"核心为"水"）
    yongshen = {
        "a": a.yongshen, "b": b.yongshen, "same": a.yongshen == b.yongshen,
        "core_a": _extract_yongshen_core(a.yongshen),
        "core_b": _extract_yongshen_core(b.yongshen),
        "core_same": _extract_yongshen_core(a.yongshen)
        == _extract_yongshen_core(b.yongshen),
    }

    # 大运（起运岁数 + 序列；序列由月柱与性别顺/逆排决定）
    start_a = a.dayun[0][0] if a.dayun else 0
    start_b = b.dayun[0][0] if b.dayun else 0
    seq_a = [g for _, g in a.dayun]
    seq_b = [g for _, g in b.dayun]
    dayun = {
        "start_same": start_a == start_b,
        "a_start": start_a, "b_start": start_b,
        "start_gap": start_b - start_a,
        "a_qiyun": a.qiyun_desc, "b_qiyun": b.qiyun_desc,
        "sequence_same": seq_a == seq_b,
        "a": [{"sui": s, "ganzhi": g} for s, g in a.dayun],
        "b": [{"sui": s, "ganzhi": g} for s, g in b.dayun],
        "note": ("大运干支序列由月柱与性别（阳男阴女顺排/阴男阳女逆排）决定——两盘月柱"
                 "相同则序列相同；起运岁数由出生时刻距交节气的远近决定，时辰差直接影响"
                 "起运分解"),
    }

    # 神煞（新增/消失/共有，保各盘原有顺序）
    set_a, set_b = set(a.shensha), set(b.shensha)
    shensha = {
        "same": set_a == set_b,
        "added": [n for n in b.shensha if n not in set_a],
        "removed": [n for n in a.shensha if n not in set_b],
        "common": [n for n in a.shensha if n in set_b],
    }

    return {
        "same": not changes,  # 四柱全同 ⇔ 两盘全同（时辰相同）
        "hours": {"a": shichen_a, "b": shichen_b},
        "four_pillars": {
            "same": not changes,
            "changed": [c["pillar"] for c in changes],
            "changes": changes,
        },
        "day_master": {
            "a": a.day_master, "b": b.day_master,
            "same": day_same, "note": day_note,
        },
        "wuxing": {
            "same": counts_a == counts_b,
            "a": dict(counts_a), "b": dict(counts_b),
            "diff": wuxing_diff,
            "changes": [{"wuxing": w, "delta": wuxing_diff[w]}
                        for w in WUXING_ORDER if wuxing_diff[w] != 0],
            "a_strength": a.wuxing_energy.get("strength", ""),
            "b_strength": b.wuxing_energy.get("strength", ""),
            "strength_same": a.wuxing_energy.get("strength", "")
            == b.wuxing_energy.get("strength", ""),
        },
        "yongshen": yongshen,
        "geju": {"a": a.geju, "b": b.geju, "same": a.geju == b.geju},
        "dayun": dayun,
        "shensha": shensha,
    }


def compare_pans(birth: Tuple[int, int, int], hour_a, hour_b,
                 city: str, gender: str, engine: Optional[BaziEngine] = None) -> dict:
    """同一生日不同时辰两盘对比（确定性规则，0 LLM）。

    :param birth: (year, month, day)
    :param hour_a/hour_b: 时辰序号 0-11（子..亥，子=晚子时 23 点）或时钟小时 12-23
    :param city: 出生城市（真太阳时修正；未知城市不修正）
    :param gender: 男/女/male/female（'unknown' 按男排盘）
    :param engine: 注入 BaziEngine（缺省新建）
    :return: {"pan_a": 排盘全字段, "pan_b": 排盘全字段, "diff": 差异对比, "summary": 规则摘要}
    """
    engine = engine or BaziEngine()
    year, month, day = birth
    ha, hb = normalize_hour(hour_a), normalize_hour(hour_b)
    gender = normalize_gender(gender)
    r_a = engine.calculate(year, month, day, ha, 0, city, gender)
    r_b = engine.calculate(year, month, day, hb, 0, city, gender)

    # 复用排盘全字段序列化（与 /api/paipan 输出一致；函数级导入避免引擎↔API 包级循环）
    from src.api.paipan import serialize_bazi
    from src.api.hehun import BaziInput

    def _person(h: int) -> BaziInput:
        return BaziInput(year=year, month=month, day=day, hour=h, minute=0,
                         city=city, gender=gender)

    pan_a = serialize_bazi(r_a, engine, _person(ha))
    pan_b = serialize_bazi(r_b, engine, _person(hb))
    diff = _compare_results(r_a, r_b, ha, hb,
                            SHICHEN_NAME.get(ha, ""), SHICHEN_NAME.get(hb, ""))
    return {
        "pan_a": pan_a, "pan_b": pan_b, "diff": diff,
        "summary": compare_summary(r_a, r_b, (ha, hb)),
    }
