"""BM25 古籍检索验证脚本。

验证项：
1. 索引构建（20350 条 all_chunks.json）计时与内存
2. 查询「梦见蛇」相关性（结果含蛇/梦相关词）
3. 单次检索速度 < 200ms
4. 空查询处理
5. 工具级 E2E：MessageHandler._execute_tool_call("检索", ...)

用法（服务同款 .venv）：
    cd /mnt/e/fortune-agent && .venv/bin/python scripts/test_bm25.py
退出码：0 = 全部通过；1 = 有失败项
"""
import os
import sys
import time

# 保证从项目根目录 import src.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    from src.rag.bm25_retriever import get_bm25_retriever, reset_bm25_retriever

    # ------------------------------------------------------------------
    # 1. 索引构建计时
    # ------------------------------------------------------------------
    print("== 1. 索引构建 ==")
    reset_bm25_retriever()
    r = get_bm25_retriever()
    t0 = time.perf_counter()
    built = r.ensure_built()
    build_wall = time.perf_counter() - t0
    print(f"  构建耗时: {build_wall:.1f}s")
    stats = r.stats()
    print(f"  统计: {stats}")
    print(f"  进程内存(峰值): {r.memory_mb():.0f} MB")
    import gc
    gc.collect()
    print(f"  进程内存(gc后): {r.memory_mb():.0f} MB")
    check("索引构建成功", built, f"build_error={r.build_error}")
    check("文档数 = 20350", stats["docs"] == 20350, f"actual={stats['docs']}")
    check("词项数 > 10万", stats["terms"] > 100_000, f"actual={stats['terms']}")

    # ------------------------------------------------------------------
    # 2. 「梦见蛇」相关性
    # ------------------------------------------------------------------
    print("== 2. 查询「梦见蛇」相关性 ==")
    refs = r.search("梦见蛇", top_k=5)
    for i, ref in enumerate(refs, 1):
        print(f"   {i}. 【{ref['source']}】{ref['text'][:80]}... (score={ref['score']})")
    snake_dream = [ref for ref in refs if ("蛇" in ref["text"] or "梦" in ref["text"])]
    check("返回 5 条结果", len(refs) == 5, f"actual={len(refs)}")
    check("≥3 条含蛇/梦相关词", len(snake_dream) >= 3, f"actual={len(snake_dream)}")
    check("来源字段非空", all(ref["source"] for ref in refs))

    # ------------------------------------------------------------------
    # 3. 检索速度 < 200ms（构建完成后计时）
    # ------------------------------------------------------------------
    print("== 3. 检索速度 ==")
    queries = ["梦见蛇", "桃花运 感情", "财运 破财 投资", "开业 吉日 搬家", "八字 用神 喜用"]
    latencies = []
    for q in queries:
        t0 = time.perf_counter()
        r.search(q, top_k=5)
        latencies.append((time.perf_counter() - t0) * 1000)
    print(f"  各查询耗时(ms): {[f'{x:.1f}' for x in latencies]}")
    check("单次检索 < 200ms", max(latencies) < 200.0, f"max={max(latencies):.1f}ms")

    # ------------------------------------------------------------------
    # 4. 空查询处理
    # ------------------------------------------------------------------
    print("== 4. 空查询 ==")
    check("空字符串返回 []", r.search("") == [], f"actual={r.search('')!r}")
    check("纯空白返回 []", r.search("   ") == [], f"actual={r.search('   ')!r}")
    check("None 安全返回 []", r.search(None) == [], f"actual={r.search(None)!r}")

    # ------------------------------------------------------------------
    # 5. 工具级 E2E：真实 MessageHandler._execute_tool_call 检索路径
    # ------------------------------------------------------------------
    print("== 5. 工具级 E2E ==")
    from src.bot.handler import MessageHandler
    # 构造真实 handler（引擎/LLM 传 None 即可，检索工具不依赖它们）
    handler = MessageHandler(None, None, None, None, None, None,
                              retriever=None, llm=None, dao=None)
    res = handler._execute_tool_call("检索", "梦见蛇", "test_user")
    print(f"   ok={res.ok} needs_info={res.needs_info}")
    print(f"   text 前 160 字: {res.text[:160]}")
    check("检索工具执行成功", res.ok)
    check("返回古籍检索结果", "古籍检索结果" in res.text, f"text={res.text[:60]!r}")

    # 空参数 → 提示缺关键词（needs_info，LLM 自然追问）
    res2 = handler._execute_tool_call("检索", "", "test_user")
    check("空参数 → needs_info", res2.needs_info, f"ok={res2.ok} text={res2.text[:60]!r}")

    # 纯标点/无有效词 → 无结果友好提示，不报错
    # （注意：不能用"量子力学 飞碟"这类测试——解梦语料里真的存在"梦见飞碟"条目，
    #   BM25 命中真实内容属于正确行为）
    res3 = handler._execute_tool_call("检索", "???!!! ￥￥", "test_user")
    check("无结果 → 友好提示", res3.ok and "未检索到" in res3.text, f"text={res3.text[:60]!r}")

    print()
    print(f"结果: {_PASS} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
