#!/usr/bin/env python3
"""对话场景标准答案验证脚本（batch2 D3）——免费模型 glm-4-flash 跑真实链路。

用途：为每次发版提供对话层回归基准。数据：tests/standard_answers/qa_scenarios.jsonl
（13 条场景，从既有 QA 复测 7 场景资产沉淀：qa-12-report / qa-12-fix-retest /
device-check-2026-08-24 / acceptance-cases / fix-toolguide-mainchain 择日 7 场景）。

链路：handler 单测链路 = MessageHandler.process（生产主链，与 /api/chat 同构），
LLM 全部走 glm-4-flash（智谱免费，OpenAI 兼容端点）：
  1) FortuneLLM(provider="glm") → 工具循环走 JSON 工单路径（原生 tool_use 链门控关闭）；
  2) 模块级把 src.llm.client.deepseek_anthropic_completion 重路由到
     glm_openai_completion（主链所有 LLM 调用点均经此函数，含意图分析/自由对话/
     深度分析/润色/工具循环）——key 只从环境变量 ZHIPU_API_KEY 读取，不落任何文件。

数据隔离（不触碰任何真实数据）：
  - 真实库 /mnt/d/fortune-data/userdata/fortune.db（settings.db_path）→ 复制到临时目录，
    仅取其 users/persons/memberships/sessions 结构与 wx_dev_user 账号；
  - 临时库上 seed QA 锚定前置数据：chart_records 已存盘（庚午 辛巳 乙酉 甲申，
    QA 复测同一锚点 1990-05-20 15:30 男 北京）、favorites 3 条（对话/晨笺/灵签）；
  - USER_MEMORY_DIR 指向临时目录（对话画像不写仓库 data/memory/）；
  - 原始库全程只读（复制即快照，运行期零写入）。

断言：结构断言（四柱出现/直读模板原文/宜忌日期/引用格式/工具调用与否/降级文案），
无主观好评判。neg_checks 语义 = 必须为假（即"不得出现"）。
失败场景逐条打印证据，供报告单列问题清单（真 bug vs 断言问题）。

用法：
  /home/a/fortune-agent/.venv/bin/python scripts/verify_qa_scenarios.py [--out REPORT.json] [--only id1,id2]
退出码：0 = 全过；1 = 有失败（供 CI/发版门禁复用）。
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# ── 运行期目录隔离（必须在任何 src 导入前就位，config 于 import 时读 env）──
# 对话画像写临时目录（不碰仓库 data/memory/）；命盘图输出同样走临时目录
# （生产机 /opt/fortune-data/charts 在本环境只读，且不应写系统目录）。
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="qa-d3-"))
os.environ["USER_MEMORY_DIR"] = str(_TMP_ROOT / "memory")
os.environ["CHARTS_DIR"] = str(_TMP_ROOT / "charts")
(_TMP_ROOT / "memory").mkdir(exist_ok=True)
(_TMP_ROOT / "charts").mkdir(exist_ok=True)

# ── 模型路由 patch（必须在任何 LLM 调用前就位）──────────────────────────
# key 只读环境变量，不落任何文件。deepseek_anthropic_completion 与
# glm_openai_completion 前两参（api_key, messages）同形、参数名同，
# 直接透传即可；tools/tool_choice 仅原生 tool_use 链使用（provider=glm
# 时不会传入），防御性剥离。关键：不得另行前插 api_key（调用方本就按
# 位置传入第一个参数，前插会与后续关键字冲突 → 必失败）。
_GLM_KEY = os.environ.get("ZHIPU_API_KEY", "").strip()
if not _GLM_KEY:
    print("FATAL: 环境变量 ZHIPU_API_KEY 未配置（免费模型 glm-4-flash 需要）", file=sys.stderr)
    sys.exit(2)

import src.llm.client as llm_client  # noqa: E402

_MODEL = llm_client.GLM_DEFAULT_MODEL  # "glm-4-flash"


def _glm_routed(api_key, messages, model="deepseek-v4-flash", max_tokens=1000,
                temperature=0.7, timeout=60.0, client=None, stream_cb=None,
                tools=None, tool_choice=None):
    """deepseek_anthropic_completion → glm 免费模型（显式签名镜像）。

    调用方（advisor/chat/analyzer/tool loop 等）会带上各自的 api_key 与
    model（来自 .env，可能过期或为付费模型码，打到智谱 = 401/1214），
    故 key 与 model 一律强制替换为环境变量 key + glm-4-flash；
    tools/tool_choice 仅原生 tool_use 链使用（provider=glm 时不传），丢弃。
    """
    return llm_client.glm_openai_completion(
        _GLM_KEY, messages, model=_MODEL,
        max_tokens=max_tokens, temperature=temperature, timeout=timeout,
        client=client, stream_cb=stream_cb)


llm_client.deepseek_anthropic_completion = _glm_routed

# ── 配置与导入（.env 经 src.config 加载；USER_MEMORY_DIR 必须先于 handler 构造）──
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

_SETTINGS = load_settings()

# QA 锚定账号与盘面（qa-12 复测同一账号/同一锚点）
# 注意：必须用 QA 锚点原时刻 15:30 —— 引擎含真太阳时修正，北京 15:00 经
# 修正后落入未时 → 时柱癸未（与本锚点甲申不符，曾致 3 场景误判）。
USER_ID = "wx_dev_user"
CHART_BAZI = ["庚午", "辛巳", "乙酉", "甲申"]
BIRTH = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
         "city": "北京", "gender": "男"}

SCENARIOS_PATH = REPO / "tests" / "standard_answers" / "qa_scenarios.jsonl"
SCENARIO_TIMEOUT = 300  # 单场景墙钟上限（主链多轮 LLM 调用，QA 实测单轮 25~34s）


class StubRetriever:
    """无向量库检索（不加载 bge-m3 大模型）：search 恒返回 []，链路 fail-open。"""

    def search(self, query, top_k=5, **kw):
        return []


def seed_db(src_db: Path, tmpdir: Path) -> Path:
    """复制真实库 → 临时库并 seed QA 锚定前置数据（原始库零写入）。"""
    dst = tmpdir / "fortune.db"
    shutil.copy2(src_db, dst)
    from src.storage.chart_dao import ChartDAO
    from src.storage.favorite_dao import FavoriteDAO
    ChartDAO(str(dst)).save_chart(
        USER_ID, 1, BIRTH,
        {"bazi": CHART_BAZI, "day_master": "乙木",
         "dayun": [["0", "甲申"]], "liunian": {"2026": "丙午"},
         "shensha": [], "geju": "伤官格", "yongshen": "水"})
    fav = FavoriteDAO(str(dst))
    fav.add(USER_ID, "chat", "qa-d3-1477", "我的八字排盘分析")
    fav.add(USER_ID, "jian", "2026-08-23", "晨笺 2026-08-23 己亥日")
    fav.add(USER_ID, "qian", "qa-d3-3", "灵签第3签 山径独行莫问程")
    return dst


def build_handler(db_path: Path) -> MessageHandler:
    """生产装配（与 src/main.py 同构；retriever 用 Stub，其余真实引擎）。"""
    llm = FortuneLLM(api_key="qa-d3-glm", model=_MODEL, deep_model=_MODEL,
                     provider="glm", glm_api_key=_GLM_KEY)
    handler = MessageHandler(
        BaziEngine(), ZiweiEngine(), LiuyaoEngine(), FengshuiEngine(),
        MianxiangEngine(), ZeriEngine(), StubRetriever(), llm,
        UserDAO(str(db_path)),
        dream_engine=DreamEngine(), hehun_engine=HehunEngine(),
        qimen_engine=QimenEngine(), xingming_engine=XingmingEngine(),
        session_dao=SessionDAO(str(db_path)),
    )
    return handler


# ── 断言求值：check 为真 / neg_check 为假 ────────────────────────────────

def _eval_check(spec: dict, reply: str) -> bool:
    name, expect = next(iter(spec.items()))
    if name == "contains":
        return expect in reply
    if name == "not_contains":
        return expect not in reply
    if name == "contains_any":
        return any(s in reply for s in expect)
    if name == "not_contains_any":
        return all(s not in reply for s in expect)
    if name == "min_len":
        return len(reply) >= expect
    if name == "regex":
        return re.search(expect, reply) is not None
    if name == "regex_absent":
        return re.search(expect, reply) is None
    raise ValueError(f"未知断言类型: {name}")


def _users_count(db_path: Path) -> int:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()


def run_scenario(case: dict, db_path: Path) -> dict:
    """跑单条场景：真实 glm 链路 → 断言 → 返回结果 dict（含逐条证据）。"""
    sid = case["id"]
    result = {"id": sid, "category": case["category"], "input": case["input"],
              "ok": False, "attempts": 0, "elapsed": 0.0, "checks": [],
              "exception": None, "reply": "", "reply_preview": ""}

    def _run_once() -> str:
        handler = build_handler(db_path)
        return handler.process(case["input"], case.get("user_id") or USER_ID,
                               session_id=f"qa-d3-{sid}")

    for attempt in range(1, 3):  # 网络/上游偶发失败允许重试 1 次
        result["attempts"] = attempt
        t0 = time.monotonic()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                reply = ex.submit(_run_once).result(timeout=SCENARIO_TIMEOUT)
        except concurrent.futures.TimeoutError as e:
            result["exception"] = f"场景超时（>{SCENARIO_TIMEOUT}s）"
            result["elapsed"] = time.monotonic() - t0
            break
        except Exception as e:  # noqa: BLE001 — 主链异常也要落报告
            result["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
            result["elapsed"] = time.monotonic() - t0
            if attempt == 1 and "Timeout" not in type(e).__name__:
                time.sleep(10)
                continue
            break
        result["elapsed"] = time.monotonic() - t0
        result["reply"] = reply or ""
        result["reply_preview"] = (reply or "")[:400]
        if not reply and attempt == 1:
            time.sleep(10)  # 空回复可能是上游抖动 → 重试一次
            continue
        break

    # ── 断言（neg_checks 语义：必须为假）──
    checks_out = []
    ok = True
    if result["exception"]:
        ok = False
        checks_out.append({"check": "无异常", "ok": False,
                           "detail": result["exception"]})
    else:
        reply = result["reply"]
        for spec in case.get("checks", []):
            name, expect = next(iter(spec.items()))
            passed = _eval_check(spec, reply)
            ok = ok and passed
            checks_out.append({
                "check": json.dumps(spec, ensure_ascii=False),
                "ok": passed,
                "detail": (f"命中: {expect}" if passed and name == "contains"
                           else f"未命中: {expect}") if not passed else ""})
        for spec in case.get("neg_checks", []):
            name, expect = next(iter(spec.items()))
            # neg 断言 = 该条件必须为假
            violated = _eval_check(spec, reply)
            passed = not violated
            ok = ok and passed
            checks_out.append({
                "check": f"NEG {json.dumps(spec, ensure_ascii=False)}",
                "ok": passed,
                "detail": f"不应出现却出现: {expect}" if violated else ""})
        # 后置断言（DB 状态）
        for spec in case.get("post_checks", []):
            if "users_count_unchanged" in spec:
                count = _users_count(db_path)
                passed = (count == case.get("_users_before"))
                ok = ok and passed
                checks_out.append({
                    "check": "users 行数不变（攻击未生效）",
                    "ok": passed,
                    "detail": f"users 行数 {case.get('_users_before')} → {count}"})
    result["checks"] = checks_out
    result["ok"] = ok
    return result


def main():
    ap = argparse.ArgumentParser(description="D3 对话场景标准答案验证（glm-4-flash）")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径（缺省仅打印）")
    ap.add_argument("--only", default="", help="只跑指定场景 id（逗号分隔）")
    args = ap.parse_args()

    cases = []
    for line_no, line in enumerate(SCENARIOS_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"FATAL: qa_scenarios.jsonl 第 {line_no} 行非法 JSON: {exc}", file=sys.stderr)
            sys.exit(2)
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in only]
    if not cases:
        print("FATAL: 无场景可跑", file=sys.stderr)
        sys.exit(2)

    tmp_root = _TMP_ROOT
    db_path = seed_db(_SETTINGS.db_path, tmp_root)
    users_before = _users_count(db_path)
    for c in cases:
        c["_users_before"] = users_before

    print(f"模型: {_MODEL}（智谱免费，OpenAI 兼容端点，key 仅环境变量）")
    print(f"场景数: {len(cases)} | 临时库: {db_path} | users 基线: {users_before}")
    print("=" * 78)

    results = []
    for case in cases:
        r = run_scenario(case, db_path)
        results.append(r)
        status = "PASS" if r["ok"] else "FAIL"
        print(f"[{status}] {r['id']} {case['title']} "
              f"({r['elapsed']:.1f}s, {r['attempts']}次尝试)")
        for ch in r["checks"]:
            if not ch["ok"]:
                print(f"       ✗ {ch['check']}: {ch['detail']}")
        if r["exception"]:
            print(f"       异常: {r['exception']}")
        print(f"       回复预览: {r['reply_preview'][:200]}")
        print("-" * 78)

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print(f"\n合计: {passed}/{len(results)} 通过；失败 {len(failed)}")
    for r in failed:
        print(f"  FAIL {r['id']}: {r.get('exception') or r['reply_preview'][:120]}")

    report = {
        "model": _MODEL,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "total": len(results), "passed": passed,
        "failed_ids": [r["id"] for r in failed],
        "results": results,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n结果已写入: {args.out}")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
