#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S1：L3 门禁「边界/攻击类」剔除范围的唯一事实源（纯函数，零依赖）。

判据（k39 勘察结论，**可判定** —— 机器可判、无人工逐条挑选）：

    task["category"] == "edge"

即评估集 10 域里的 `edge` 域。它是 `data/eval/agent_tasks.jsonl` 里
**唯一**的结构化边界标记：任务 schema（`validate_tasks.CATEGORIES`）
没有 attack/boundary 字段，`neg_checks` / `no_tool` / `expected_tools=[]`
在正常任务里同样出现（T002/T003 等 paipan 任务也是 no_tool），不能作为
边界判据；`severity` / `source` 亦与边界无关。`edge` 域的定义见
`docs/superpowers/eval/2026-08-31-eval-set-annotation.md:102`
（空输入/乱码/SQL 注入/超长/未知意图/额度门/支付守卫/改城市重算/性别链路/
明天追问/明确重排/emoji/工具边界/G4 三连）。

为什么只剔 L3：这些任务的目标是**韧性与安全**（该说的说了 / 不该说的没说 /
不落库 / 不硬断 / 不越权），而 L3 五维质量判卷（accuracy/completeness/
personalization/actionability/citation_quality）系统性给这类"礼貌兜底"回复
低分——用质量均值评价韧性目标属于口径错配（E6 归因 §3.2）。因此：

- **L3**：`weighted_avg` / `p0_avg` 剔除 edge 任务（门禁口径）；
  同时输出 `weighted_avg_incl_edge` / `p0_avg_incl_edge` 如实公示含边界口径。
- **L1 / L2 / L4**：**一分不减**，edge 任务仍进分母、仍是硬门禁
  （L1 工具选择/参数、L2 断言通过率、L4 状态断言照常覆盖它们）。
  `runner._aggregate_layers` 的 L1/L2 视图根本不携带 `category`，
  结构上不可能做类别剔除——由 `tests/test_k39_edge_scope.py` 断言。

阈值不变（L3 加权 ≥7.5 / P0 ≥8.0），本次只改聚合口径，不动阈值。
"""

# 边界/攻击类判据：评估集 category 字段的 edge 域
EDGE_CATEGORY = "edge"


def is_edge_category(category) -> bool:
    """category 值是否属边界/攻击类（None / 非字符串 一律 False）。"""
    return category == EDGE_CATEGORY


def is_edge_record(record) -> bool:
    """任务定义 dict 或判卷记录 dict 是否属边界/攻击类。

    两者都带 `category` 键（见 `validate_tasks.REQUIRED_KEYS` 与
    `judge.judge_task` 的返回体），因此同一判据对两层通用。
    """
    if not isinstance(record, dict):
        return False
    return is_edge_category(record.get("category"))


def split_edge(records: list) -> tuple:
    """records → (非边界, 边界) 两个列表（保持原顺序，不修改入参）。"""
    edge, main = [], []
    for r in records or []:
        (edge if is_edge_record(r) else main).append(r)
    return main, edge


def edge_ids(records: list) -> list:
    """边界/攻击类任务的 id 列表（用于报告公示被剔除清单）。"""
    return [r.get("id") for r in (records or []) if is_edge_record(r)]
