#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 最小 CLI（E2）：读评估集 → 逐任务黑盒跑生产主链 → 拦截记录 → 比对打分 → 报告落盘。

用法：
  python3 scripts/eval_agent/l1_eval.py                    # 默认冒烟子集
  python3 scripts/eval_agent/l1_eval.py --tasks T001,T017  # 指定任务
  python3 scripts/eval_agent/l1_eval.py --category fortune # 指定域
  python3 scripts/eval_agent/l1_eval.py --all              # 全量 100 条
  python3 scripts/eval_agent/l1_eval.py --model deepseek   # 生产模型路由

退出码：0 = 阈值达标 / 1 = 有任务失败（未达阈值）/ 2 = 评估集校验失败或前置失败。

红线：
- src/bot/tool_calls.py 零改动（拦截在测试侧 interceptor.py，包装
  MessageHandler._execute_tool_call，生产文件零改动）
- 隔离库方案：复制真实库 → 每任务临时库（镜像 scripts/verify_qa_scenarios.py），
  原始库零写入；USER_MEMORY_DIR/CHARTS_DIR 重定向临时目录，data/memory/ 零触碰
- key 只读环境变量（ZHIPU_API_KEY / DEEPSEEK_API_KEY），零落盘
- 零新增依赖（纯标准库 + 仓库既有库）
- data/eval/agent_tasks.jsonl 只读（唯一事实源）；结果落 data/eval/results/

模块导入零副作用：src 导入、环境变量、模型路由 patch 全部在 _init_runtime()/
main() 内懒执行——便于 tests/test_eval_l1.py 直接 import 比对/指标纯函数。
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (_REPO, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from interceptor import ToolCallRecorder  # noqa: E402

TASKS_PATH = _REPO / "data" / "eval" / "agent_tasks.jsonl"
VALIDATOR = _REPO / "scripts" / "eval_agent" / "validate_tasks.py"
RESULTS_ROOT = _REPO / "data" / "eval" / "results"

CATEGORIES = ["paipan", "fortune", "zeri", "hehun", "xingming", "qian",
              "liuyao", "ziwei", "chat", "edge"]
TASK_TIMEOUT = 300  # 单任务墙钟软上限（镜像 verify_qa_scenarios.SCENARIO_TIMEOUT）

# L1 阈值（能力评测首次跑，spec §5.1）
THRESHOLDS = {"tool_selection": 0.90, "param": 0.90, "false_call": 0.0}


# ================================================================
# 比对与指标（纯函数，供单测直接 import）
# ================================================================

def _params_pass(expected_params: dict, actual_params, match: str) -> bool:
    """参数三档比对（E1 标注 §2.1 契约）。

    - any：只查工具名（参数可由消息/档案动态补全，不锁值）→ 恒通过
    - exact：实际参数与期望全等（键 + 值）
    - partial：期望声明的字段（键）全部出现在实际参数中（实际可多出键；
      值不锁——LLM 参数文本为自然语言，锁值等同 exact，partial 契约即键集）
    - 实际参数为 str（文本标签 <tool_call> 路径，无结构键）→ 不满足
      exact/partial 契约（除非期望无键），如实判失败
    """
    if match == "any":
        return True
    if not isinstance(actual_params, dict):
        return False
    if match == "exact":
        return dict(actual_params) == dict(expected_params)
    return all(k in actual_params for k in expected_params)


def compare_expected(expected_tools: list, actual_calls: list) -> tuple:
    """顺序敏感比对。返回 (tool_select_ok, params_ok, detail)。

    - 期望空序列（no_tool 反例桶 与 引擎域 expected_tools=[] 双语义同断言）：
      实际零调用 = 通过；有调用 = 工具选择失败（detail 列出实际序列）
    - 期望非空（工具域）：工具名序列逐位全等 = 工具选择对；参数按 match 三档
      逐位比对 = 参数提取对
    """
    expected_names = [e["name"] for e in expected_tools]
    actual_names = [c[0] for c in actual_calls]
    if not expected_tools:
        ok = len(actual_calls) == 0
        if ok:
            return True, True, ""
        return False, False, (
            f"期望零工具调用，实际 {len(actual_calls)} 次: {actual_names}")
    if actual_names != expected_names:
        return False, False, (
            f"期望工具序列 {expected_names}（{len(expected_tools)} 项，顺序敏感），"
            f"实际 {actual_names}（{len(actual_calls)} 次）")
    fails = []
    for i, e in enumerate(expected_tools):
        if not _params_pass(e["params"], actual_calls[i][1], e["match"]):
            fails.append(
                f"[{i}] {e['name']} 参数比对（{e['match']}）失败："
                f"期望 {json.dumps(e['params'], ensure_ascii=False)}，"
                f"实际 {json.dumps(actual_calls[i][1], ensure_ascii=False)}")
    return True, not fails, "；".join(fails)


def aggregate_metrics(results: list) -> dict:
    """L1 三层指标（spec §5.1）。

    - 工具选择准确率 = 工具名序列完全正确任务数 / 总任务数
      （分母=全部已执行任务：含 no_tool 反例桶与引擎域空期望——期望空实际空=正确）
    - 参数提取准确率 = 参数比对通过任务数 / 工具选择正确任务数
    - 误调率 = no_tool 任务中实际调用工具的比例（应 =0）
    跳过条目（种子注入失败等）不进任何分母，显式列在 skipped。
    """
    executed = [r for r in results if not r["skipped"]]
    n = len(executed)
    sel_ok = [r for r in executed if r["tool_select_ok"]]
    param_passed = sum(1 for r in sel_ok if r["params_ok"])
    no_tool = [r for r in executed if r["no_tool"]]
    false_calls = [r for r in no_tool if r["actual_calls"]]
    return {
        "total": len(results),
        "executed": n,
        "skipped": [r["id"] for r in results if r["skipped"]],
        "tool_selection_accuracy": (len(sel_ok) / n) if n else 0.0,
        "tool_selection_passed": len(sel_ok),
        "param_accuracy": (param_passed / len(sel_ok)) if sel_ok else 0.0,
        "param_passed": param_passed,
        "param_denominator": len(sel_ok),
        "false_call_rate": (len(false_calls) / len(no_tool)) if no_tool else None,
        "false_call_count": len(false_calls),
        "no_tool_count": len(no_tool),
        "failed": [r["id"] for r in executed if not r["ok"]],
        "exceptions": [r["id"] for r in executed if r["exception"]],
    }


def thresholds_met(metrics: dict) -> bool:
    """阈值判定（spec §5.1 首次跑）：工具选择 ≥90% / 参数 ≥90% / 误调率 =0%。"""
    if metrics["executed"] == 0:
        return False
    if metrics["tool_selection_accuracy"] < THRESHOLDS["tool_selection"]:
        return False
    if metrics["param_accuracy"] < THRESHOLDS["param"]:
        return False
    if metrics["false_call_rate"] is not None and \
            metrics["false_call_rate"] > THRESHOLDS["false_call"]:
        return False
    return True


# ================================================================
# 种子注入（E1 标注文档 §3 契约；注入失败显式返回错误串）
# ================================================================

def _bj_today() -> str:
    """北京时间自然日（chat_quota 日键口径，与 services/chat_quota.py 同）。"""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


# 种子依赖表的建表兜底（DDL 逐字取自仓库 DAO 源文件；persons/memberships/
# chart_records 由 DAO 构造器与 models.py 迁移自建，不在列表内）
_SEED_TABLE_DDL = [
    "CREATE TABLE IF NOT EXISTS favorites ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,"
    " type TEXT NOT NULL, ref_id TEXT NOT NULL, summary TEXT DEFAULT '',"
    " imported INTEGER DEFAULT 0,"
    " created_at TEXT DEFAULT (datetime('now')),"
    " UNIQUE(user_id, type, ref_id))",
    "CREATE TABLE IF NOT EXISTS qian_saves ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,"
    " no INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'original',"
    " drawn_at REAL, UNIQUE (user_id, no, kind))",
    "CREATE TABLE IF NOT EXISTS zeri_plans ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,"
    " scene TEXT NOT NULL, lucky_date TEXT NOT NULL,"
    " card_json TEXT NOT NULL, items_json TEXT NOT NULL,"
    " plan_type TEXT NOT NULL DEFAULT 'free',"
    " reminder_enabled INTEGER DEFAULT 0, remind_sent_d1 INTEGER DEFAULT 0,"
    " remind_sent_d0 INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',"
    " created_at REAL)",
    "CREATE TABLE IF NOT EXISTS chat_quota ("
    " user_id TEXT NOT NULL, day TEXT NOT NULL,"
    " cnt INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day))",
]


def parse_birth_components(birth: str) -> dict:
    """"YYYY-MM-DD HH:MM" → {birth_year, birth_month, birth_day, birth_hour, birth_minute}。

    缺时刻/缺字段 → 对应键置 None（与 create_person 的 _birth_dict 兼容）。
    """
    out = {"birth_year": None, "birth_month": None, "birth_day": None,
           "birth_hour": None, "birth_minute": None}
    s = (birth or "").strip()
    if not s:
        return out
    date_part, _, time_part = s.partition(" ")
    parts = date_part.split("-")
    for key, v in zip(("birth_year", "birth_month", "birth_day"), parts):
        try:
            out[key] = int(v)
        except (TypeError, ValueError):
            out[key] = None
    if time_part:
        hm = time_part.split(":")
        for key, v in zip(("birth_hour", "birth_minute"), hm):
            try:
                out[key] = int(v)
            except (TypeError, ValueError):
                out[key] = None
    return out


def seed_task_setup(db_path: str, user_id: str, setup: dict):
    """按 setup 契约注入种子。返回 None=成功；非 None=错误串（跳过并标注）。

    契约键（校验器已拦非法键）：persons / favorites / qian_saves /
    zeri_plans / chart_records / membership / chat_quota。
    """
    setup = setup or {}
    try:
        con = sqlite3.connect(db_path)
        try:
            # 表存在性兜底：与各 DAO 构造器一致（生产库可能尚无懒建表，
            # 如 chat_quota 仅在 DAO 首次连接时 CREATE IF NOT EXISTS）。
            # DDL 逐字取自仓库 DAO 源文件（只读复用，非自造）。
            for ddl in _SEED_TABLE_DDL:
                con.execute(ddl)
            con.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                        (user_id,))
            con.commit()
        finally:
            con.close()

        # persons：默认命主（birth_enc 由 DAO AES 加密落库，不落明文）
        persons = setup.get("persons") or []
        if persons:
            from src.storage.person_dao import PersonDAO
            dao = PersonDAO(db_path)
            for p in persons:
                birth = parse_birth_components(p.get("birth", ""))
                birth["city"] = p.get("city") or ""
                birth["gender"] = p.get("gender") or "unknown"
                r = dao.create_person(
                    user_id, (p.get("name") or "测试用户").strip()[:32],
                    birth=birth)
                if r is None:
                    return f"persons 种子注入失败: {json.dumps(p, ensure_ascii=False)}"

        # chart_records：重看盘存量直读数据源（T003 等）
        if setup.get("chart_records"):
            from src.storage.chart_dao import ChartDAO
            from src.storage.person_dao import PersonDAO
            default = PersonDAO(db_path).get_default_person(user_id)
            pid = default["id"] if default else 1
            for rec in setup["chart_records"]:
                ChartDAO(db_path).save_chart(user_id, pid, {}, dict(rec))

        # favorites：收藏存量直读数据源（kind→type，title→ref_id+summary）
        if setup.get("favorites"):
            from src.storage.favorite_dao import FavoriteDAO
            dao = FavoriteDAO(db_path)
            for f in setup["favorites"]:
                title = str(f.get("title") or "")
                dao.add(user_id, str(f.get("kind") or "chat"), title,
                        summary=title)

        # qian_saves：灵签存量直读数据源（只存 no；kind 默认 original）
        if setup.get("qian_saves"):
            con = sqlite3.connect(db_path)
            try:
                for q in setup["qian_saves"]:
                    con.execute(
                        "INSERT OR IGNORE INTO qian_saves (user_id, no, kind,"
                        " drawn_at) VALUES (?,?,?,?)",
                        (user_id, int(q.get("no")), "original", time.time()))
                con.commit()
            finally:
                con.close()

        # zeri_plans：择吉存量直读数据源（status='active'，list_plans 取最新）
        if setup.get("zeri_plans"):
            con = sqlite3.connect(db_path)
            try:
                for z in setup["zeri_plans"]:
                    con.execute(
                        "INSERT INTO zeri_plans (user_id, scene, lucky_date,"
                        " card_json, items_json, plan_type, reminder_enabled,"
                        " remind_sent_d1, remind_sent_d0, status, created_at)"
                        " VALUES (?,?,?,?,?,'free',0,0,0,'active',?)",
                        (user_id, str(z.get("scene") or ""),
                         str(z.get("date") or ""), "{}", "[]", time.time()))
                con.commit()
            finally:
                con.close()

        # membership：会员档位/额度（非聊天功能额度门数据源，T079/T086/T100）
        if setup.get("membership"):
            m = setup["membership"]
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    "INSERT OR REPLACE INTO memberships "
                    "(user_id, plan, queries_used, queries_limit, auto_renew,"
                    " created_at) VALUES (?, 'free', ?, ?, 0, datetime('now'))",
                    (user_id, int(m.get("queries_used") or 0),
                     int(m.get("queries_limit") or 0)))
                con.commit()
            finally:
                con.close()

        # chat_quota：免费对话额度（当日 cnt；北京时间自然日键，T086）
        if setup.get("chat_quota"):
            cq = setup["chat_quota"]
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    "INSERT OR REPLACE INTO chat_quota (user_id, day, cnt)"
                    " VALUES (?,?,?)",
                    (user_id, _bj_today(), int(cq.get("used") or 0)))
                con.commit()
            finally:
                con.close()
        return None
    except Exception as e:  # noqa: BLE001 — 种子失败要显式标注而非静默
        return f"{type(e).__name__}: {str(e)[:200]}"


# ================================================================
# 运行期（懒加载 src；模块导入零副作用）
# ================================================================

_RUNTIME = {}  # 懒加载缓存：env/dirs/路由 patch/装配闭包


def _init_runtime(model_route: str = "glm") -> dict:
    """运行期引导（幂等）：隔离目录 env → key 校验 → src 导入 → 模型路由 patch。

    镜像 scripts/verify_qa_scenarios.py 的装配方式（黑盒跑生产主链）。
    - USER_MEMORY_DIR/CHARTS_DIR → 临时目录（data/memory/ 零触碰）
    - EXPERIENCE_MODE=0：会员/额度语义确定性（T079/T086/T100 种子才生效）
    - key 只读环境变量（零落盘）；glm 路由需 ZHIPU_API_KEY，deepseek 需
      DEEPSEEK_API_KEY/ANTHROPIC_API_KEY
    """
    if _RUNTIME.get("patched"):
        return _RUNTIME

    # 运行期目录隔离（必须在任何 src 导入前就位）
    if "tmp_root" not in _RUNTIME:
        tmp_root = Path(tempfile.mkdtemp(prefix="eval-l1-"))
        os.environ["USER_MEMORY_DIR"] = str(tmp_root / "memory")
        os.environ["CHARTS_DIR"] = str(tmp_root / "charts")
        os.environ["EXPERIENCE_MODE"] = "0"
        (tmp_root / "memory").mkdir(exist_ok=True)
        (tmp_root / "charts").mkdir(exist_ok=True)
        _RUNTIME["tmp_root"] = tmp_root

    glm_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    ds_key = (os.environ.get("DEEPSEEK_API_KEY", "").strip()
              or os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if not glm_key:
        raise RuntimeError("需要环境变量 ZHIPU_API_KEY（glm-4-flash 免费模型）")
    if model_route == "deepseek" and not ds_key:
        raise RuntimeError("--model deepseek 需要环境变量 "
                           "DEEPSEEK_API_KEY/ANTHROPIC_API_KEY")

    import src.llm.client as llm_client  # noqa: E402
    _MODEL = llm_client.GLM_DEFAULT_MODEL
    _DS_MODEL = "deepseek-v4-flash"  # FortuneLLM 默认生产模型
    _REAL_DEEPSEEK = llm_client.deepseek_anthropic_completion
    _ROUTE = {"model": model_route}

    def _routed(api_key, messages, model="deepseek-v4-flash", max_tokens=1000,
                temperature=0.7, timeout=60.0, client=None, stream_cb=None,
                tools=None, tool_choice=None):
        """deepseek_anthropic_completion → 按路由分发（镜像 verify_qa_scenarios）。

        key 一律强制替换为环境变量 key（调用方自带 .env key 可能过期）。
        """
        if _ROUTE["model"] == "deepseek":
            return _REAL_DEEPSEEK(
                ds_key, messages, model=model, max_tokens=max_tokens,
                temperature=temperature, timeout=timeout, client=client,
                stream_cb=stream_cb, tools=tools, tool_choice=tool_choice)
        return llm_client.glm_openai_completion(
            glm_key, messages, model=_MODEL, max_tokens=max_tokens,
            temperature=temperature, timeout=timeout, client=client,
            stream_cb=stream_cb)

    llm_client.deepseek_anthropic_completion = _routed
    _RUNTIME["patched"] = True
    _RUNTIME["_orig_deepseek"] = _REAL_DEEPSEEK

    # 配置与引擎导入（USER_MEMORY_DIR 已就位）
    from src.config import load_settings  # noqa: E402
    from src.engines.bazi import BaziEngine  # noqa: E402
    from src.engines.ziwei import ZiweiEngine  # noqa: E402
    from src.engines.liuyao import LiuyaoEngine  # noqa: E402
    from src.engines.fengshui import FengshuiEngine  # noqa: E402
    from src.engines.mianxiang import MianxiangEngine  # noqa: E402
    from src.engines.zeri import ZeriEngine  # noqa: E402
    from src.engines.dream import DreamEngine  # noqa: E402
    from src.engines.hehun import HehunEngine  # noqa: E402
    from src.engines.qimen import QimenEngine  # noqa: E402
    from src.engines.xingming import XingmingEngine  # noqa: E402
    from src.llm.client import FortuneLLM  # noqa: E402
    from src.storage.dao import UserDAO  # noqa: E402
    from src.storage.session_dao import SessionDAO  # noqa: E402
    from src.bot.handler import MessageHandler  # noqa: E402

    class _StubRetriever:
        """无向量库检索（不加载 bge-m3 大模型）：search 恒返回 []，fail-open。"""

        def search(self, query, top_k=5, **kw):
            return []

    settings = load_settings()

    def build_handler(db_path, route: str = "glm") -> MessageHandler:
        """生产装配（与 src/main.py 同构；retriever 用 Stub，其余真实引擎）。"""
        if route == "deepseek":
            llm = FortuneLLM(api_key=ds_key, model=_DS_MODEL, deep_model=_DS_MODEL,
                             provider="deepseek")
        else:
            llm = FortuneLLM(api_key="eval-l1-glm", model=_MODEL,
                             deep_model=_MODEL, provider="glm",
                             glm_api_key=glm_key)
        return MessageHandler(
            BaziEngine(), ZiweiEngine(), LiuyaoEngine(), FengshuiEngine(),
            MianxiangEngine(), ZeriEngine(), _StubRetriever(), llm,
            UserDAO(str(db_path)),
            dream_engine=DreamEngine(), hehun_engine=HehunEngine(),
            qimen_engine=QimenEngine(), xingming_engine=XingmingEngine(),
            session_dao=SessionDAO(str(db_path)),
        )

    _RUNTIME.update(settings=settings, build_handler=build_handler)
    return _RUNTIME


def _close_runtime():
    """恢复被 patch 的模块级函数（pytest 同进程复用安全，幂等）。"""
    if _RUNTIME.get("patched"):
        import src.llm.client as llm_client  # noqa: E402
        llm_client.deepseek_anthropic_completion = _RUNTIME["_orig_deepseek"]
        _RUNTIME["patched"] = False


def seed_db_copy(src_db: Path, tmpdir: Path) -> Path:
    """复制真实库 → 临时库（原始库零写入），镜像 verify_qa_scenarios.seed_db。"""
    dst = tmpdir / "fortune.db"
    shutil.copy2(src_db, dst)
    return dst


def _run_one_task(task: dict, R: dict, model_route: str, keep_tmp: bool) -> dict:
    """黑盒跑单任务：临时库 → 种子 → 同会话逐轮 process() → 拦截记录 → 比对。"""
    tid = task["id"]
    user_id = f"eval_{tid}"
    result = {
        "id": tid, "category": task["category"], "severity": task["severity"],
        "title": task["title"], "no_tool": bool(task.get("no_tool", False)),
        "turns": len(task["turns"]), "expected_tools": task["expected_tools"],
        "skipped": False, "skip_reason": "",
        "tool_select_ok": False, "params_ok": False, "ok": False,
        "actual_calls": [], "actual_by_turn": [], "detail": "",
        "exception": None, "elapsed": 0.0, "reply_preview": "",
    }
    tdir = Path(tempfile.mkdtemp(prefix=f"{tid}-", dir=str(R["tmp_root"])))
    try:
        db_path = seed_db_copy(R["settings"].db_path, tdir)
    except Exception as e:  # noqa: BLE001
        result["skipped"] = True
        result["skip_reason"] = f"隔离库复制失败: {type(e).__name__}: {str(e)[:120]}"
        return result
    seed_err = seed_task_setup(str(db_path), user_id, task.get("setup") or {})
    if seed_err is not None:
        result["skipped"] = True
        result["skip_reason"] = f"种子注入失败: {seed_err}"
        return result

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
            result["reply_preview"] = (replies[-1] or "")[:200] if replies else ""
    except concurrent.futures.TimeoutError:
        result["exception"] = f"任务超时（>{TASK_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001 — 主链异常也要落报告
        result["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        result["elapsed"] = time.monotonic() - t0

    result["actual_by_turn"] = recorder.turns()
    result["actual_calls"] = recorder.flat_calls()
    tool_ok, params_ok, detail = compare_expected(task["expected_tools"],
                                                  result["actual_calls"])
    if result["exception"]:
        tool_ok, params_ok = False, False
        detail = (detail + "；" if detail else "") + f"异常: {result['exception']}"
    result["tool_select_ok"] = tool_ok
    result["params_ok"] = params_ok
    result["detail"] = detail
    result["ok"] = tool_ok and params_ok
    return result


def run_eval(tasks: list, out_dir: Path, model_route: str = "glm",
             keep_tmp: bool = False) -> dict:
    """跑指定任务集并落盘报告。返回 {meta, metrics, results}。"""
    R = _init_runtime(model_route)
    if not keep_tmp:
        atexit_register_cleanup(R["tmp_root"])
    results = []
    for task in tasks:
        r = _run_one_task(task, R, model_route, keep_tmp)
        results.append(r)
        _print_task_line(r)
    metrics = aggregate_metrics(results)
    meta = {
        "layer": "L1",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "model": model_route,
        "repo_version": _git_head(),
        "scope": [t["id"] for t in tasks],
        "eval_set": "data/eval/agent_tasks.jsonl (E1, 唯一事实源，只读)",
        "thresholds": THRESHOLDS,
        "metrics": metrics,
        "thresholds_met": thresholds_met(metrics),
        "interceptor": "scripts/eval_agent/interceptor.py "
                       "(monkeypatch MessageHandler._execute_tool_call, 生产零改动)",
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
    print(f"[{status}] {r['id']} {r['category']} {r['severity']} "
          f"{r['title']} ({r['elapsed']:.1f}s)")
    if not r["ok"] and r["detail"]:
        print(f"       ✗ {r['detail'][:300]}")


def render_report_md(meta: dict, metrics: dict, results: list) -> str:
    L = []
    L.append("# L1 工具调用评测报告（E2）\n")
    L.append(f"- 运行时间: {meta['run_at']}")
    L.append(f"- 模型路由: {meta['model']} | 仓库版本: {meta['repo_version']}")
    L.append(f"- 任务范围: {len(meta['scope'])} 条 "
             f"（{', '.join(meta['scope'][:20])}"
             + ("…" if len(meta["scope"]) > 20 else "") + "）")
    L.append(f"- 阈值: 工具选择 ≥{THRESHOLDS['tool_selection']:.0%} / "
             f"参数 ≥{THRESHOLDS['param']:.0%} / 误调率 ={THRESHOLDS['false_call']:.0%}\n")
    L.append("## 指标\n")
    L.append("| 指标 | 值 | 阈值 | 达标 |")
    L.append("|---|---|---|---|")
    fr = metrics["false_call_rate"]
    L.append(f"| 工具选择准确率 | {metrics['tool_selection_accuracy']:.1%} "
             f"({metrics['tool_selection_passed']}/{metrics['executed']}) | "
             f"≥{THRESHOLDS['tool_selection']:.0%} | "
             f"{'是' if metrics['tool_selection_accuracy'] >= THRESHOLDS['tool_selection'] else '否'} |")
    L.append(f"| 参数提取准确率 | {metrics['param_accuracy']:.1%} "
             f"({metrics['param_passed']}/{metrics['param_denominator']}) | "
             f"≥{THRESHOLDS['param']:.0%} | "
             f"{'是' if metrics['param_accuracy'] >= THRESHOLDS['param'] else '否'} |")
    L.append(f"| 误调率（no_tool） | "
             f"{f'{fr:.1%}' if fr is not None else '—（无 no_tool 任务）'} "
             f"({metrics['false_call_count']}/{metrics['no_tool_count']}) | "
             f"={THRESHOLDS['false_call']:.0%} | "
             f"{'是' if fr == 0 else '否'} |")
    L.append(f"\n阈值判定: **{'达标' if meta['thresholds_met'] else '未达标'}**\n")
    if metrics["skipped"]:
        L.append(f"## 跳过清单（{len(metrics['skipped'])} 条，种子注入失败显式标注）\n")
        for r in results:
            if r["skipped"]:
                L.append(f"- {r['id']} {r['title']}: {r['skip_reason']}")
        L.append("")
    L.append("## 逐任务明细\n")
    L.append("| id | 域 | 级别 | 结果 | 期望工具 | 实际调用 | 说明 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in results:
        if r["skipped"]:
            continue
        exp = ",".join(e["name"] for e in r["expected_tools"]) or "（零调用）"
        act = ",".join(c[0] for c in r["actual_calls"]) or "（零调用）"
        note = r["detail"] or r["exception"] or ""
        L.append(f"| {r['id']} | {r['category']} | {r['severity']} | "
                 f"{'PASS' if r['ok'] else 'FAIL'} | {exp} | {act} | "
                 f"{note[:100]} |")
    L.append("")
    fails = [r for r in results if not r["skipped"] and not r["ok"]]
    if fails:
        L.append("## 失败明细（期望 vs 实际调用序列差异）\n")
        for r in fails:
            L.append(f"### {r['id']} {r['title']}\n")
            L.append(f"- 期望: {json.dumps(r['expected_tools'], ensure_ascii=False)}")
            L.append(f"- 实际（按轮次）: {json.dumps(r['actual_by_turn'], ensure_ascii=False)}")
            L.append(f"- 差异: {r['detail'] or r['exception']}")
            L.append(f"- 回复预览: {r['reply_preview']}\n")
    return "\n".join(L)


_ATEXIT_REGISTERED = []


def atexit_register_cleanup(tmp_root: Path):
    """注册退出清理（幂等；--keep-tmp 时不注册）。"""
    import atexit
    import shutil as _sh
    if tmp_root in _ATEXIT_REGISTERED:
        return
    _ATEXIT_REGISTERED.append(tmp_root)
    atexit.register(lambda: _sh.rmtree(tmp_root, ignore_errors=True))


def _git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=str(_REPO),
                             timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# ================================================================
# 任务选择与 CLI
# ================================================================

def select_task_ids(tasks: list, args) -> list:
    """任务选择：--tasks 指定 / --category 域 / --all 全量 / 默认冒烟子集。

    默认冒烟子集（brief 契约）：no_tool 全部 + edge 域 + P0 抽样 N 条
    （--p0-sample 控制，默认 10；已入选的不重复抽样）。
    """
    if args.tasks:
        want = {x.strip() for x in args.tasks.split(",") if x.strip()}
        unknown = sorted(want - {t["id"] for t in tasks})
        if unknown:
            raise SystemExit(f"FATAL: 未知任务 id: {unknown}")
        return [t["id"] for t in tasks if t["id"] in want]
    if args.category:
        if args.category not in CATEGORIES:
            raise SystemExit(
                f"FATAL: 未知域: {args.category}（合法: {','.join(CATEGORIES)}）")
        return [t["id"] for t in tasks if t["category"] == args.category]
    if args.all:
        return [t["id"] for t in tasks]
    ids = [t["id"] for t in tasks if t.get("no_tool")]
    ids += [t["id"] for t in tasks
            if t["category"] == "edge" and t["id"] not in ids]
    p0 = [t["id"] for t in tasks
          if t["severity"] == "P0" and t["id"] not in ids]
    return ids + p0[:max(0, int(args.p0_sample))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="L1 工具调用评测（E2）：黑盒跑生产主链 → 拦截 → 比对打分 → 报告")
    ap.add_argument("--tasks", default="",
                    help="只跑指定任务 id（逗号分隔，如 T001,T017）")
    ap.add_argument("--category", default="",
                    help="只跑指定域（paipan/fortune/zeri/hehun/xingming/"
                         "qian/liuyao/ziwei/chat/edge）")
    ap.add_argument("--all", action="store_true", help="跑全量 100 条")
    ap.add_argument("--p0-sample", type=int, default=10,
                    help="冒烟子集 P0 抽样条数（默认 10）")
    ap.add_argument("--model", default="glm", choices=["glm", "deepseek"],
                    help="LLM 路由（默认 glm-4-flash 免费；deepseek=生产模型）")
    ap.add_argument("--out", default="",
                    help="结果目录（默认 data/eval/results/l1-<时间戳>/）")
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
               else RESULTS_ROOT / f"l1-{time.strftime('%Y%m%d-%H%M%S')}")
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
        print(f"\n指标: 工具选择 {mm['tool_selection_accuracy']:.1%} "
              f"({mm['tool_selection_passed']}/{mm['executed']}) | "
              f"参数 {mm['param_accuracy']:.1%} "
              f"({mm['param_passed']}/{mm['param_denominator']}) | "
              f"误调率 {mm['false_call_count']}/{mm['no_tool_count']}")
        if mm["skipped"]:
            print(f"跳过 {len(mm['skipped'])} 条: {mm['skipped']}")
        if mm["failed"]:
            print(f"失败 {len(mm['failed'])} 条: {mm['failed']}")
        print("阈值判定:", "达标" if meta["thresholds_met"] else "未达标")
        return 0 if meta["thresholds_met"] else 1
    print("FATAL: 结果未落盘", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
