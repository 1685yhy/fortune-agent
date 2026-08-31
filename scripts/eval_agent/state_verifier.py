#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L4 临时库状态断言执行器（E5）：读 state_checks 在临时库上查表断言。

纯函数式断言器（零 LLM / 零新增依赖 / 只查库，spec §5.4 核心机制 #3）：

    输入 (库路径, 跑前行数快照, state_checks 契约) → 逐键断言通过/失败 + 原因

三键语义（spec §5.4 #3 / E1 标注 §2 / brief 实查口径）：
  <表>_created   = true → 跑后行数 > 跑前行数（persons/chat/favorites/chart_records 等业务表）
  <表>_unchanged = true → 跑后行数 == 跑前行数
  <表>_equals    = <值> → 跑后行数 == 指定值（评估集当前 0 条使用，schema 契约完备性实现）

表名映射（关键）：state_checks 键为**业务表名**（chat/memories 等），物理表以真实库
schema / DAO 源码为准——主链对话历史落 `sessions` 表（SessionDAO.add_message 写
user+assistant 两行；直读/快路径不写 chat，E1 标注 §4 事实），故 `chat` → `sessions`。
物理表名先在库内查（原样命中即用），未命中再查别名映射；表不存在按行数 0 处理
（懒建表场景：chart_records/favorites 由 DAO 构造器 CREATE IF NOT EXISTS 自建，
created 断言起点 = 0）。

运行期纪律（E5 brief）：断言一律在**临时库副本**上执行（生产库零写入由
l4_eval 的 seed_db_copy 隔离兜底）；本模块只读连接，不写库。

_equals 值类型说明：评估集 schema 校验器（validate_tasks.py）目前对
state_checks 全部值强制 bool（_equals 无从表达数值），且评估集 0 条使用——
本实现按 spec 契约支持 int（行数精确相等）；bool 兜底（True=等于跑前快照，
False=等于 0）仅为校验器兼容的退化语义，E6 若启用 _equals 数值断言须同步
放宽校验器（见 task-E5-report.md concerns）。
"""
import re
import sqlite3
from pathlib import Path

# 业务表名 → 物理表名映射（以真实库 schema / DAO 源码为事实源）
TABLE_ALIASES = {
    "chat": "sessions",  # 主链对话历史（SessionDAO.add_message，LLM 分析路径写两行）
}

_STATE_KEY_RE = re.compile(r"^(.+)_(created|unchanged|equals)$")


def resolve_table_name(name: str) -> str:
    """业务表名 → 物理表名：原样命中用原样，否则查别名映射。"""
    return TABLE_ALIASES.get(name, name)


def _connect(db_path):
    """只读连接（sqlite3.connect 原生；库路径可 sqlite:// 风格）。"""
    return sqlite3.connect(str(db_path))


def count_table(db_path, table: str) -> int:
    """物理表行数；表不存在 → 0（懒建表场景 created 断言起点）。"""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not row or row[0] == 0:
            return 0
        return con.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0]
    finally:
        con.close()


def snapshot_counts(db_path) -> dict:
    """跑前行数快照：库内全部物理表 → 行数（sqlite_* 内部表剔除）。"""
    con = _connect(db_path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'")]
        out = {}
        for t in tables:
            out[t] = con.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
        return out
    finally:
        con.close()


def parse_state_key(key: str):
    """解析断言键 → (table, action)；非法 → None。"""
    m = _STATE_KEY_RE.match(key)
    if not m:
        return None
    return m.group(1), m.group(2)


def run_state_checks(db_path, before_counts: dict, state_checks: dict) -> dict:
    """逐键断言。返回 {ok, checks, detail}。

    - before_counts：snapshot_counts 跑前快照（缺失表按 0）
    - state_checks：任务契约（{<表>_created/_unchanged/_equals: 值}）
    - checks：逐键结果 [{key, table, action, before, after, expected, pass, reason}]
      断言失败/非法键 → pass=False + 原因（期望 vs 实际行数变化）
    """
    before_counts = before_counts or {}
    checks = []
    for key, value in (state_checks or {}).items():
        parsed = parse_state_key(key)
        if parsed is None:
            checks.append({
                "key": key, "table": "?", "action": "?",
                "before": None, "after": None, "expected": value,
                "pass": False,
                "reason": f"非法断言键 {key!r}（须 <表>_created/_unchanged/_equals）",
            })
            continue
        table, action = parsed
        physical = resolve_table_name(table)
        before = before_counts.get(physical, 0)
        after = count_table(db_path, physical)
        entry = {"key": key, "table": physical, "action": action,
                 "before": before, "after": after, "expected": value,
                 "pass": True, "reason": ""}
        if action == "created":
            ok = after > before
            entry["reason"] = ("" if ok else
                               f"{physical}: 跑前 {before} 行 → 跑后 {after} 行"
                               "（期望增加）")
        elif action == "unchanged":
            ok = after == before
            entry["reason"] = ("" if ok else
                               f"{physical}: 跑前 {before} 行 → 跑后 {after} 行"
                               "（期望不变）")
        else:  # equals
            if isinstance(value, bool):
                expected = before if value else 0  # 校验器 bool 兼容退化语义
            else:
                try:
                    expected = int(value)
                except (TypeError, ValueError):
                    entry["pass"] = False
                    entry["reason"] = (f"equals 值 {value!r} 非法"
                                       "（须为整数行数或 bool）")
                    checks.append(entry)
                    continue
            ok = after == expected
            entry["expected"] = expected
            entry["reason"] = ("" if ok else
                               f"{physical}: 实际 {after} 行，期望 {expected} 行")
        entry["pass"] = ok
        checks.append(entry)
    ok_all = all(c["pass"] for c in checks)
    detail = "；".join(
        f"{c['table']}: {c['before']}→{c['after']}"
        + ("✓" if c["pass"] else f"✗ {c['reason']}")
        for c in checks) or "（无断言键）"
    return {"ok": ok_all, "checks": checks, "detail": detail}
