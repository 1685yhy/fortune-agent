#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 最小 CLI（E3）：回复内容确定性断言层（纯脚本，零 LLM 判读）。

用法：
  python3 scripts/eval_agent/l2_eval.py                    # 默认冒烟子集
  python3 scripts/eval_agent/l2_eval.py --tasks T001,T017  # 指定任务
  python3 scripts/eval_agent/l2_eval.py --category fortune # 指定域
  python3 scripts/eval_agent/l2_eval.py --all              # 全量 108 条
  python3 scripts/eval_agent/l2_eval.py --model deepseek   # 生产模型路由

退出码：0 = 达标（断言通过率 = 100%，spec §5.2 硬门禁）/
        1 = 有断言失败 / 2 = 评估集校验失败或前置失败。

L2 测什么（spec §5.2）：回复的硬性质量——该说的说了 / 不该说的没说 /
无乱码 / 无假成功：
  1. reply_checks 四键断言（评估集契约逐项一致）：
     - contains: 全部在（空数组 = 无正断言，恒过）
     - neg_checks: 任一在 = 失败（含 {/undefined/NaN/null 四占位符、
       Traceback/服务器内部错误 等假数据与假成功字样）
     - regex: 任一命中（空数组 = 无正则要求，恒过——T083/T086 regex=[] 契约）
     - min_len: len(回复) >= min_len
  2. 多轮场景：各轮回复两两互异（防复读机）；标注指定「两轮相等」
     （judge_hint，T024 缓存命中回归）则断言两两相等——以标注文档为准
  3. 攻击场景（state_checks `*_unchanged` 标注，含全部 edge 攻击任务
     T083/T086/T087/T088/T100）：跑完查 persons/sessions/favorites/
     chart_records/memberships 表行数与跑前一致（防写入）
  4. 指标：断言通过率 = 全部断言通过任务数 / 已执行任务数；回归 = 100%
     （硬门禁，任何一条不通过 = 阻止合并）

红线（违反即返工）：
- L2 零 LLM 判读（那是 L3 judge 的事；LLM 只作为被测主链的一部分产出回复）
- src/bot/tool_calls.py 零改动（拦截在测试侧 interceptor.py，生产零改动）
- 隔离库方案：复用 l1_eval 骨架——复制真实库 → 每任务临时库（镜像
  scripts/verify_qa_scenarios.py），原始库零写入；USER_MEMORY_DIR/CHARTS_DIR
  重定向临时目录，data/memory/ 零触碰
- key 只读环境变量（ZHIPU_API_KEY / DEEPSEEK_API_KEY），零落盘
- 零新增依赖（纯标准库 + 仓库既有模块 l1_eval/interceptor）
- data/eval/agent_tasks.jsonl 只读（唯一事实源）；结果落 data/eval/results/

模块导入零副作用：l1_eval 的 src 导入/环境变量/模型路由 patch 全部在其
_init_runtime() 内懒执行——本模块 import 亦无副作用，便于单测直接 import
断言纯函数。
"""
import argparse
import concurrent.futures
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (_REPO, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import l1_eval  # noqa: E402  — 复用骨架：隔离库/种子注入/主链装配/落盘/退出码
from interceptor import ToolCallRecorder  # noqa: E402

TASKS_PATH = l1_eval.TASKS_PATH
VALIDATOR = l1_eval.VALIDATOR
RESULTS_ROOT = l1_eval.RESULTS_ROOT
TASK_TIMEOUT = l1_eval.TASK_TIMEOUT

# 冒烟子集工具域正例切片（E2 Important #2 遗留：expected_tools 非空且回复
# 含真实工具结果的样本——L2 冒烟必须覆盖「工具调用结果进回复」路径）
TOOL_SLICE = ["T017", "T018", "T026", "T027", "T039", "T045"]

# 后置库状态断言表映射（state_checks `*_unchanged` 键 → 真实表名；
# chat 历史表 = sessions，事实源 src/storage/models.py）
STATE_TABLE = {
    "persons": "persons",
    "chat": "sessions",
    "favorites": "favorites",
    "chart_records": "chart_records",
    "memberships": "memberships",
    "qian_saves": "qian_saves",
    "zeri_plans": "zeri_plans",
    "memories": "session_summaries",
}

# L2 门禁（spec §5.2）：回归 = 100%，硬门禁
GATE_PASS_RATE = 1.0

# 多轮「两轮相等」模式触发词（标注文档 §11.4 契约：T024 缓存命中两轮相等；
# 只认精确短语，避免 T090「多轮一致」语义一致性误触发）
_EQUAL_HINT_MARKERS = ("两轮相等", "回复与首轮一致")


# ================================================================
# 断言引擎（纯函数，零 LLM；供单测直接 import）
# ================================================================

def eval_reply_checks(task: dict, reply: str) -> list:
    """reply_checks 四键断言（与评估集契约逐项一致）。

    语义（annotation §2）：contains 必须全部在 / neg_checks 任一在即失败 /
    regex 任一命中 / min_len 回复长度下限。空数组均为「无该项要求」恒过
    （T083/T086 的 regex=[]、T017 系 contains=[] 契约）。
    """
    rc = task.get("reply_checks") or {}
    out = []
    for s in rc.get("contains") or []:
        ok = s in reply
        out.append({"name": "contains", "expect": s, "ok": ok,
                    "detail": "" if ok else f"回复未含关键内容: {s!r}"})
    for s in rc.get("neg_checks") or []:
        hit = s in reply
        out.append({"name": "neg_checks", "expect": s, "ok": not hit,
                    "detail": "" if not hit else f"不应出现却出现: {s!r}"})
    pats = rc.get("regex") or []
    if pats:
        hit_pat = next((p for p in pats if re.search(p, reply)), None)
        out.append({"name": "regex", "expect": pats, "ok": hit_pat is not None,
                    "detail": "" if hit_pat is not None else
                              f"正则全部未命中: {pats}"})
    else:
        out.append({"name": "regex", "expect": [], "ok": True, "detail": ""})
    ml = rc.get("min_len")
    if ml is not None:
        ok = len(reply) >= ml
        out.append({"name": "min_len", "expect": ml, "ok": ok,
                    "detail": "" if ok else
                              f"回复长度 {len(reply)} < 要求 {ml}"})
    return out


# ================================================================
# k11-F：派生数值断言（reply_checks.derived）——纯规则零 LLM。
# 事实源 = setup.persons[0] 出生 → BaziEngine 本地复算（与主链同引擎同口径）；
# 运行时派生 age/dayun/gender/shensha，断言回复中的"当前年龄声明/当前大运声明/
# 男性称谓/神煞引用/工具 JSON 泄漏"。报告 §③-6 六条用例中 entity_qa_search
# （待 k11b 搜索通道）、hour_boundary（待 k11c 口径拍板）未挂任务行。
# ================================================================

# 当前年龄声明式表述（前缀式；"岁"裸数字规则用标点前瞻排除大运表 3/13/23/33 岁段）
# k15：双单位变体「虚岁23岁到32岁。」类（单位前缀型 + 区间/段连接词）——
# review r1-2 Minor（k11b 复审记录）：pattern2 仍对窗口外端点 23/32 误报。
# 修法=前后双向排除段连接语境：左侧排除「…到虚岁32岁。」型（前置 到/至/走…），
# 右侧排除「虚岁23岁到32岁/至/起/走/换/进/交/止/后/时」与区间符（～~--）型，
# 均属大运段端点；真实当前年龄声明（句首/句读收尾的 虚岁28岁。）不受影响。
# k35-A4：右侧排除补「时」——「虚岁33岁时进入乙丑大运」类换运**时点**句曾被
# pattern2 误拦（裸「岁」后接「时」不在排除面内）；同族完成体 pattern（下条
# :159-160 右侧同位置已含「时」）为对齐事实源。语义边界不变：「时」只在
# 「虚岁N岁时…」这一时点限定语境放行，N 仍被前缀型/完成体/裸岁句读型覆盖。
_AGE_CLAIM_RES = (
    re.compile(r"(?:今年|现在|如今|目前|本人|我已经|我今年|命主|用户|你今年|你现在)"
               r"\s*(?:周岁|虚岁)?\s*(\d{1,2})\s*岁"),
    re.compile(r"(?<![\d岁到从走换进交止起至后])(?:周岁|虚岁)\s*(\d{1,2})\s*岁"
               r"(?![\d到至起走换进交止后时～~\-–—－])"),
    # k17-6（k15 审查 Minor-2 记录补拦）：岁字省略·完成体收尾型——「虚岁33了/
    # 啦/吧」类无「岁」的完成体断言只可能是当前年龄声明（事故句形态），排除面
    # 镜像 pattern2（左 到/从/走/换/进/交/止/起/至/后，右 数字/段连接词/时/
    # 区间符），换运端点句（虚岁23到32岁/从虚岁33起走乙丑/虚岁33时换入/虚岁33
    # 交运）不裸捕；残余误拦族 = 反问/引用否定句「你虚岁33了？…」——k15 review
    # 已记录的既有误拦族（岁字版同误拦）同形态延伸，非新类，注明在案
    re.compile(r"(?<![\d岁到从走换进交止起至后])(?:周岁|虚岁)\s*(\d{1,2})"
               r"(?![\d到至起走换进交止后时～~\-–—－])(?:了|啦|哈|吧|嘛)"),
    # k17-6：岁字省略·声明前缀型——「今年/本人/命主…（可夹 我/你/他/她）+
    # 虚岁/周岁 N + 句读/行尾」（「今年我虚岁33。」）；前缀即当前年龄声明语域，
    # 句读收尾排除「虚岁33时/开始走乙丑」类事件表述（数字后非句读不命中）
    re.compile(r"(?:今年|现在|如今|目前|本人|我已经|我今年|命主|用户|你今年|你现在)"
               r"\s*(?:我|你|他|她)?\s*(?:周岁|虚岁)\s*(\d{1,2})"
               r"(?=[，。！？；、,.!?]|$)"),
    # "28虚岁/27周岁"（单位后置口语）——大运表形如 "23岁丙寅"，不会命中本型
    re.compile(r"(?<![\d岁至→>])(\d{1,2})\s*(?:周岁|虚岁)"),
    # 裸 "27岁。"（数字+岁+句读）——review r1-2（Important）：前置排除须覆盖
    # 大运段端点语境（到/从/至/走/换/止/起/进/交 + 区间连字符 -–—～~），否则
    # 「丙寅运走到32岁，」「从23岁到32岁，」「23-32岁。」「虚岁23岁～32岁。」
    # 类真实合格回复被误报（～~ 为 r1-2 后补区间符，k15 补齐句读收尾的
    # 「23～32岁。」端点，见测试复现 ③）
    re.compile(r"(?<![\d岁至→>到从走换止起进交\-–—～~])(\d{1,2})\s*岁"
               r"(?=[，。！？；、,.!?\s]|$)"),
)
_DAYUN_CURRENT_RES = (
    re.compile(r"(?:当前|现在|目前|今年)\s*(?:走|行|处(?:于|在)|正走|进入?)?"
               r"\s*(?:大运|运程)\s*[^，。\n]{0,6}?"
               r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])"),
    re.compile(r"刚\s*进(?:入)?\s*"
               r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])"),
)
_TOOL_JSON_LEAK_RES = (
    "<tool_calls", "<tool_call", "[{\"tool\"", "{\"tool\"", "\\\"tool\\\"",
)


def derive_facts(task: dict) -> Optional[dict]:
    """从 setup.persons[0] 出生档案派生命理事实（BaziEngine 本地复算，零 LLM）。

    setup 无 persons → None（derived 断言 vacuous 跳过，validator 已前置要求
    persons）。engine 异常/旧数据 → None。口径与主链一致（同引擎同 now），
    同一次评测运行内回复与断言不会因时钟漂移失配。
    """
    try:
        persons = (task.get("setup") or {}).get("persons") or []
        if not persons:
            return None
        p = persons[0]
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2}))?",
                     str(p.get("birth") or ""))
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h = int(m.group(4) or 0)
        mi = int(m.group(5) or 0)
        from src.engines.bazi import BaziEngine
        r = BaziEngine().calculate(
            y, mo, d, h, mi, str(p.get("city") or "北京"),
            str(p.get("gender") or "unknown"))
        cs = getattr(r, "current_stage", None) or {}
        facts = {
            "gender": getattr(r, "gender", "") or "unknown",
            "shensha": list(getattr(r, "shensha", None) or []),
            "age_zhousui": cs.get("age_zhousui"),
            "age_xusui": cs.get("age_xusui"),
            "dayun_ganzhi": cs.get("dayun_ganzhi"),
            "dayun_sui_start": cs.get("dayun_sui_start"),
        }
        return facts
    except Exception:
        return None


def _age_claim_check(reply: str, facts: dict, spec: dict) -> dict:
    lo, hi = facts.get("age_zhousui"), facts.get("age_xusui")
    if lo is None or hi is None:
        return {"name": "derived.age_claim", "expect": "facts", "ok": True,
                "detail": "派生事实缺失，跳过"}
    window = (lo - 1, hi + 1)
    claims = []
    for rx in _AGE_CLAIM_RES:
        claims += [int(n) for n in rx.findall(reply) if n]
    claims = list(dict.fromkeys(c for c in claims if 3 <= c <= 120))
    bad = [n for n in claims if not (window[0] <= n <= window[1])]
    want = spec.get("params") or {}
    if bad:
        return {"name": "derived.age_claim", "expect": f"当前年龄∈[{window[0]},{window[1]}]",
                "ok": False,
                "detail": f"当前年龄声明超出窗口: {bad} 岁（档案实龄 {lo} 周岁/"
                          f"{hi} 虚岁）——把换运岁数当当前年龄属 k11 事故同类"}
    if want.get("require_mention") and not any(
            window[0] <= c <= window[1] for c in claims):
        return {"name": "derived.age_claim", "expect": "回复提及当前年龄",
                "ok": False,
                "detail": f"问题询问年龄但回复未出现 {lo}/{hi} 岁表述"}
    return {"name": "derived.age_claim", "expect": f"无窗口外年龄声明[{window[0]},{window[1]}]",
            "ok": True, "detail": ""}


def _dayun_claim_check(reply: str, facts: dict, spec: dict) -> dict:
    cur = facts.get("dayun_ganzhi")
    if not cur:
        return {"name": "derived.dayun_claim", "expect": "facts", "ok": True,
                "detail": "派生事实缺失，跳过"}
    hits = []
    for rx in _DAYUN_CURRENT_RES:
        for gz in rx.findall(reply):
            if gz != cur:
                hits.append(gz)
    if hits:
        return {"name": "derived.dayun_claim",
                "expect": f"当前大运={cur}",
                "ok": False,
                "detail": f"回复把当前大运说成 {hits}（引擎当前段为 {cur}）"}
    return {"name": "derived.dayun_claim", "expect": f"当前大运={cur}",
            "ok": True, "detail": ""}


def _gender_addr_check(reply: str, facts: dict, spec: dict) -> dict:
    g = facts.get("gender")
    if g == "女":
        return {"name": "derived.gender_addr", "expect": "女命放行", "ok": True,
                "detail": ""}
    try:
        from src.utils.fact_guard import FEMALE_ADDRESS_TERMS
    except Exception:
        return {"name": "derived.gender_addr", "expect": "词表", "ok": True,
                "detail": "词表加载失败跳过"}
    hits = [t for t in FEMALE_ADDRESS_TERMS if t in reply]
    if hits:
        return {"name": "derived.gender_addr",
                "expect": "男/未知命无女性称谓",
                "ok": False,
                "detail": f"回复含女性称谓词: {hits}"}
    return {"name": "derived.gender_addr", "expect": "男/未知命无女性称谓",
            "ok": True, "detail": ""}


def _shensha_refs_check(reply: str, facts: dict, spec: dict) -> dict:
    allow = set(facts.get("shensha") or ())
    if not allow:
        return {"name": "derived.shensha_refs", "expect": "facts", "ok": True,
                "detail": "本盘神煞全集缺失，跳过"}
    try:
        from src.utils.fact_guard import shensha_lexicon
        lex = shensha_lexicon()
    except Exception:
        lex = ()
    if not lex:
        return {"name": "derived.shensha_refs", "expect": "词典", "ok": True,
                "detail": "词典加载失败跳过"}
    hits = [w for w in lex if w not in allow and w in reply]
    if hits:
        return {"name": "derived.shensha_refs",
                "expect": f"只允许引用本盘神煞（{len(allow)} 个）",
                "ok": False,
                "detail": f"回复引用了白名单外神煞: {hits[:8]}"}
    return {"name": "derived.shensha_refs", "expect": "无白名单外神煞",
            "ok": True, "detail": ""}


def _tool_json_check(reply: str, facts: dict, spec: dict) -> dict:
    hits = [s for s in _TOOL_JSON_LEAK_RES if s in reply]
    if hits:
        return {"name": "derived.tool_json", "expect": "回复不含工具 JSON",
                "ok": False, "detail": f"工具 JSON/标签残留: {hits[:5]}"}
    return {"name": "derived.tool_json", "expect": "回复不含工具 JSON",
            "ok": True, "detail": ""}


_DERIVED_CHECKERS = {
    "age_claim": _age_claim_check,
    "dayun_claim": _dayun_claim_check,
    "gender_addr": _gender_addr_check,
    "shensha_refs": _shensha_refs_check,
    "tool_json": _tool_json_check,
}


def eval_derived_checks(task: dict, reply: str, facts: dict = None) -> list:
    """reply_checks.derived 断言（k11-F）：逐条纯规则判定，零 LLM。"""
    rc = task.get("reply_checks") or {}
    specs = rc.get("derived") or []
    if not specs:
        return []
    if facts is None:
        facts = derive_facts(task)
    out = []
    for spec in specs:
        fn = _DERIVED_CHECKERS.get(spec.get("type"))
        if fn is None:
            out.append({"name": "derived.unknown", "expect": spec.get("type"),
                        "ok": False, "detail": f"未知 derived type: {spec}"})
            continue
        try:
            out.append(fn(reply, facts or {}, spec))
        except Exception as e:  # noqa: BLE001 — 断言自身异常视为失败（宁可显式）
            out.append({"name": "derived." + str(spec.get("type")),
                        "expect": spec.get("params"), "ok": False,
                        "detail": f"断言执行异常: {type(e).__name__}: {str(e)[:120]}"})
    return out


def multi_turn_mode(task: dict) -> str:
    """跨轮断言模式（以标注文档为准，annotation §11.4）。

    judge_hint 含「两轮相等/回复与首轮一致」（T024 缓存命中回归）→ equal；
    否则（含「互异/内容不同」或无标注）→ distinct（默认防复读机）。
    """
    hint = task.get("judge_hint") or ""
    if any(m in hint for m in _EQUAL_HINT_MARKERS):
        return "equal"
    return "distinct"


def eval_multi_turn(task: dict, replies: list) -> list:
    """turns≥2：各轮回复两两互异（防串轮复读）；equal 模式两两相等（T024）。"""
    if len(replies) < 2:
        return []
    mode = multi_turn_mode(task)
    pairs = [(i, j) for i in range(len(replies))
             for j in range(i + 1, len(replies))]
    if mode == "equal":
        bad = [(i, j) for i, j in pairs if replies[i] != replies[j]]
    else:
        bad = [(i, j) for i, j in pairs if replies[i] == replies[j]]
    label = ("各轮回复两两相等（T024 缓存命中契约）" if mode == "equal"
             else "各轮回复两两互异（防串轮复读）")
    why = "回复不同" if mode == "equal" else "回复完全相同（疑似串轮复读）"
    return [{"name": "multi_turn", "expect": mode, "ok": not bad,
             "detail": "" if not bad else f"{label} 违反: 轮 {bad} {why}"}]


def _table_count(db_path: str, table: str) -> int:
    """表行数。表不存在 = 0 行（无表视为 0；链跑后建表出现行 = 写入被检测）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return 0
        raise
    finally:
        con.close()


def snapshot_unchanged(db_path: str, state_checks: dict) -> dict:
    """state_checks `*_unchanged` 键 → {state_key: 行数}（跑前基线，种子注入后）。

    未知表映射的键 → None（比对时显式失败，不静默忽略）。
    """
    out = {}
    for key, want in (state_checks or {}).items():
        if not key.endswith("_unchanged") or want is not True:
            continue
        table = STATE_TABLE.get(key[: -len("_unchanged")])
        out[key] = (None if table is None
                    else _table_count(db_path, table))
    return out


def eval_state_unchanged(state_checks: dict, before: dict, after: dict) -> list:
    """攻击后置断言（防写入）：跑完查库行数与跑前一致。"""
    out = []
    for key in sorted(before):
        table = STATE_TABLE.get(key[: -len("_unchanged")])
        b, a = before[key], after.get(key)
        if table is None or b is None:
            out.append({"name": "state_unchanged", "expect": key, "ok": False,
                        "detail": f"未知表映射: {key}"})
            continue
        ok = a == b
        out.append({"name": "state_unchanged", "expect": key, "ok": ok,
                    "detail": "" if ok else
                              f"{key}: {table} 行数 {b} → {a}（攻击写入被检测）"})
    return out


# ================================================================
# 指标与门禁（纯函数）
# ================================================================

def aggregate_metrics(results: list) -> dict:
    """L2 指标：断言通过率 = 全部断言通过任务数 / 已执行任务数。

    跳过条目（种子注入失败等）不进分母，显式列在 skipped（同 E1 契约）。
    """
    executed = [r for r in results if not r["skipped"]]
    n = len(executed)
    passed = [r for r in executed if r["ok"]]
    return {
        "total": len(results),
        "executed": n,
        "skipped": [r["id"] for r in results if r["skipped"]],
        "assertion_pass_rate": (len(passed) / n) if n else 0.0,
        "passed": len(passed),
        "failed": [r["id"] for r in executed if not r["ok"]],
        "exceptions": [r["id"] for r in executed if r["exception"]],
    }


def thresholds_met(metrics: dict) -> bool:
    """L2 门禁（spec §5.2）：回归 = 100%，任何一条不通过 = 阻止合并。"""
    if metrics["executed"] == 0:
        return False
    return metrics["assertion_pass_rate"] >= GATE_PASS_RATE


# ================================================================
# 运行期（复用 l1_eval 骨架；本模块 import 零副作用）
# ================================================================

def _run_one_task(task: dict, R: dict, model_route: str, keep_tmp: bool) -> dict:
    """黑盒跑单任务：临时库 → 种子 → unchanged 基线快照 → 同会话逐轮 process()
    → 收集逐轮回复 → reply_checks + 跨轮互异 + 攻击后置库状态断言。"""
    tid = task["id"]
    user_id = f"eval_{tid}"
    result = {
        "id": tid, "category": task["category"], "severity": task["severity"],
        "title": task["title"], "no_tool": bool(task.get("no_tool", False)),
        "turns": len(task["turns"]), "skipped": False, "skip_reason": "",
        "ok": False, "checks": [], "replies": [], "full_reply": "",
        "actual_by_turn": [], "actual_calls": [],
        "exception": None, "elapsed": 0.0,
    }
    tdir = Path(tempfile.mkdtemp(prefix=f"{tid}-", dir=str(R["tmp_root"])))
    try:
        db_path = l1_eval.seed_db_copy(R["settings"].db_path, tdir)
    except Exception as e:  # noqa: BLE001
        result["skipped"] = True
        result["skip_reason"] = f"隔离库复制失败: {type(e).__name__}: {str(e)[:120]}"
        return result
    seed_err = l1_eval.seed_task_setup(str(db_path), user_id,
                                       task.get("setup") or {})
    if seed_err is not None:
        result["skipped"] = True
        result["skip_reason"] = f"种子注入失败: {seed_err}"
        return result

    state_checks = task.get("state_checks") or {}
    before = snapshot_unchanged(str(db_path), state_checks)
    recorder = ToolCallRecorder()
    t0 = time.monotonic()
    try:
        with recorder:
            recorder.install()  # 默认目标 MessageHandler（context 兜底恢复）
            handler = R["build_handler"](db_path, model_route)

            def _run_chain():
                replies = []
                for i, turn in enumerate(task["turns"]):
                    recorder.begin_turn()
                    text = (turn.get("text") or "").strip()
                    replies.append(
                        handler.process(text, user_id,
                                        session_id=f"eval-{tid}") or "")
                return replies

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                replies = ex.submit(_run_chain).result(timeout=TASK_TIMEOUT)
    except concurrent.futures.TimeoutError:
        replies = []
        result["exception"] = f"任务超时（>{TASK_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001 — 主链异常也要落报告
        replies = []
        result["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        result["elapsed"] = time.monotonic() - t0

    result["actual_by_turn"] = recorder.turns()
    result["actual_calls"] = recorder.flat_calls()
    result["replies"] = [r[:2000] for r in replies]
    full = "\n".join(replies)
    result["full_reply"] = full[:4000]

    checks = []
    if result["exception"]:
        checks.append({"name": "无异常", "expect": "", "ok": False,
                       "detail": f"异常: {result['exception']}"})
    else:
        checks += eval_reply_checks(task, full)
        # k11-F：派生数值断言（age/dayun/gender/shensha/tool_json）——事实源
        # 运行时引擎复算（setup.persons），纯规则零 LLM
        checks += eval_derived_checks(task, full)
        checks += eval_multi_turn(task, replies)
        if before:
            after = snapshot_unchanged(str(db_path), state_checks)
            checks += eval_state_unchanged(state_checks, before, after)
    result["checks"] = checks
    result["ok"] = all(c["ok"] for c in checks)
    return result


def run_eval(tasks: list, out_dir: Path, model_route: str = "glm",
             keep_tmp: bool = False) -> dict:
    """跑指定任务集并落盘报告（meta.json + results.json + report.md）。"""
    R = l1_eval._init_runtime(model_route)
    if not keep_tmp:
        l1_eval.atexit_register_cleanup(R["tmp_root"])
    results = []
    for task in tasks:
        r = _run_one_task(task, R, model_route, keep_tmp)
        results.append(r)
        _print_task_line(r)
    metrics = aggregate_metrics(results)
    meta = {
        "layer": "L2",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "model": model_route,
        "repo_version": _git_head(),
        "scope": [t["id"] for t in tasks],
        "eval_set": "data/eval/agent_tasks.jsonl (E1, 唯一事实源，只读)",
        "gate": {"assertion_pass_rate": GATE_PASS_RATE},
        "metrics": metrics,
        "thresholds_met": thresholds_met(metrics),
        "skeleton_reuse": "l1_eval（E2）：隔离库复制/种子注入/主链装配/"
                          "拦截/落盘/退出码契约全量复用，仅改断言对象=回复内容",
        "llm_judging": "无（L2 纯脚本确定性断言；L3 judge 才用 LLM）",
        "isolation": "每任务复制真实库→临时库 + USER_MEMORY_DIR/CHARTS_DIR 重定向",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        render_report_md(meta, metrics, results), encoding="utf-8")
    print(f"\n结果已写入: {out_dir}")
    return {"meta": meta, "metrics": metrics, "results": results}


def _print_task_line(r: dict):
    if r["skipped"]:
        print(f"[SKIP] {r['id']} {r['title']} —— {r['skip_reason']}")
        return
    status = "PASS" if r["ok"] else "FAIL"
    nfail = sum(1 for c in r["checks"] if not c["ok"])
    print(f"[{status}] {r['id']} {r['category']} {r['severity']} "
          f"{r['title']} ({r['elapsed']:.1f}s) 断言 {len(r['checks'])}"
          + (f" 失败 {nfail}" if nfail else ""))
    if not r["ok"]:
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       ✗ {c['name']}: {c['detail'][:200]}")


def render_report_md(meta: dict, metrics: dict, results: list) -> str:
    L = []
    L.append("# L2 回复内容评测报告（E3）\n")
    L.append(f"- 运行时间: {meta['run_at']}")
    L.append(f"- 模型路由: {meta['model']} | 仓库版本: {meta['repo_version']}")
    L.append(f"- 任务范围: {len(meta['scope'])} 条 "
             f"（{', '.join(meta['scope'][:20])}"
             + ("…" if len(meta["scope"]) > 20 else "") + "）")
    L.append(f"- 门禁: 断言通过率 = {GATE_PASS_RATE:.0%}（spec §5.2 回归硬门禁）"
             f" | L2 零 LLM 判读\n")
    L.append("## 指标\n")
    L.append("| 指标 | 值 | 门禁 | 达标 |")
    L.append("|---|---|---|---|")
    L.append(f"| 断言通过率 | {metrics['assertion_pass_rate']:.1%} "
             f"({metrics['passed']}/{metrics['executed']}) | "
             f"={GATE_PASS_RATE:.0%} | "
             f"{'是' if meta['thresholds_met'] else '否'} |")
    L.append(f"\n门禁判定: **{'达标' if meta['thresholds_met'] else '未达标'}**"
             f"（首测基线如实留档，失败明细供修复）\n")
    if metrics["skipped"]:
        L.append(f"## 跳过清单（{len(metrics['skipped'])} 条，种子注入失败显式标注）\n")
        for r in results:
            if r["skipped"]:
                L.append(f"- {r['id']} {r['title']}: {r['skip_reason']}")
        L.append("")
    L.append("## 逐任务明细\n")
    L.append("| id | 域 | 级别 | 结果 | 断言数 | 失败断言 | 工具调用 | 说明 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        if r["skipped"]:
            continue
        nfail = [c["name"] for c in r["checks"] if not c["ok"]]
        calls = ",".join(c[0] for c in r["actual_calls"]) or "（零调用）"
        note = (r["exception"] or "")[:80]
        L.append(f"| {r['id']} | {r['category']} | {r['severity']} | "
                 f"{'PASS' if r['ok'] else 'FAIL'} | {len(r['checks'])} | "
                 f"{','.join(nfail) if nfail else '—'} | {calls} | {note} |")
    L.append("")
    fails = [r for r in results if not r["skipped"] and not r["ok"]]
    if fails:
        L.append("## 失败明细（期望片段 vs 实际回复摘录）\n")
        for r in fails:
            L.append(f"### {r['id']} {r['title']}\n")
            for i, c in enumerate(r["checks"]):
                if not c["ok"]:
                    L.append(f"- 断言 {c['name']}（期望 "
                             f"{json.dumps(c['expect'], ensure_ascii=False)}）: "
                             f"{c['detail']}")
            if r["replies"]:
                L.append(f"\n各轮回复摘录:")
                for i, rep in enumerate(r["replies"]):
                    L.append(f"  - 轮{i+1}: {rep[:400].replace(chr(10), ' ')}")
            L.append("")
    return "\n".join(L)


# ================================================================
# 任务选择与 CLI
# ================================================================

def select_task_ids(tasks: list, args) -> list:
    """任务选择：--tasks 指定 / --category 域 / --all 全量 / 默认冒烟子集。

    默认冒烟子集（brief 契约）：no_tool 全部 + edge 域 + P0 抽样 N 条
    （--p0-sample 默认 10）+ 工具域正例切片 TOOL_SLICE（E2 Important #2
    遗留：expected_tools 非空且回复含真实工具结果的样本）。
    """
    if args.tasks:
        want = {x.strip() for x in args.tasks.split(",") if x.strip()}
        unknown = sorted(want - {t["id"] for t in tasks})
        if unknown:
            raise SystemExit(f"FATAL: 未知任务 id: {unknown}")
        return [t["id"] for t in tasks if t["id"] in want]
    if args.category:
        if args.category not in l1_eval.CATEGORIES:
            raise SystemExit(
                f"FATAL: 未知域: {args.category}"
                f"（合法: {','.join(l1_eval.CATEGORIES)}）")
        return [t["id"] for t in tasks if t["category"] == args.category]
    if args.all:
        return [t["id"] for t in tasks]
    ids = [t["id"] for t in tasks if t.get("no_tool")]
    ids += [t["id"] for t in tasks
            if t["category"] == "edge" and t["id"] not in ids]
    p0 = [t["id"] for t in tasks
          if t["severity"] == "P0" and t["id"] not in ids]
    ids += p0[:max(0, int(args.p0_sample))]
    known = {t["id"] for t in tasks}
    slice_ok = [x for x in TOOL_SLICE if x in known]
    missing = [x for x in TOOL_SLICE if x not in known]
    if missing:
        print(f"[warn] 工具切片任务不在评估集: {missing}", file=sys.stderr)
    ids += [x for x in slice_ok if x not in ids]
    return ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="L2 回复内容评测（E3）：黑盒跑生产主链 → 逐轮收集回复 → "
                    "reply_checks 断言 + 跨轮互异 + 攻击后置库状态 → 报告")
    ap.add_argument("--tasks", default="",
                    help="只跑指定任务 id（逗号分隔，如 T001,T017）")
    ap.add_argument("--category", default="",
                    help="只跑指定域（paipan/fortune/zeri/hehun/xingming/"
                         "qian/liuyao/ziwei/chat/edge）")
    ap.add_argument("--all", action="store_true", help="跑全量 108 条")
    ap.add_argument("--p0-sample", type=int, default=10,
                    help="冒烟子集 P0 抽样条数（默认 10）")
    ap.add_argument("--model", default="glm", choices=["glm", "deepseek"],
                    help="LLM 路由（默认 glm-4-flash 免费；deepseek=生产模型）")
    ap.add_argument("--out", default="",
                    help="结果目录（默认 data/eval/results/l2-<时间戳>/）")
    ap.add_argument("--keep-tmp", action="store_true",
                    help="保留临时隔离目录（默认退出清理）")
    args = ap.parse_args(argv)

    # 1) 评估集校验（唯一事实源，只读；失败退出码 2）
    if not TASKS_PATH.exists():
        print(f"FATAL: 评估集不存在: {TASKS_PATH}", file=sys.stderr)
        return 2
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(TASKS_PATH)],
        capture_output=True, text=True, timeout=120)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        print(f"FATAL: 评估集校验失败（validate_tasks 退出码 {proc.returncode}）",
              file=sys.stderr)
        return 2

    tasks = [json.loads(line) for line in TASKS_PATH.read_text(encoding="utf-8")
             .splitlines() if line.strip()]
    ids = select_task_ids(tasks, args)
    selected = [t for t in tasks if t["id"] in ids]
    if not selected:
        print("FATAL: 无任务可跑", file=sys.stderr)
        return 2

    out_dir = (Path(args.out) if args.out
               else RESULTS_ROOT / f"l2-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        run_eval(selected, out_dir, model_route=args.model,
                 keep_tmp=args.keep_tmp)
    except RuntimeError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: 运行器错误: {type(e).__name__}: {str(e)[:300]}",
              file=sys.stderr)
        return 2

    m = (out_dir / "meta.json")
    if m.exists():
        meta = json.loads(m.read_text(encoding="utf-8"))
        mm = meta["metrics"]
        print(f"\n指标: 断言通过率 {mm['assertion_pass_rate']:.1%} "
              f"({mm['passed']}/{mm['executed']})")
        if mm["skipped"]:
            print(f"跳过 {len(mm['skipped'])} 条: {mm['skipped']}")
        if mm["failed"]:
            print(f"失败 {len(mm['failed'])} 条: {mm['failed']}")
        print("门禁判定:", "达标" if meta["thresholds_met"] else "未达标")
        return 0 if meta["thresholds_met"] else 1
    print("FATAL: 结果未落盘", file=sys.stderr)
    return 2


def _git_head() -> str:
    return l1_eval._git_head()


if __name__ == "__main__":
    sys.exit(main())
