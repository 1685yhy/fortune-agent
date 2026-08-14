"""Retriever v2 单元测试 — bge-m3 (1024维) 全链路一致性验证。

验证项：
1. EMBEDDING_COLLECTION 环境变量可指定集合名
2. add_chunks 写入的集合维度为 1024（用 1024 零向量查询通过 / 384 报错）
3. 带 category 过滤的 search 只返回该分类结果
4. 不带 category 的 search 可跨分类检索
5. 关键词回退路径可用（BM25 / 子串）

用法（服务同款 .venv，模型已缓存在 /tmp/modelscope，首次加载约 1-2 分钟）：
    cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_retriever_v2.py
退出码：0 = 全部通过；1 = 有失败项
"""
import os
import sys
import tempfile
import shutil

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
    from src.rag.embedder import Embedder
    from src.rag.retriever import Retriever
    from src.rag.chunker import chunk_text

    print("Loading bge-m3 embedder (cached, ~1-2 min first load)...")
    embedder = Embedder(model_name="BAAI/bge-m3")
    check("embedder load", embedder.load())
    check("embedder dimension == 1024", embedder.dimension == 1024,
          f"got {embedder.dimension}")

    tmp_dir = tempfile.mkdtemp(prefix="retriever_v2_test_")
    os.environ["EMBEDDING_COLLECTION"] = "test_v2_collection"
    try:
        retriever = Retriever(tmp_dir, embedder)
        check(
            "collection name from env EMBEDDING_COLLECTION",
            retriever._collection_name == "test_v2_collection",
            f"got {retriever._collection_name}",
        )

        # ---- 1. add_chunks with category metadata ----
        # 注意: chunk_text 只产出 >50 字的块，测试文本需足够长
        chunks = [
            chunk_text("紫微斗数以命宫为枢纽，命宫紫微星主贵主福，一生际遇由此而定。"
                       "紫微入命者，志气高远，喜掌权柄，逢吉曜则贵气临身，事业有成。",
                       source="紫微全书", author="佚名", category="ziwei")[0],
            chunk_text("命宫坐七杀，性格刚烈，果断敢为，宜武职或开创事业。"
                       "七杀为将星，主威权，若得吉化则横发，逢煞则辛劳有成。",
                       source="紫微斗数全书", author="佚名", category="ziwei")[0],
            chunk_text("风水讲究藏风聚气，明堂开阔则财源广进，砂环水抱为上吉。"
                       "阳宅宜居处宜坐北朝南，纳气之门宜开于生旺之方，山水有情方为福地。",
                       source="堪舆精要", author="佚名", category="fengshui")[0],
        ]
        check("chunker produced 3 chunks", len(chunks) == 3,
              f"got {len(chunks)}")
        retriever.add_chunks(chunks)
        check("collection count == 3", retriever.count() == 3,
              f"got {retriever.count()}")

        # ---- 2. dimension consistency ----
        # 复用 retriever 的 client（同进程避免 chroma SharedSystemClient 冲突）
        client = retriever.client
        col = client.get_collection("test_v2_collection", embedding_function=None)
        zero_1024 = [[0.0] * 1024]
        try:
            col.query(query_embeddings=zero_1024, n_results=1)
            check("stored dimension == 1024 (1024-dim query OK)", True)
        except Exception as e:
            check("stored dimension == 1024 (1024-dim query OK)", False, str(e))

        try:
            col.query(query_embeddings=[[0.0] * 384], n_results=1)
            check("384-dim query rejected (dimension enforced)", False,
                  "384-dim query unexpectedly succeeded")
        except Exception:
            check("384-dim query rejected (dimension enforced)", True)

        # ---- 3. category-filtered search ----
        results = retriever.search("命宫", category="ziwei", top_k=5, min_score=0.0)
        check("ziwei category search returns hits", len(results) > 0,
              f"got {len(results)}")
        check(
            "all hits are ziwei category",
            all(r.category == "ziwei" for r in results),
            str([r.category for r in results]),
        )
        check(
            "ziwei search does not return fengshui docs",
            all("风水" not in r.text for r in results),
            "fengshui doc leaked into ziwei results",
        )

        # ---- 4. cross-category search ----
        results = retriever.search("风水", top_k=5, min_score=0.0)
        check("cross-category search returns hits", len(results) > 0)
        fengshui_hits = [r for r in results if r.category == "fengshui"]
        check("cross-category search finds fengshui doc",
              any("风水" in r.text for r in results))

        # ---- 5. keyword fallback path ----
        r2 = Retriever(tmp_dir, embedder)
        kw = r2._keyword_search("命宫", "ziwei", 5)
        check("keyword fallback returns ziwei hits", len(kw) > 0)
        check("keyword fallback respects category", all(x.category == "ziwei" for x in kw))

        # ---- 6. chunk_id preserved on roundtrip ----
        ids_in = {c.chunk_id for c in chunks}
        got = col.get(include=["documents"])
        check("chunk ids roundtrip", ids_in.issubset(set(got["ids"])),
              f"missing {ids_in - set(got['ids'])}")
    finally:
        os.environ.pop("EMBEDDING_COLLECTION", None)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'='*50}\nPASS={_PASS} FAIL={_FAIL}")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
