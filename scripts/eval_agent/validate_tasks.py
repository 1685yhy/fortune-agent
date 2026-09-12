#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_tasks.py — E1 评估集 schema 校验器（纯标准库，零新增依赖）

用法：
    python3 scripts/eval_agent/validate_tasks.py [file] [--self-check]

校验内容：
    1. 逐条 schema（id 唯一性 / 必填字段 / category / severity / pass_k /
       expected_tools 结构 / no_tool 互斥 / reply_checks 四键 + 四占位符 +
       derived 派生断言键（k11-F 扩展）/ turns / state_checks / setup 已知键）
    2. 覆盖矩阵（category 分布表 / no_tool >= 10 / 链路任务 >= 5 / 总数 == 108）

退出码：
    0 = 全绿（含分布达标）
    1 = 有非法条目（逐条打印错误）
    2 = 分布不达标
    附 --self-check：复制文件并故意破坏 1 条，断言校验器报错（返回 0 = 自测通过）

schema 事实源：docs/superpowers/specs/2026-08-30-agent-eval-system-design.md 第四章（4.2/4.3/4.4/4.5）
"""
import argparse
import collections
import copy
import json
import re
import sys

CATEGORIES = [
    "paipan", "fortune", "zeri", "hehun", "xingming",
    "qian", "liuyao", "ziwei", "chat", "edge",
]
SEVERITIES = ["P0", "P1", "P2"]
# k11-F：reply_checks.derived 断言类型（执行见 scripts/eval_agent/l2_eval.py）
DERIVED_TYPES = ("age_claim", "dayun_claim", "gender_addr",
                 "shensha_refs", "tool_json")
MATCH_LEVELS = ["exact", "partial", "any"]
ROLES = ["user", "assistant"]
NEG_PLACEHOLDERS = ["{", "undefined", "NaN", "null"]
REQUIRED_KEYS = [
    "id", "title", "category", "severity", "pass_k", "source",
    "turns", "expected_tools", "no_tool", "reply_checks",
]
OPTIONAL_KEYS = ["setup", "state_checks", "judge_hint",
                 # k38：择日族双通道契约——部分引擎域任务同时存在「引擎意图
                 # 路径」与「LLM 工具路径」两条合法通道（T028/T038 实测），
                 # 本键显式声明「expected_tools=[] 时视为等价通过的 cap_id」。
                 "allow_tools"]
# setup 已知键（persons/favorites 为方案 4.2 原生；qian_saves/zeri_plans/chart_records/
# membership/chat_quota 为 E1 文档化扩展，见 2026-08-31-eval-set-annotation.md §3）
SETUP_KEYS = ["persons", "favorites", "qian_saves", "zeri_plans",
              "chart_records", "membership", "chat_quota"]
STATE_ACTION_RE = re.compile(r"^[a-z_]+_(created|unchanged|equals)$")

DIST_TARGETS = {
    "paipan": 12, "fortune": 12, "zeri": 10, "hehun": 6, "xingming": 6,
    "qian": 6, "liuyao": 6, "ziwei": 6, "chat": 10, "edge": 16,
}
MIN_NO_TOOL = 10
MIN_CHAIN = 5
# 100 基线 + k11-F 事实断言 T101-T103 + k15 评测尾巴 T104-T108
# （entity_qa_search 正例×2/负例 + hour_boundary 开关双行）
TOTAL_EXPECTED = 108

# 链路任务判定（4.4）：turns >= 2 且含 建档 -> 排盘 -> 测算 -> 收藏 四步
CHAIN_SETUP_RE = re.compile(r"199\d年|19[7-9]\d年|20\d\d年|出生|生辰")
CHAIN_PAIPAN_RE = re.compile(r"排.*盘|我的盘|八字|命盘")
CHAIN_SUAN_RE = re.compile(r"运势|财运|流年|流月|择日|吉日|灵签|合婚|摇卦|紫微|签|卦|日子")
CHAIN_SHOUCANG_RE = re.compile(r"收藏")


class ValidatorError(Exception):
    pass


def line_errs(line_no, errs, out):
    for e in errs:
        out.append("L%d %s" % (line_no, e))


def check_task(t, out):
    """对单条任务执行全部 schema 检查；错误写入 out。"""
    # 必填字段
    for k in REQUIRED_KEYS:
        if k not in t:
            out.append("[%s] 缺少必填字段 %s" % (t.get("id", "?"), k))
            return
    # id
    if not re.match(r"^T\d{3}$", t["id"]):
        out.append("[%s] id 必须形如 T001-T108" % t["id"])
    if not isinstance(t["title"], str) or not t["title"].strip():
        out.append("[%s] title 必须为非空字符串" % t["id"])
    # category
    if t["category"] not in CATEGORIES:
        out.append("[%s] category=%r 非法，合法值 %s" % (t["id"], t["category"], CATEGORIES))
    # severity
    if t["severity"] not in SEVERITIES:
        out.append("[%s] severity=%r 非法，合法值 %s" % (t["id"], t["severity"], SEVERITIES))
    # pass_k：整数，P0 必须为 3，其余必须为 1
    if not isinstance(t["pass_k"], int) or isinstance(t["pass_k"], bool):
        out.append("[%s] pass_k 必须为整数" % t["id"])
    else:
        expect = 3 if t["severity"] == "P0" else 1
        if t["pass_k"] != expect:
            out.append("[%s] pass_k=%d 应为 %d（P0=3，其余=1）" % (t["id"], t["pass_k"], expect))
    # source：非空且前缀合法
    if not isinstance(t["source"], str) or not t["source"].strip():
        out.append("[%s] source 必须为非空字符串" % t["id"])
    else:
        if not (t["source"].startswith("scenario-migrate") or t["source"].startswith("real-log")
                or t["source"].startswith("manual") or t["source"].startswith("bug-")):
            out.append("[%s] source=%r 前缀非法（scenario-migrate/real-log/manual/bug-*）" % (t["id"], t["source"]))
    # expected_tools / no_tool 互斥
    if not isinstance(t["no_tool"], bool):
        out.append("[%s] no_tool 必须为 bool" % t["id"])
    if not isinstance(t["expected_tools"], list):
        out.append("[%s] expected_tools 必须为数组" % t["id"])
    else:
        for i, et in enumerate(t["expected_tools"]):
            if not isinstance(et, dict) or not isinstance(et.get("name"), str) or not et["name"].strip():
                out.append("[%s] expected_tools[%d] 缺少非空 name" % (t["id"], i))
            if et.get("match") not in MATCH_LEVELS:
                out.append("[%s] expected_tools[%d].match=%r 非法（exact/partial/any）"
                           % (t["id"], i, et.get("match")))
            if "params" in et and not isinstance(et["params"], dict):
                out.append("[%s] expected_tools[%d].params 必须为对象" % (t["id"], i))
        if t["no_tool"] is True and len(t["expected_tools"]) > 0:
            out.append("[%s] no_tool=true 时 expected_tools 必须为空数组" % t["id"])
    # allow_tools（k38 择日族双通道契约，可选键）
    if "allow_tools" in t:
        at = t["allow_tools"]
        if not isinstance(at, list) or not all(isinstance(x, str) for x in at):
            out.append("[%s] allow_tools 必须为字符串数组" % t["id"])
        else:
            if not at:
                out.append("[%s] allow_tools 不得为空数组（空=不启用该契约）"
                           % t["id"])
            if len(t.get("expected_tools") or []) > 0:
                out.append("[%s] allow_tools 仅对 expected_tools=[] 的任务有意义"
                           "（非空期望走严格序列比对，禁止开旁路）" % t["id"])
            # k38-M4：与 no_tool 互斥——no_tool=true 是「期望零调用」反例桶，
            # 声明 allow_tools 会把该桶的判别力放开（现有 12 条 no_tool 反例
            # 任务将来可被顺手加键绕过）。
            if t.get("no_tool") is True:
                out.append("[%s] no_tool=true 时不得声明 allow_tools"
                           "（期望零调用桶禁止开旁路）" % t["id"])
    # reply_checks
    rc = t["reply_checks"]
    if not isinstance(rc, dict):
        out.append("[%s] reply_checks 必须为对象" % t["id"])
    else:
        for k in ["contains", "neg_checks", "regex", "min_len"]:
            if k not in rc:
                out.append("[%s] reply_checks 缺少键 %s" % (t["id"], k))
        for k in ["contains", "neg_checks", "regex"]:
            if k in rc and (not isinstance(rc[k], list) or not all(isinstance(x, str) for x in rc[k])):
                out.append("[%s] reply_checks.%s 必须为字符串数组" % (t["id"], k))
        if "min_len" in rc and (not isinstance(rc["min_len"], int) or rc["min_len"] < 0):
            out.append("[%s] reply_checks.min_len 必须为非负整数" % t["id"])
        if "neg_checks" in rc and isinstance(rc["neg_checks"], list):
            for ph in NEG_PLACEHOLDERS:
                if ph not in rc["neg_checks"]:
                    out.append("[%s] neg_checks 缺少占位符 %r（{/undefined/NaN/null 四件套）" % (t["id"], ph))
        # k11-F：reply_checks.derived 可选键（派生数值断言，L2 纯规则层执行）
        # 元素 = {type, params?}；type ∈ DERIVED_TYPES。age_claim/dayun_claim/
        # gender_addr/shensha_refs 需要 setup.persons 出生档案做运行时引擎派生
        # 事实源（tool_json 不需要）。schema 校验器只查结构，执行在 l2_eval。
        if "derived" in rc:
            if not isinstance(rc["derived"], list):
                out.append("[%s] reply_checks.derived 必须为数组" % t["id"])
            else:
                need_persons = ("age_claim", "dayun_claim", "gender_addr",
                                "shensha_refs")
                has_persons = bool((t.get("setup") or {}).get("persons"))
                for i, dv in enumerate(rc["derived"]):
                    if not isinstance(dv, dict) or not isinstance(
                            dv.get("type"), str):
                        out.append("[%s] derived[%d] 必须为含 type 的对象"
                                   % (t["id"], i))
                        continue
                    if dv["type"] not in DERIVED_TYPES:
                        out.append("[%s] derived[%d].type=%r 非法（%s）"
                                   % (t["id"], i, dv["type"], DERIVED_TYPES))
                    if "params" in dv and not isinstance(dv["params"], dict):
                        out.append("[%s] derived[%d].params 必须为对象"
                                   % (t["id"], i))
                    if dv["type"] in need_persons and not has_persons:
                        out.append("[%s] derived[%d].type=%s 需要 setup.persons"
                                   "（运行时引擎派生事实源）" % (t["id"], i,
                                                              dv["type"]))
    # turns
    if not isinstance(t["turns"], list) or len(t["turns"]) == 0:
        out.append("[%s] turns 必须为非空数组" % t["id"])
    else:
        for i, turn in enumerate(t["turns"]):
            if not isinstance(turn, dict) or turn.get("role") not in ROLES:
                out.append("[%s] turns[%d] role 非法（user/assistant）" % (t["id"], i))
            if not isinstance(turn.get("text"), str):
                out.append("[%s] turns[%d].text 必须为字符串" % (t["id"], i))
    # state_checks
    sc = t.get("state_checks")
    if sc is not None:
        if not isinstance(sc, dict):
            out.append("[%s] state_checks 必须为对象" % t["id"])
        else:
            for k, v in sc.items():
                if not STATE_ACTION_RE.match(k):
                    out.append("[%s] state_checks 键 %r 非法（须 <表>_created/_unchanged/_equals）" % (t["id"], k))
                # 值契约（E6 放宽，task-E5-report.md concern #3 落地）：created/
                # unchanged 为 bool；_equals 支持 int（行数精确相等，与
                # state_verifier._equals 契约对齐——bool 为校验器兼容退化语义，
                # int 为正式数值断言；评估集当前 0 条使用，schema 完备性保留）
                if not isinstance(v, bool) and not (
                        k.endswith("_equals") and isinstance(v, int)):
                    out.append("[%s] state_checks.%s 值必须为 bool"
                               "（_equals 可为 int 行数）" % (t["id"], k))
    # setup 已知键
    st = t.get("setup")
    if st is not None:
        if not isinstance(st, dict):
            out.append("[%s] setup 必须为对象" % t["id"])
        else:
            for k in st:
                if k not in SETUP_KEYS:
                    out.append("[%s] setup 键 %r 非契约键 %s（新增键须同步标注文档）" % (t["id"], k, SETUP_KEYS))
            if "persons" in st:
                if not isinstance(st["persons"], list):
                    out.append("[%s] setup.persons 必须为数组" % t["id"])
                else:
                    for p in st["persons"]:
                        if not isinstance(p, dict):
                            out.append("[%s] setup.persons 元素必须为对象" % t["id"])
                            continue
                        if p.get("gender") not in ("male", "female"):
                            out.append("[%s] setup.persons.gender 非法（male/female）" % t["id"])
                        for k in ("name", "birth", "city"):
                            if k in p and not isinstance(p[k], str):
                                out.append("[%s] setup.persons.%s 必须为字符串" % (t["id"], k))
                        # k11c/k15：persons.solar_time 真太阳时档案开关（可选键；
                        # 缺省=默认开语义）。合法值 int 0/1（0=关 显式携带）；
                        # 布尔/字符串/越界值 → schema 错误（种子注入歧义）
                        if "solar_time" in p and not (
                                isinstance(p["solar_time"], int)
                                and not isinstance(p["solar_time"], bool)
                                and p["solar_time"] in (0, 1)):
                            out.append("[%s] setup.persons.solar_time 必须为 int 0/1"
                                       % (t["id"],))
            for k in ("favorites", "qian_saves", "zeri_plans", "chart_records"):
                if k in st and not isinstance(st[k], list):
                    out.append("[%s] setup.%s 必须为数组" % (t["id"], k))
            for k in ("membership", "chat_quota"):
                if k in st and not isinstance(st[k], dict):
                    out.append("[%s] setup.%s 必须为对象" % (t["id"], k))
    # judge_hint
    if "judge_hint" in t and (not isinstance(t["judge_hint"], str) or not t["judge_hint"].strip()):
        out.append("[%s] judge_hint 必须为非空字符串" % t["id"])


def is_chain_task(t):
    """链路任务：turns >= 2 且含 建档 -> 排盘 -> 测算 -> 收藏 四步。"""
    if len(t.get("turns", [])) < 2:
        return False
    texts = [turn.get("text", "") for turn in t["turns"]]
    setup_hit = False
    st = t.get("setup") or {}
    if st.get("persons") or st.get("chart_records"):
        setup_hit = True
    else:
        for text in texts:
            if CHAIN_SETUP_RE.search(text):
                setup_hit = True
                break
    return (setup_hit
            and any(CHAIN_PAIPAN_RE.search(x) for x in texts)
            and any(CHAIN_SUAN_RE.search(x) for x in texts)
            and any(CHAIN_SHOUCANG_RE.search(x) for x in texts))


def validate_file(path):
    """返回 (errors, tasks)。errors 为空 => 逐条合法。"""
    errors, tasks = [], []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                t = json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append("L%d JSON 解析失败: %s" % (line_no, e))
                continue
            if not isinstance(t, dict):
                errors.append("L%d 条目必须为 JSON 对象" % line_no)
                continue
            line_errs(line_no, [], errors)  # no-op keep structure
            check_task(t, errors)
            tasks.append(t)
    return errors, tasks


def coverage_errors(tasks):
    """覆盖矩阵校验，返回错误列表 + 统计字典。"""
    errs = []
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        dup = [i for i, c in collections.Counter(ids).items() if c > 1]
        errs.append("id 不唯一: %s" % dup)
    counter = collections.Counter(t["category"] for t in tasks)
    for cat, target in DIST_TARGETS.items():
        if counter[cat] < target:
            errs.append("category=%s 共 %d 条 < 目标 %d" % (cat, counter[cat], target))
    no_tool_cnt = sum(1 for t in tasks if t.get("no_tool") is True)
    if no_tool_cnt < MIN_NO_TOOL:
        errs.append("no_tool=true 共 %d 条 < 目标 %d" % (no_tool_cnt, MIN_NO_TOOL))
    chain = [t["id"] for t in tasks if is_chain_task(t)]
    if len(chain) < MIN_CHAIN:
        errs.append("链路任务共 %d 条 < 目标 %d" % (len(chain), MIN_CHAIN))
    if len(tasks) != TOTAL_EXPECTED:
        errs.append("任务总数 %d != %d" % (len(tasks), TOTAL_EXPECTED))
    stats = {
        "total": len(tasks),
        "categories": dict(sorted(counter.items())),
        "no_tool": no_tool_cnt,
        "chain_tasks": chain,
        "chain_count": len(chain),
    }
    return errs, stats


def print_stats(stats):
    print("== 覆盖矩阵统计 ==")
    print("总任务数: %d (目标 %d)" % (stats["total"], TOTAL_EXPECTED))
    print("category 分布:")
    for cat, cnt in stats["categories"].items():
        print("  %-10s %d" % (cat, cnt))
    print("no_tool=true: %d (目标 >= %d)" % (stats["no_tool"], MIN_NO_TOOL))
    print("链路任务: %d (目标 >= %d) %s" % (stats["chain_count"], MIN_CHAIN, stats["chain_tasks"]))


def run_self_check(path):
    """破坏 1 条任务，断言校验器报错。返回 (ok, message)。"""
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    broken = copy.deepcopy(lines)
    # 破坏：把第一条任务的 neg_checks 删掉四占位符之一 + 置 category 非法
    t = json.loads(broken[0])
    t["neg_checks"] = ["foo"]
    t["category"] = "hack"
    broken[0] = json.dumps(t, ensure_ascii=False)
    tmp = path + ".broken"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(broken) + "\n")
    errs, _ = validate_file(tmp)
    import os
    os.remove(tmp)
    if errs:
        return True, "自测通过：破坏集被检出 %d 处错误（示例: %s）" % (len(errs), errs[0])
    return False, "自测失败：破坏集未检出任何错误"


def main():
    ap = argparse.ArgumentParser(description="E1 评估集 schema 校验器")
    ap.add_argument("file", nargs="?", default="data/eval/agent_tasks.jsonl")
    ap.add_argument("--self-check", action="store_true", help="自测模式：故意破坏 1 条验证报错")
    args = ap.parse_args()

    if args.self_check:
        ok, msg = run_self_check(args.file)
        print("[self-check] " + msg)
        # 再对正常集跑一遍，确认全绿基线
        errs, tasks = validate_file(args.file)
        _, stats = coverage_errors(tasks)
        if errs:
            print("自测基线异常：正常集出现错误：")
            for e in errs[:5]:
                print("  " + e)
            sys.exit(1)
        print("[self-check] 正常集基线 OK (%d 条)" % stats["total"])
        sys.exit(0 if ok else 1)

    errs, tasks = validate_file(args.file)
    for e in errs:
        print("ERROR: " + e)
    if errs:
        print("共 %d 处条目级错误（schema 未达标）" % len(errs))
        sys.exit(1)

    cerrs, stats = coverage_errors(tasks)
    print_stats(stats)
    if cerrs:
        for e in cerrs:
            print("DIST-ERROR: " + e)
        print("分布未达标（exit 2）")
        sys.exit(2)

    print("ALL GREEN: %d 条全部通过 schema + 覆盖矩阵校验" % stats["total"])
    sys.exit(0)


if __name__ == "__main__":
    main()
