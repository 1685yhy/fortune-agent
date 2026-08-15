# src/engine/case_loader.py
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Case:
    id: str
    source: str
    source_lines: str
    pills: list[str] = field(default_factory=list)
    gender: str = ""
    expected: dict = field(default_factory=dict)
    prose: str = ""
    quality: str = "reference"
    audit: str = ""

    REQUIRED = ("id", "source", "source_lines")
    QUALITIES = ("unit", "reference", "rejected")
    STEMS = "甲乙丙丁戊己庚辛壬癸"
    BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

    def validate(self) -> None:
        for key in self.REQUIRED:
            if not getattr(self, key):
                raise ValueError(f"case {self.id or '?'}: 缺必需字段 {key}")
        if self.quality not in self.QUALITIES:
            raise ValueError(f"case {self.id}: quality 非法: {self.quality}")
        if self.pills:
            if len(self.pills) != 4 or any(len(p) != 2 for p in self.pills):
                raise ValueError(f"case {self.id}: pills 必须为 4 柱各 2 字")
            if any(p[0] not in self.STEMS or p[1] not in self.BRANCHES for p in self.pills):
                raise ValueError(f"case {self.id}: pills 含非法干支: {self.pills}")


def load_cases(path: str) -> list[Case]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} JSON 解析失败: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"{path}:{line_no} 非对象")
            case = Case(**{k: data.get(k, "") for k in Case.__dataclass_fields__})
            case.validate()
            cases.append(case)
    return cases
