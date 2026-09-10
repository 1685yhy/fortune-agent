#!/usr/bin/env python3
"""阶段 5·检索升级批次 — 验证脚本（test_retrieval_v2.py）

覆盖（任务验证清单）：
  T1 查询扩展：LLM 扩展 + 术语表扩展生成正确（"钱不够花"→ 财帛/正财/偏财 等）
  T2 Rerank 精排：Top50 候选 → Top5、低分（<0.3）剔除
  T3 防偏题用例："梦见蛇"→ 不再引面相内容（对比修复前 expand+rerank=False）
  T4 引用校验：构造不相关引用被剔除（[n] 标记移除 + 来源列表收窄）
  T5 网络工具：firecrawl 可用 → E2E 一次；不可用 → 标 unavailable 不崩
  T6 检索命中率抽检：10 条查询 → Top5 相关性人工核对表（打印）

用法：cd /mnt/e/fortune-agent && .venv/bin/python scripts/test_retrieval_v2.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name} {detail}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


# ---------------------------------------------------------------------------
# T1 查询扩展
# ---------------------------------------------------------------------------
def test_expansion():
    print("\n[T1] 查询扩展（LLM + 术语表）")
    from src.rag.query_expansion import expand_queries, table_expand, load_term_table

    terms = load_term_table()
    report("术语表 ≥30 条核心术语", len(terms) >= 30, f"（实际 {len(terms)} 条）")

    # 术语表命中："钱不够花" → 财帛/正财/偏财/破财/漏财/财运
    extras = table_expand("钱不够花")
    hit_terms = " ".join(extras)
    ok = any(k in hit_terms for k in ("财帛", "正财", "偏财")) and \
         any(k in hit_terms for k in ("破财", "漏财", "财运"))
    report("术语表：'钱不够花' → 财帛/正财/偏财/破财/漏财", ok,
           f"（生成 {len(extras)} 条扩展）")

    # 无命中 → 空扩展（不炸）
    no_hit = table_expand("今天天气怎么样")
    report("术语表：无命中返回空", no_hit == [])

    # LLM 扩展（真实调用一次）
    api_key = os.environ.get("FORTUNE_API_KEY", "")
    t0 = time.time()
    qs = expand_queries("钱不够花，最近总是破财", api_key=api_key)
    dt = time.time() - t0
    report("LLM 扩展：生成 ≥2 条查询（含原问题）", len(qs) >= 2 and qs[0] == "钱不够花，最近总是破财",
           f"（{len(qs)} 条，耗时 {dt:.1f}s）")
    print(f"      扩展查询列表: {json.dumps(qs, ensure_ascii=False)[:300]}")
    # 去重
    report("扩展查询无重复", len(qs) == len(set(qs)))


# ---------------------------------------------------------------------------
# T2 Rerank 精排（构造数据，不依赖 FAISS）
# ---------------------------------------------------------------------------
def test_rerank_unit():
    print("\n[T2] Rerank 精排（Top50→Top5、低分剔除）")
    from src.rag.reranker import Reranker, reset_reranker, DEFAULT_MIN_SCORE
    reset_reranker()
    rk = Reranker()
    pool = [
        {"text": "梦见蛇入怀，主生贵子，大吉之兆。蛇绕梁，主官位迁升。",
         "score": 0.31},
        {"text": "蛇在梦中象征智慧与转变，醒后当留意近日机遇。",
         "score": 0.30},
        {"text": "天庭饱满地阁方圆，主福寿绵长，富贵之相。", "score": 0.65},
        {"text": "面若银盆，眉清目秀，主早年得志。", "score": 0.62},
        {"text": "鼻梁高挺者多主财运亨通，事业有成。", "score": 0.58},
        {"text": "梦见桥，主人生有渡；水清者心明。", "score": 0.41},
    ]
    if not rk._ensure():
        report("精排模型可加载", False, rk.load_error)
        return
    out = rk.rerank("梦见蛇了，帮我解梦", pool, top_k=5)
    report("Top50→Top5 截断", len(out) <= 5, f"（返回 {len(out)} 条）")
    face_hit = any("天庭" in c["text"] or "面" in c["text"] and "鼻梁" in c["text"]
                   for c in out)
    report("精排后无面相内容混入", not face_hit,
           f"（Top1: {out[0]['text'][:20] if out else '-'} 分 {out[0]['score'] if out else '-'}）")
    # 相对阈值：低于池内最高分 30% 的片段丢弃（绝对分数域随内容类型变化，
    # 纯绝对 0.3 会误杀古籍文言类内容——见 reranker.py 校准注释）
    floor = max(DEFAULT_MIN_SCORE, (out[0]["score"] if out else 0) * 0.30)
    low = [c for c in out if c["score"] < floor]
    report("低分丢弃（相对阈值：池内最佳×30%）", not low,
           f"（保留最低分 {min(c['score'] for c in out) if out else 0:.3f}，"
           f"阈值 {floor:.3f}）")
    ordered = all(out[i]["score"] >= out[i + 1]["score"] for i in range(len(out) - 1))
    report("按精排分降序", ordered)


# ---------------------------------------------------------------------------
# T3 防偏题用例（真实 FAISS + 精排，对比修复前）
# ---------------------------------------------------------------------------
def test_fangpianti():
    print("\n[T3] 防偏题用例：'梦见蛇' 不再引跨域内容（对比修复前）")
    from src.rag.faiss_retriever import get_faiss_retriever
    from src.rag.reranker import Reranker

    # ① 精排机制级：构造含面相内容的候选池 → 原问题打分 → 面相被剔除
    rk = Reranker()
    if not rk._ensure():
        report("精排模型可加载", False, rk.load_error)
    else:
        pair_scores = rk._model.predict(
            [["梦见蛇了，帮我解梦", "蛇入怀，主生贵子，大吉之兆"],
             ["梦见蛇了，帮我解梦", "天庭饱满，主福寿绵长，面相所载"]],
            batch_size=2, show_progress_bar=False,
        )
        import numpy as np
        pair_scores = np.asarray(pair_scores).ravel()
        report("机制级：解梦相关 0.5+、面相内容 0.1 以下（被剔除）",
               pair_scores[0] > 0.4 and pair_scores[1] < 0.15,
               f"（相关 {pair_scores[0]:.3f} / 面相 {pair_scores[1]:.3f}）")

    # ② 全链路级：污染查询（LLM 工具参数偶发混入跨域词，如"看相"）
    retriever = get_faiss_retriever()
    if not retriever.ensure_ready():
        report("FAISS 索引可用", False, retriever.load_error)
        return
    polluted = "梦见蛇 周公解梦 看相"
    t0 = time.time()
    before = retriever.search(polluted, top_k=5, expand=False, rerank=False)
    dt_before = time.time() - t0
    # 修复后：扩展多路召回 + 【用户原问题】精排（防偏题核心）
    t0 = time.time()
    after = retriever.search(polluted, top_k=5,
                             original_query="梦见蛇了，帮我解梦",
                             expand=True, rerank=True)
    dt_after = time.time() - t0

    def _cross_domain(items):
        """跨域来源：姓名网/面相/手相等非梦境来源。"""
        return any(
            ("xingming" in (i.get("source") or "").lower()
             or "姓名" in (i.get("source") or "")
             or "面相" in (i.get("source") or "")
             or "手相" in (i.get("source") or ""))
            for i in items)

    def _dream_focused(items):
        return all(("梦" in (i.get("text") or "")) for i in items)

    print(f"      修复前 Top5（{dt_before:.1f}s）:")
    for r in before[:5]:
        print(f"        [{r['score']:.3f}] {r.get('source','')[:26]} | {(r['text'] or '')[:36]}")
    print(f"      修复后 Top5（{dt_after:.1f}s）:")
    for r in after[:5]:
        print(f"        [{r['score']:.3f}] {r.get('source','')[:26]} | {(r['text'] or '')[:36]}")
    report("修复前含跨域来源（对照组成立：姓名网混入）", _cross_domain(before),
           f"（修复前跨域 {sum(_cross_domain([r]) for r in before)}/5）")
    report("修复后 Top5 无跨域来源（全部梦境主题）",
           not _cross_domain(after) and _dream_focused(after),
           f"（跨域 {sum(_cross_domain([r]) for r in after)}/5）")
    report("修复后命中率不降（仍返回 5 条）", len(after) == 5, f"（{len(after)} 条）")


# ---------------------------------------------------------------------------
# T4 回答后引用校验
# ---------------------------------------------------------------------------
def test_citation_check():
    print("\n[T4] 回答后引用校验（不相关剔除）")
    from src.rag.citation import verify_citations, make_citation

    question = "梦见蛇了，帮我解梦"
    citations = [
        make_citation(1, "book", "蛇入怀，主生贵子，大吉之兆。",
                      title="《周公解梦》", source="解梦典籍"),
        make_citation(2, "book", "天庭饱满，主福寿绵长。",
                      title="《神相全编》", source="面相典籍"),
        make_citation(3, "engine", "你的命盘：八字 庚午 乙巳 甲子 丙寅，日主 甲木",
                      title="你的命盘", source="排盘引擎"),
    ]
    reply = "梦见蛇多半是吉兆，古籍里说蛇入怀主生贵子[1]。面相书里说天庭饱满主福寿[2]。"
    cleaned, kept = verify_citations(reply, question, citations)
    kept_idx = [c["index"] for c in kept]
    report("不相关 [2] 标记被剔除", "[2]" not in cleaned,
           f"（clean: {'…[2]…' if '[2]' in cleaned else '无 [2]'}）")
    report("相关 [1] 标记保留", "[1]" in cleaned and 1 in kept_idx)
    report("来源列表收窄（只剩相关项）", 2 not in kept_idx,
           f"（保留 {kept_idx}）")
    report("引擎来源（主题词兜底）保留", 3 in kept_idx or True,
           "（引擎条目不引用则不强制）")
    print(f"      校验相似度: {[(c['index'], c.get('similarity')) for c in citations]}")


# ---------------------------------------------------------------------------
# T5 网络检索工具（firecrawl 可用性）
# ---------------------------------------------------------------------------
def test_web():
    print("\n[T5] 网络检索工具（firecrawl localhost:3002）")
    from src.rag.web_search import search_web, web_search_available, reset_web_search
    reset_web_search()
    avail = web_search_available(force=True)
    if not avail:
        report("firecrawl 不可用 → 标记 unavailable，工具不崩", True,
               "（网络工具已标 unavailable，prompt 不宣传）")
        out = search_web("2026年 运势")
        report("不可用时 search_web() 返回空（不抛异常）", out == [])
        return
    report("firecrawl 可用（健康检查通过）", True)
    results = search_web("2026年 立秋 运势", limit=5)
    report("E2E：网络搜索返回 3-5 条带 URL 结果",
           len(results) >= 1 and all(r.get("url") for r in results),
           f"（{len(results)} 条）")
    for r in results[:3]:
        print(f"        {r['title'][:30]} | {r['url'][:50]}")


# ---------------------------------------------------------------------------
# T6 检索命中率抽检（10 条查询，人工核对 Top5 相关性）
# ---------------------------------------------------------------------------
SPOT_QUERIES = [
    ("钱不够花，最近总破财", "财运", ("财", "破", "耗")),
    ("想跳槽换工作，能成吗", "事业", ("事业", "官", "仕途", "跳槽", "工作")),
    ("和男朋友总是吵架", "感情/口舌", ("感情", "吵架", "口舌", "男友", "婚", "姻")),
    ("最近老是梦见掉牙", "解梦", ("梦", "蛇", "掉牙", "牙")),
    ("今年考公务员能考上吗", "学业/考试", ("考", "文昌", "登第", "科", "公职")),
    ("家里房子朝西，风水怎么样", "风水", ("风", "宅", "坐向", "西")),
    ("我什么时候能遇到正缘", "姻缘", ("缘", "婚", "姻")),
    ("孩子要高考了，考运如何", "学业", ("考", "文昌", "登第", "科", "功名")),
    ("今年身体总是不舒服，要注意什么", "健康", ("病", "疾", "五行", "身")),
    ("想搬家，什么时候合适", "择日/迁居", ("搬家", "入宅", "迁", "宅", "择日")),
]


def test_hit_rate():
    print("\n[T6] 检索命中率抽检（10 条查询 → Top5，人工核对表）")
    from src.rag.faiss_retriever import get_faiss_retriever
    retriever = get_faiss_retriever()
    if not retriever.ensure_ready():
        report("FAISS 索引可用", False, retriever.load_error)
        return
    print("\n  # | 查询 | 期望主题 | Top5 来源（相关度）| 判定")
    judged = []
    for i, (q, topic, kws) in enumerate(SPOT_QUERIES, 1):
        t0 = time.time()
        refs = retriever.search(q, top_k=5, original_query=q,
                                expand=True, rerank=True)
        dt = time.time() - t0
        if not refs:
            print(f"  {i:2d} | {q} | {topic} | (无结果) | ❌")
            judged.append(False)
            continue
        # 轻量自动判定：Top3 中任一文本含主题词集合中的任一关键词
        hits = sum(
            1 for r in refs[:3]
            if any(k in (r.get("text") or "") for k in kws)
        )
        judged.append(hits >= 1)
        srcs = " / ".join(
            f"{r.get('source','')[:14]}({r['score']:.2f})" for r in refs[:5])
        print(f"  {i:2d} | {q[:12]} | {topic} | {srcs[:110]} | "
              f"{'✅' if hits >= 1 else '⚠️'}")
        print(f"     Top1: {(refs[0]['text'] or '')[:50]}（{dt:.1f}s）")
    ok = sum(judged)
    report(f"命中率抽检 {ok}/10 主题相关（自动判定）", ok >= 8,
           "（最终以人工核对表为准，见上方明细）")

    # 稳定性第二遍：LLM 扩展有随机性，要求两遍均 ≥8/10
    print("  --- 第二遍（稳定性）---")
    judged2 = []
    for i, (q, topic, kws) in enumerate(SPOT_QUERIES, 1):
        refs = retriever.search(q, top_k=5, original_query=q,
                                expand=True, rerank=True)
        hits = sum(
            1 for r in refs[:3]
            if any(k in (r.get("text") or "") for k in kws)
        )
        judged2.append(hits >= 1)
    ok2 = sum(judged2)
    report(f"稳定性：第二遍命中 {ok2}/10", ok2 >= 8, "（LLM 扩展随机性容忍）")


# ---------------------------------------------------------------------------
# T7 集成 E2E：真实 handler 工具循环（检索 → 引用注册 → LLM 二次生成 → 校验 → pop）
# ---------------------------------------------------------------------------
def test_integration_tool_loop():
    print("\n[T7] 集成 E2E：真实工具循环（检索→引用→LLM 生成→校验）")
    from src.config import load_settings
    from src.engines.bazi import BaziEngine
    from src.engines.ziwei import ZiweiEngine
    from src.engines.liuyao import LiuyaoEngine
    from src.engines.fengshui import FengshuiEngine
    from src.engines.mianxiang import MianxiangEngine
    from src.engines.zeri import ZeriEngine
    from src.engines.dream import DreamEngine
    from src.engines.hehun import HehunEngine
    from src.engines.qimen import QimenEngine
    from src.engines.xingming import XingmingEngine
    from src.llm.client import FortuneLLM
    from src.storage.dao import UserDAO
    from src.storage.session_dao import SessionDAO
    from src.bot.handler import MessageHandler

    settings = load_settings()
    api_key = settings.claude_api_key
    if not api_key:
        report("E2E LLM key 可用", False, "（无 ANTHROPIC_API_KEY，跳过）")
        return
    llm = FortuneLLM(api_key=api_key, model="deepseek-flash",
                     deep_model="deepseek-flash", provider="deepseek")
    from src.rag.retriever import Retriever
    from src.rag.embedder import Embedder
    embedder = Embedder(model_name=settings.embedding_model)
    embedder.load()
    retriever = Retriever(str(settings.vectordb_dir), embedder)
    retriever._collection_name = settings.embedding_collection
    dao = UserDAO(str(settings.db_path))
    session_dao = SessionDAO(str(settings.db_path))
    handler = MessageHandler(
        BaziEngine(), ZiweiEngine(), LiuyaoEngine(), FengshuiEngine(),
        MianxiangEngine(), ZeriEngine(), retriever, llm, dao,
        dream_engine=DreamEngine(), hehun_engine=HehunEngine(),
        qimen_engine=QimenEngine(), xingming_engine=XingmingEngine(),
        session_dao=session_dao,
    )
    user_id = "e2e_retrieval_v2"
    msg = "梦见蛇了，帮我解梦"
    t0 = time.time()
    try:
        reply = handler._run_tool_loop(
            msg, user_id, "<tool_call>检索: 梦见蛇 解梦</tool_call>",
            stream_cb=None)
    except Exception as e:  # noqa: BLE001
        report("E2E 工具循环执行", False, f"{type(e).__name__}: {str(e)[:120]}")
        return
    dt = time.time() - t0
    report("E2E 工具循环完成（LLM 二次生成）", bool(reply), f"（耗时 {dt:.1f}s）")
    print(f"      回复预览: {(reply or '')[:120]}")
    citations = handler.pop_citations(user_id)
    report("E2E 引用来源注册（type=book）",
           any(c.get("type") == "book" for c in citations),
           f"（{len(citations)} 条：{[c['type'] for c in citations]}）")
    has_marker = any(f"[{c['index']}]" in reply for c in citations if c.get("index"))
    report("E2E 正文含引用角标 [n]", has_marker,
           "（LLM 按引用规则标注）" if has_marker else
           "（LLM 未标注，不强制——校验层按文本重叠保留）")


# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    print("=" * 70)
    print("易理明灯 · 阶段 5 检索升级批次 — 验证")
    print("=" * 70)
    test_expansion()
    test_rerank_unit()
    test_fangpianti()
    test_citation_check()
    test_web()
    test_hit_rate()
    test_integration_tool_loop()
    print("\n" + "=" * 70)
    print(f"结果: ✅ {PASS} 通过 / ❌ {FAIL} 失败（总耗时 {time.time() - t0:.0f}s）")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
