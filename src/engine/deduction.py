"""推演链：规则推演的逐步记录与可回放序列化（阶段2 核心数据结构）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


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
