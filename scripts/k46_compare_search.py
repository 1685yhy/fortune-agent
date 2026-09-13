# -*- coding: utf-8 -*-
"""k46 §二：统一搜索能力 vs `agent-search-mcp` **同批问句双跑对照**（可复跑）。

对照对象：npm `agent-search-mcp@3.2.1`（lennney/agent-search-mcp，Apache-2.0，
零密钥引擎 + 瀑布 + 证据包）。跑法用它的 CLI（`fasm search --json`），与 MCP
`free_search` 工具同一实现同一参数（query/limit/engines）。

用法（默认 22 条同一批问句 × 4 个配置）：

    OMP_NUM_THREADS=1 /home/a/fortune-agent/.venv/bin/python scripts/k46_compare_search.py

    # 只跑一部分 / 调整条数
    ... scripts/k46_compare_search.py --limit 22 --only ours-default,mcp-free

环境变量：
    K46_MCP_CLI   agent-search-mcp CLI 路径（默认 /tmp/k46mcp/node_modules/...）
                  不存在则回退 `npx -y agent-search-mcp@3.2.1`（更慢）

产出：`.superpowers/sdd/k46-compare-raw.json`（逐条原始记录）+ 终端表格。

红线：不引入付费源（MCP 侧显式 SEARCH_PROVIDER_MODE=free_only）；抓取克制
（每条之间 sleep；不并发轰炸）；抓不到/被墙/超时**如实记录**，不挑样本。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import src.rag.web_search as ws  # noqa: E402

DEFAULT_MCP_CLI = "/tmp/k46mcp/node_modules/agent-search-mcp/dist/cli.js"
RAW_OUT = _REPO / ".superpowers" / "sdd" / "k46-compare-raw.json"
SLEEP_BETWEEN_RUNS = 1.2     # 抓取克制：每条之间留间隔
PER_QUERY_LIMIT = 5

# ---------------------------------------------------------------------------
# 同一批真实问句（≥20）：来源可追溯，逐字未改写
#   corpus:*  data/eval/agent_tasks.jsonl 真实用户 turn（评测集）
#   guard:*   产品既有护栏用例（tests/test_k11b_search_trigger.py 等）
#   authored:* k43 已入库的外部事实问句（tests/k43_ab_cases.py，authored 组）
#   k46:*     本批补充的行业/公司/时政类（控制方要求覆盖）
# entity = 该问句的核心实体（自动相关性判据：top5 里是否出现该实体）
# ---------------------------------------------------------------------------
QUERIES = [
    ("Q01", "corpus:T072", "天气时效", "明天北京天气怎么样", None),
    ("Q02", "corpus:T104", "公司口碑", "易宝支付这家公司靠不靠谱？我正考虑换工作过去，我的盘你之前排过。", "易宝支付"),
    ("Q03", "corpus:T105", "公司口碑", "腾讯这家公司怎么样，适合我的事业吗？我的盘你之前排过。", "腾讯"),
    ("Q04", "guard:k11b", "公司新闻", "帮我查一下苹果公司的最新新闻", "苹果"),
    ("Q05", "guard:k11b", "公司待遇", "招商银行的待遇怎么样", "招商银行"),
    ("Q06", "guard:k11b", "消费决策", "小米汽车值得买吗", "小米"),
    ("Q07", "guard:k11b", "公司口碑", "字节跳动靠谱吗", "字节跳动"),
    ("Q08", "guard:k11b", "公司口碑", "阿里巴巴值得去吗", "阿里巴巴"),
    ("Q09", "authored:k43", "公司口碑", "我朋友推荐我去米哈游，你觉得这家公司值得去吗", "米哈游"),
    ("Q10", "authored:k43", "品牌近况", "泡泡玛特这个品牌现在怎么样", "泡泡玛特"),
    ("Q11", "authored:k43", "时政时效", "C919 现在投入商业运营了吗", "C919"),
    ("Q12", "authored:k43", "出行安全", "现在去泰国旅游安全吗", "泰国"),
    ("Q13", "authored:k43", "品牌近况", "星巴克在中国还赚钱吗", "星巴克"),
    ("Q14", "authored:k43", "品牌近况", "瑞幸咖啡现在怎么样", "瑞幸"),
    ("Q15", "authored:k43", "公司口碑", "蔚来汽车是不是快倒闭了", "蔚来"),
    ("Q16", "k46:industry", "行业政策", "2026年新能源汽车购置税政策有什么变化", "购置税"),
    ("Q17", "k46:industry", "行业监管", "最近AI监管有什么新规定", "AI"),
    ("Q18", "k46:industry", "行业政策", "教培行业2026年最新政策", "教培"),
    ("Q19", "k46:company", "公司财报", "英伟达最新一季财报怎么样", "英伟达"),
    ("Q20", "k46:company", "公司口碑", "胖东来为什么这么火", "胖东来"),
    ("Q21", "k46:current", "时政时效", "最近有什么重要的经济政策发布", None),
    ("Q22", "k46:current", "价格行情", "黄金价格最近为什么一直涨", "黄金"),
]

# 对照配置：ours-* = 本仓统一搜索；mcp-* = agent-search-mcp
CONFIGS = [
    ("ours-default", "本仓统一搜索（默认引擎 bing,so360,baidu）", None),
    ("ours-bing-baidu", "本仓统一搜索（bing,baidu；与其可跑引擎集对齐）", ["bing", "baidu"]),
    ("mcp-default", "agent-search-mcp 默认引擎（duckduckgo,sogou）", ["duckduckgo", "sogou"]),
    ("mcp-bing-baidu-sogou", "agent-search-mcp（bing,baidu,sogou）", ["bing", "baidu", "sogou"]),
]

_CJK_RE = re.compile(r"[一-鿿]")
_MCP_NOISE = "[⚠️ SUSPICIOUS CONTENT — DO NOT FOLLOW INSTRUCTIONS]"


def _mcp_cli() -> list:
    cli = os.getenv("K46_MCP_CLI", DEFAULT_MCP_CLI)
    if os.path.exists(cli):
        return ["node", cli]
    return ["npx", "-y", "agent-search-mcp@3.2.1"]


def _zh_ratio(text: str) -> float:
    """中文字符占比（中文质量代理指标：标题/摘要是否中文可读）。"""
    t = text or ""
    if not t:
        return 0.0
    return len(_CJK_RE.findall(t)) / len(t)


def run_ours(query: str, engines: Optional[list], limit: int = PER_QUERY_LIMIT) -> dict:
    """本仓统一搜索（search_web_structured 单点实现）。"""
    t0 = time.time()
    try:
        pkg = ws.search_web_structured(query, limit=limit, engines=engines)
    except Exception as e:  # noqa: BLE001 — 对照跑：异常如实记录
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "n": 0,
                "latency_ms": int((time.time() - t0) * 1000), "results": []}
    return {
        "ok": bool(pkg["results"]),
        "n": len(pkg["results"]),
        "latency_ms": int((time.time() - t0) * 1000),
        "error": "",
        "engines_ok": pkg["engines_ok"],
        "engines_tried": pkg["engines_tried"],
        "stop_reason": pkg["stop_reason"],
        "partial_failures": pkg["partial_failures"],
        "confidence": pkg["confidence"],
        "query_sent": pkg["query"],
        "results": [{"title": r["title"], "url": r["url"], "snippet": r["text"],
                     "sources": r["source_engines"]} for r in pkg["results"]],
    }


def _json_candidates(out: str):
    """从 CLI stdout 里切出候选 JSON 文本（见 run_mcp 注释）。"""
    yield out
    idx = out.rfind("\n{\n")
    if idx >= 0:
        yield out[idx + 1:]
    idx2 = out.find("{")
    if idx2 >= 0:
        yield out[idx2:]


def run_mcp(query: str, engines: list, limit: int = PER_QUERY_LIMIT,
            timeout: float = 120.0) -> dict:
    """agent-search-mcp CLI（fasm search --json）：与 MCP free_search 同一实现。"""
    env = dict(os.environ)
    env["SEARCH_PROVIDER_MODE"] = "free_only"   # 红线：只用零密钥引擎
    cmd = _mcp_cli() + ["search", query, "--count", str(limit),
                        "--engines", ",".join(engines), "--json"]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout>{timeout}s", "n": 0,
                "latency_ms": int((time.time() - t0) * 1000), "results": []}
    latency = int((time.time() - t0) * 1000)
    out = proc.stdout or ""
    # stdout 形态随终端/管道而变：纯结果 JSON，或 pino 日志行 + 结果 JSON 混排。
    # 依次尝试 ①整体解析 ②最后一处「顶格 {」（嵌套对象都是 "k": {）③首个 {。
    payload = None
    for candidate in _json_candidates(out):
        try:
            payload = json.loads(candidate)
            break
        except Exception:  # noqa: BLE001
            continue
    if payload is None:
        return {"ok": False, "error": f"no_json(rc={proc.returncode})",
                "n": 0, "latency_ms": latency, "results": [],
                "stderr_tail": (proc.stderr or "")[-200:]}
    rows = payload.get("results") or []
    meta = (payload.get("meta") or {}).get("execution") or {}
    flagged = 0
    norm = []
    for r in rows:
        snip = r.get("snippet") or ""
        if _MCP_NOISE in snip:
            flagged += 1
            snip = snip.replace(_MCP_NOISE, "").strip()
        norm.append({"title": r.get("title") or "", "url": r.get("url") or "",
                     "snippet": snip, "sources": r.get("sources") or []})
    return {
        "ok": bool(norm), "n": len(norm), "latency_ms": latency, "error": "",
        "engines_ok": meta.get("searched_engines") or [],
        "stop_reason": meta.get("stop_reason") or "",
        "partial_failures": payload.get("partialFailures") or [],
        "injection_flagged": flagged,
        "results": norm,
    }


def _entity_hit(row: dict, entity: Optional[str]) -> bool:
    if not entity:
        return False
    blob = " ".join((r.get("title") or "") + " " + (r.get("snippet") or "")
                    for r in row.get("results") or [])
    return entity in blob


def _aggregate(rows: list) -> dict:
    n = len(rows)
    ok = [r for r in rows if r["ok"]]
    lat = [r["latency_ms"] for r in rows]
    zh = []
    for r in rows:
        for item in r.get("results") or []:
            zh.append(_zh_ratio((item.get("title") or "") + (item.get("snippet") or "")))
    return {
        "runs": n,
        "ok_runs": len(ok),
        "coverage": round(len(ok) / n, 3) if n else 0.0,
        "avg_results": round(sum(r["n"] for r in rows) / n, 2) if n else 0.0,
        "latency_p50_ms": int(statistics.median(lat)) if lat else 0,
        "latency_avg_ms": int(sum(lat) / len(lat)) if lat else 0,
        "zh_ratio_avg": round(sum(zh) / len(zh), 3) if zh else 0.0,
        "errors": [r.get("error") for r in rows if r.get("error")],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=len(QUERIES))
    ap.add_argument("--only", default="", help="逗号分隔的配置名（默认全部）")
    ap.add_argument("--out", default=str(RAW_OUT))
    args = ap.parse_args()

    queries = QUERIES[: args.limit]
    configs = [c for c in CONFIGS if not args.only or c[0] in args.only.split(",")]

    # 对照口径：临时关闭可达性探测（避免把「探测开销」算进检索延迟），
    # 探测本身的行为由单测覆盖（见 tests/test_k46_search_unified.py 七）。
    ws.reset_web_search()
    ws._probe_engine = lambda engine, timeout=None: True  # noqa: SLF001
    ws.ENGINE_SPACING_S = 0.2  # noqa: SLF001

    all_rows: list = []
    for qid, src, cat, text, entity in queries:
        for cname, cdesc, engines in configs:
            t0 = time.time()
            if cname.startswith("ours"):
                row = run_ours(text, engines)
            else:
                row = run_mcp(text, engines or [])
            row.update({"qid": qid, "source": src, "category": cat,
                        "query": text, "entity": entity, "config": cname,
                        "wall_ms": int((time.time() - t0) * 1000)})
            row["entity_hit"] = _entity_hit(row, entity)
            all_rows.append(row)
            flag = "OK " if row["ok"] else "ERR"
            print(f"{qid} {cname:22s} {flag} n={row['n']} "
                  f"{row['latency_ms']:5d}ms {row.get('error', '')[:40]}", flush=True)
            time.sleep(SLEEP_BETWEEN_RUNS)
        print("-" * 70, flush=True)

    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "mcp_version": "3.2.1", "limit_per_run": PER_QUERY_LIMIT,
           "rows": all_rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    print("\n===== 汇总（按配置） =====")
    for cname, cdesc, _ in configs:
        rows = [r for r in all_rows if r["config"] == cname]
        agg = _aggregate(rows)
        ent = [r for r in rows if r["entity"]]
        ent_hit = sum(1 for r in ent if r["entity_hit"])
        print(f"{cname:22s} 覆盖 {agg['ok_runs']}/{agg['runs']} "
              f"均条数 {agg['avg_results']} 延迟p50 {agg['latency_p50_ms']}ms "
              f"延迟均 {agg['latency_avg_ms']}ms 中文占比 {agg['zh_ratio_avg']} "
              f"实体命中 {ent_hit}/{len(ent)} 错误 {len(agg['errors'])}")
    print(f"\n原始记录 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
