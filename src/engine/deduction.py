"""推演链：规则推演的逐步记录与可回放序列化（阶段2 核心数据结构）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeductionStep:
    step_id: int
    rule: str      # 规则来源，如 "geju.determine_geju" / "qiongtong_table[甲][寅]"
    fact: str      # 输入事实（人可读）
    output: str    # 推演结果
    source: str    # 依据出处（书名/规则名）
    rationale: str = ""  # 一句话推理依据

    def to_text(self) -> str:
        return (f"第{self.step_id}步 [{self.rule}]\n"
                f"  事实: {self.fact}\n"
                f"  推得: {self.output}\n"
                f"  依据: {self.source}\n"
                f"  理由: {self.rationale}")


@dataclass
class DeductionChain:
    input: dict                     # 原始输入（公历+性别等）
    pills: list[str]                # 四柱 [年,月,日,时]
    steps: list[DeductionStep] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)   # 覆盖清单/未覆盖标注

    def append(self, step: DeductionStep) -> None:
        self.steps.append(step)

    def add_coverage(self, key: str, note: str) -> None:
        self.coverage.setdefault(key, []).append(note)

    def to_text(self) -> str:
        lines = [f"四柱: {' '.join(self.pills)}", ""]
        lines += [s.to_text() for s in self.steps]
        if self.coverage:
            lines.append("")
            lines.append("## 覆盖说明")
            for key, notes in self.coverage.items():
                lines.append(f"- {key}: {'；'.join(notes)}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "input": self.input,
            "pills": self.pills,
            "steps": [vars(s) for s in self.steps],
            "coverage": self.coverage,
        }, ensure_ascii=False, indent=1)


from src.engine.rules.geju import determine_geju
from src.engine.rules.shishen import shishen_of, detect_combos
from src.engine.rules.shensha import shensha_of
from src.engine.rules.ziwei import analyze as ziwei_analyze, palace_order as ziwei_palace_order
from src.engine.rules.liuyao import YAO_TERMS, liuqin_of, analyze as liuyao_analyze
from src.engine.rules.qimen import analyze as qimen_analyze, men_attribute, star_attribute
from src.engine.rules.liuren import analyze as liuren_analyze, classify_zongmen


def _step(step_id: int, rule: str, fact: str, output: str,
          source: str, rationale: str = "") -> DeductionStep:
    return DeductionStep(step_id, rule, fact, output, source, rationale)


_QIONGTONG_PATH = Path(__file__).parent / "cases" / "qiongtong_table.json"
_QIONGTONG_CACHE: dict | None = None


def _load_qiongtong() -> dict:
    global _QIONGTONG_CACHE
    if _QIONGTONG_CACHE is None:
        _QIONGTONG_CACHE = json.loads(_QIONGTONG_PATH.read_text(encoding="utf-8"))
    return _QIONGTONG_CACHE


def deduce(pills: list[str], engine_result=None, question: str = "",
           system: str = "bazi") -> DeductionChain:
    """主推演链：按体系分支逐步记录（排盘→规则要点→断语要点）。

    system: "bazi"（默认，阶段2 行为完全一致）/ "ziwei" / "liuyao" / "qimen" / "liuren"。
    engine_result: 各体系排盘引擎结果（BaziResult/ZiweiResult/LiuyaoResult/
                   QimenResult/LiurenResult）；None 走降级路径并明示未覆盖。
    """
    if len(pills) != 4 or any(len(p) != 2 for p in pills):
        raise ValueError(f"pills 必须为四柱: {pills}")
    chain = DeductionChain(input={}, pills=pills)
    sid = 0

    def next_step(rule, fact, output, source, rationale=""):
        nonlocal sid
        sid += 1
        chain.append(_step(sid, rule, fact, output, source, rationale))

    if system == "bazi":
        _deduce_bazi(chain, next_step, pills, engine_result, question)
    elif system == "ziwei":
        _deduce_ziwei(chain, next_step, pills, engine_result, question)
    elif system == "liuyao":
        _deduce_liuyao(chain, next_step, pills, engine_result, question)
    elif system == "qimen":
        _deduce_qimen(chain, next_step, pills, engine_result, question)
    elif system == "liuren":
        _deduce_liuren(chain, next_step, pills, engine_result, question)
    else:
        raise ValueError(f"未知推演体系: {system}（支持 bazi/ziwei/liuyao/qimen/liuren）")
    return chain


def _deduce_bazi(chain: DeductionChain, next_step, pills: list[str],
                 engine_result=None, question: str = "") -> None:
    """八字分支：阶段2 原逻辑原样保留（默认 system="bazi" 行为完全一致）。"""
    day_stem = pills[2][0]
    month_branch = pills[1][1]

    # 1. 排盘（engine_result 可选）
    if engine_result is not None:
        wuxing_str = "，".join(f"{k}{v}" for k, v in getattr(engine_result, "wuxing", {}).items())
        next_step("排盘引擎.calculate", f"出生信息→四柱 {' '.join(pills)}，日主 {day_stem}",
                  f"五行旺衰: {wuxing_str or '未知'}", "lunar-python+排盘引擎",
                  "八字排盘为确定性计算，同输入必同输出")
    else:
        next_step("排盘引擎.calculate", f"四柱 {' '.join(pills)}（无公历输入，pills-only 路径）",
                  "仅四柱可用", "lunar-python+排盘引擎",
                  "滴天髓命例无公历生日，排盘步骤降级为四柱直用")

    # 2. 十神 + 组合
    stems = [p[0] for p in pills]
    shishen_str = "，".join(f"{s}:{shishen_of(day_stem, s)}" for s in stems)
    combos = detect_combos(pills)
    combos_str = "、".join(combos) if combos else "无经典组合命中"
    next_step("shishen.detect_combos", f"天干 {shishen_str}",
              combos_str, "子平真诠·十神",
              "十神按异性为正同性为偏；组合按经典规则五组判定")

    # 3. 格局
    geju = determine_geju(pills)
    next_step("geju.determine_geju", f"月支={month_branch}",
              geju, "子平真诠·八格",
              "月令藏干透干优先，不透取本气，比劫归建禄/月刃")

    # 3.5 调候用神（穷通宝鉴 120 格查表）
    try:
        table = _load_qiongtong()
        cell = table.get(day_stem, {}).get(month_branch, "")
        cell_text = cell[:60] + ("…" if len(cell) > 60 else "")
        next_step(f"qiongtong_table[{day_stem}][{month_branch}]",
                  f"日干 {day_stem} × 月支 {month_branch}",
                  cell_text, f"穷通宝鉴·{day_stem}·{month_branch}月",
                  "穷通宝鉴查表为确定性规则；乙丑/丁丑两格为源文本缺口冬尾补给(见阶段1审计)")
    except (KeyError, OSError) as exc:
        chain.add_coverage("未覆盖", f"穷通宝鉴查表失败: {exc}")

    # 4. 神煞
    shensha_hits = shensha_of(pills)
    next_step("shensha.shensha_of", f"四柱地支 {' '.join(p[1] for p in pills)}",
              "、".join(shensha_hits) if shensha_hits else "无命中",
              "渊海子平·神煞",
              "桃花/文昌/羊刃/禄神/华盖/孤辰寡宿，年日两局并查")

    # 5. 大运流年（engine_result 可选）
    if engine_result is not None:
        dayun = getattr(engine_result, "dayun", [])[:3]
        liunian = getattr(engine_result, "liunian", {})
        dayun_str = "，".join(f"{age}岁起{ganzhi}" for age, ganzhi in dayun) or "未知"
        liunian_str = "，".join(f"{y}:{gz}" for y, gz in list(liunian.items())[:3]) or "未知"
        next_step("大运流年.engine", f"近期大运 {dayun_str}；流年 {liunian_str}",
                  "大运流年已列", "排盘引擎·大运流年",
                  "大运阳男阴女顺排逆排，流年逐年干支")
    else:
        chain.add_coverage("未覆盖", "大运/流年（pills-only 无公历输入，阶段3 前不补）")

    # 6. 断语要点（面向 question 的规则组合）
    key_points = []
    if combos:
        key_points.append(f"组合提示：{'、'.join(combos)}")
    key_points.append(f"格局：{geju}")
    if shensha_hits:
        key_points.append(f"神煞：{'、'.join(shensha_hits)}")
    q = f"，针对问事「{question}」" if question else ""
    next_step("断语要点.compose", f"组合/格局/神煞 汇总{q}",
              "；".join(key_points), "规则组合",
              "要点句由规则结果确定性组装，不做自由发挥")


def _deduce_ziwei(chain: DeductionChain, next_step, pills: list[str],
                  result, question: str = "") -> None:
    """紫微分支：排盘(紫微)→五行局→四化→命宫/十二宫→断语要点（规则要点组装）。

    证据检索(evidence.py)为八字专属，本体系不举证；未提供排盘结果走未覆盖明示。
    """
    chain.add_coverage("未举证", "证据检索(evidence.py)为八字专属，本体系不做证据检索")
    if result is None:
        chain.add_coverage("未覆盖", "未提供紫微排盘结果(engine_result)，本体系无法排盘")
        return
    raw = getattr(result, "raw_data", {}) or {}
    year_gan = raw.get("year_gan") or pills[0][0]
    lunar_month = raw.get("lunar_month")
    lunar_day = raw.get("lunar_day")
    time_zhi = raw.get("time_zhi")
    lunar_str = f"农历{lunar_month}月{lunar_day}日" if lunar_month else "农历未知"

    # 1. 排盘（紫微）
    next_step("ziwei.排盘.calculate",
              f"出生信息→紫微排盘（年干{year_gan}，{lunar_str}，{time_zhi or '时支未知'}时）",
              f"命宫{result.ming_gong}、身宫{result.shen_gong}、五行局{result.wuxing_ju}",
              "lunar-python+紫微排盘引擎",
              "紫微排盘为确定性计算，同输入必同输出")
    # 2. 五行局（命宫干支纳音定局）
    next_step("ziwei.wuxing_ju_of",
              f"命宫{result.ming_gong}干支纳音定局（{raw.get('ming_gong_ganzhi', '')}）",
              result.wuxing_ju, "紫微斗数全书·安星诀",
              "命宫干支纳音定五行局（水二/木三/金四/土五/火六）")
    # 3. 生年四化
    sihua = getattr(result, "sihua", {}) or {}
    sihua_str = "、".join(f"{v}{k}" for k, v in sihua.items()) if sihua else "无四化"
    next_step("ziwei.sihua_of", f"生年干 {year_gan}",
              sihua_str, "生年四化表（甲廉破武阳…癸破巨阴贪）",
              "生年四化查表为确定性规则")
    # 4. 命宫/十二宫定序
    order = ziwei_palace_order(result.ming_gong)
    order_str = "、".join(f"{name}{dz}" for name, dz in order.items())
    next_step("ziwei.palace_order", f"命宫{result.ming_gong}起逆时针布十二宫",
              order_str, "紫微斗数全书·十二宫",
              "命宫→兄弟→夫妻→…→父母，逆时针定序")
    # 5. 断语要点（只组装规则要点，不做解释性断语）
    points = "；".join(ziwei_analyze(result))
    next_step("ziwei.断语要点.compose", f"紫微规则要点汇总（针对问事「{question}」）",
              points, "紫微规则库（确定性要点，无解释性断语）",
              "要点句由规则结果确定性组装，不做自由发挥")


def _deduce_liuyao(chain: DeductionChain, next_step, pills: list[str],
                   result, question: str = "") -> None:
    """六爻分支：起卦(seed 固定注明)→六亲→世应→动变→断语要点（规则要点组装）。

    起卦为随机过程，seed 由调用方固定以保证可复现；证据检索为八字专属不举证。
    """
    chain.add_coverage("未举证", "证据检索(evidence.py)为八字专属，本体系不做证据检索")
    if result is None:
        chain.add_coverage("未覆盖", "未提供六爻起卦结果(engine_result)，本体系无法推演")
        return
    day_gan = pills[2][0]

    # 1. 起卦（seed 固定注明）
    next_step("liuyao.起卦.cast",
              f"起卦方法 random，调用方固定 seed 可复现（问事「{question}」）",
              f"本卦 {result.original_hexagram}，动爻 {len(result.changing_lines)} 爻",
              "六爻排盘引擎（三枚铜钱六掷）",
              "六爻起卦为随机过程，固定 seed 保证测试可复现")
    # 2. 六亲（以日干五行为"我"）
    shi = result.shi_yao
    shi_dz = result.lines[shi].get("dizhi", "") if len(result.lines) > shi else ""
    if shi_dz:
        lq = liuqin_of(day_gan, shi_dz)
    else:
        lq = "（世爻无地支，无法判六亲）"
    next_step("liuyao.liuqin_of", f"日干{day_gan}为「我」，世爻地支 {shi_dz}",
              f"世爻六亲：{lq}", "火珠林·六亲",
              "生我父母/克我官鬼/我克妻财/同我兄弟/我生子孙")
    # 3. 世应
    next_step("liuyao.shiying_positions", f"本卦 {result.original_hexagram}",
              f"世在{YAO_TERMS[result.shi_yao]}、应在{YAO_TERMS[result.ying_yao]}",
              "卜筮正宗·安世应",
              "八宫六十四卦表查世位，应爻隔三位（世位+3 取模 6）")
    # 4. 动变（老阴/老阳动，阴阳互变得变卦）
    if result.changing_lines:
        terms = "、".join(YAO_TERMS[i] for i in result.changing_lines)
        next_step("liuyao.bian_hexagram", f"动爻 {terms}",
                  f"变卦 {result.changed_hexagram or '未知'}",
                  "火珠林·动变", "老阴/老阳为动爻，阴阳互变得变卦")
    else:
        next_step("liuyao.bian_hexagram", "无动爻（静卦）",
                  f"静卦不变（{result.original_hexagram}）",
                  "火珠林·动变", "老阴/老阳为动爻，无动则静卦")
    # 5. 断语要点（只组装规则要点，不做解释性断语）
    points = "；".join(liuyao_analyze(result, day_gan=day_gan))
    next_step("liuyao.断语要点.compose", f"六爻规则要点汇总（针对问事「{question}」）",
              points, "六爻规则库（确定性要点，无解释性断语）",
              "要点句由规则结果确定性组装，不做自由发挥")


def _deduce_qimen(chain: DeductionChain, next_step, pills: list[str],
                  result, question: str = "") -> None:
    """奇门分支：排盘(遁局/局数)→八门→九星→值符值使→断语要点（规则要点组装）。

    证据检索(evidence.py)为八字专属，本体系不举证；未提供排盘结果走未覆盖明示。
    """
    chain.add_coverage("未举证", "证据检索(evidence.py)为八字专属，本体系不做证据检索")
    if result is None:
        chain.add_coverage("未覆盖", "未提供奇门排盘结果(engine_result)，本体系无法推演")
        return

    # 1. 排盘（遁局/局数）
    next_step("qimen.排盘.calculate",
              "出生信息→奇门排盘（公历→节气定阴阳遁）",
              f"遁局 {result.dun_type}{result.ju_number}局",
              "lunar-python+奇门排盘引擎",
              "奇门排盘为确定性计算，同输入必同输出")
    # 2. 八门五行
    doors = set(result.bamen.values())
    door_str = "、".join(f"{m}属{men_attribute(m)}" for m in sorted(doors))
    next_step("qimen.men_attribute", f"八门分布：{'、'.join(sorted(doors))}",
              door_str, "烟波钓叟歌·八门",
              "开乾金/休坎水/生艮土/伤震木/杜巽木/景离火/死坤土/惊兑金")
    # 3. 九星五行
    stars = set(result.jiuxing.values())
    star_str = "、".join(f"{s}属{star_attribute(s)}" for s in sorted(stars))
    next_step("qimen.star_attribute", f"九星分布：{'、'.join(sorted(stars))}",
              star_str, "烟波钓叟歌·九星",
              "九星落宫五行，天禽寄坤二宫属土")
    # 4. 值符值使（排盘事实）
    next_step("qimen.值符值使", f"遁局 {result.dun_type}{result.ju_number}局",
              f"值符星 {result.zhifu_star}，值使门 {result.zhishi_door}",
              "奇门排盘引擎·值符值使",
              "值符随旬首落宫、值使随时干，由排盘确定")
    # 5. 断语要点（只组装规则要点，不做解释性断语）
    points = "；".join(qimen_analyze(result))
    next_step("qimen.断语要点.compose", f"奇门规则要点汇总（针对问事「{question}」）",
              points, "奇门规则库（确定性要点，无解释性断语）",
              "要点句由规则结果确定性组装，不做自由发挥")


def _deduce_liuren(chain: DeductionChain, next_step, pills: list[str],
                   result, question: str = "") -> None:
    """六壬分支：排盘(月将/天地盘)→四课→三传(宗门)→旬空/贵人→断语要点。

    证据检索(evidence.py)为八字专属，本体系不举证；排盘引擎的降级如实记未覆盖。
    """
    chain.add_coverage("未举证", "证据检索(evidence.py)为八字专属，本体系不做证据检索")
    if result is None:
        chain.add_coverage("未覆盖", "未提供六壬排盘结果(engine_result)，本体系无法推演")
        return
    chart = result.to_dict()
    raw = chart["raw_data"]
    day_gan, day_zhi = chart["day_gan"], chart["day_zhi"]
    tp_items = list(chart["tianpan"].items())
    tp_str = "、".join(f"{g}{p}" for g, p in tp_items[:6])
    if len(tp_items) > 6:
        tp_str += "…（共12宫）"

    # 1. 排盘（月将/天地盘）
    next_step("liuren.排盘.calculate",
              f"出生信息→六壬排盘（日柱{day_gan}{day_zhi}）",
              f"月将{chart['yuejiang']}加时{chart['hour_zhi']}，天地盘 {tp_str}",
              "lunar-python+六壬排盘引擎",
              "月将加占时顺布十二宫，同输入必同输出")
    # 2. 四课
    ke_str = "、".join(f"{k['位置']}{k['干支']}" for k in chart["sipan"])
    next_step("liuren.sipan", f"日干寄宫取干上神递生为课，日支取支上神递生为课",
              ke_str, "六壬大全·四课",
              "课1干上神、课2递生、课3支上神、课4递生")
    # 3. 三传（宗门）
    zongmen, kename = classify_zongmen(chart)
    next_step("liuren.classify_zongmen", f"盘型 {raw.get('盘型', '常')}，四课上下克情判定宗门",
              f"三传 {chart['sanchuan']}，为{kename}（{zongmen}）",
              "六壬大全·九宗门",
              "贼克/比用/涉害/遥克/昴星/别责/八专/返吟/伏吟确定性判定")
    # 4. 旬空/贵人
    guiren = f"{raw.get('guiren_shen', '')}{raw.get('guiren_direction', '')}" or "（未排）"
    next_step("liuren.kongwang", f"日柱{day_gan}{day_zhi}所在旬",
              f"旬空 {chart['xunkong']}；贵人{guiren}",
              "六壬大全·旬空/贵人",
              "日柱旬首推空亡二字；甲戊庚牛羊定贵人，落阳支顺行阴支逆行")
    # 4.5 排盘降级如实记录（绝不装懂）
    for d in raw.get("降级") or []:
        chain.add_coverage("未覆盖", f"六壬排盘降级：{d}")
    # 5. 断语要点（只组装规则要点，不做解释性断语）
    points = "；".join(liuren_analyze(chart))
    next_step("liuren.断语要点.compose", f"六壬规则要点汇总（针对问事「{question}」）",
              points, "六壬规则库（确定性要点，无解释性断语）",
              "要点句由规则结果确定性组装，不做自由发挥")
