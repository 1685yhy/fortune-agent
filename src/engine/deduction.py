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


def deduce(pills: list[str], engine_result=None, question: str = "") -> DeductionChain:
    """主推演链：排盘→十神→格局→神煞→大运流年→断语要点，逐步记录。
    engine_result: BaziResult（可选）——提供五行旺衰/大运/流年；None 走 pills-only 路径。"""
    if len(pills) != 4 or any(len(p) != 2 for p in pills):
        raise ValueError(f"pills 必须为四柱: {pills}")
    chain = DeductionChain(input={}, pills=pills)
    sid = 0

    def next_step(rule, fact, output, source, rationale=""):
        nonlocal sid
        sid += 1
        chain.append(_step(sid, rule, fact, output, source, rationale))

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

    return chain
