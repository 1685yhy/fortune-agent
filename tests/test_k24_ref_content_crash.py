"""k24 补丁回归：检索结果读取契约 + 手相/面相报告生成（真实非空 refs）。

背景（P0 回归）
--------------
k24 修好古籍检索（refs 从恒空变成非空）后，暴露出一批此前潜伏的消费点：
它们读 `r.content` —— 而 `ChunkResult`/`_FaissChunk` **都没有该字段**。
空库时代 `if refs:` 恒假 → 该行永不执行 → 潜伏；refs 一非空立刻炸：

- 聊天路径 `handler._try_palm_reading` / `_try_face_reading` 整体被
  `except Exception: return None` 包裹 → **报告静默消失**（用户什么都收不到）
- `/api/palm-reading` `/api/face-reading` → `{"status":"error","message":
  "分析失败：'ChunkResult' object has no attribute 'content'"}` → **异常文本透给前端**

为什么原测试没抓到：`tests/test_emoji_cleanup.py` 传的是 `retriever=None`，
`if refs:` 分支根本不执行。本文件因此**必须用非空 retriever**覆盖该分支。
"""
import pytest

from src.book_categories import ref_text, ref_title
from src.rag.retriever import ChunkResult


class _NonEmptyRetriever:
    """真实返回 ChunkResult 的 stub retriever（**非空**，让 `if refs:` 分支执行）。

    刻意不 monkeypatch 被测函数的任何属性——它模拟的就是生产 retriever
    返回对象的形态（与 FAISS 的 _FaissChunk 同构：只有 text/source/title/score）。
    """

    def __init__(self, n: int = 3):
        self.n = n
        self.calls = []

    def search(self, query, category=None, top_k=20, min_score=0.3, **kw):
        self.calls.append(query)
        return [
            ChunkResult(
                text=f"《麻衣神相》云：生命线深长者生命力强（{query} #{i}）",
                source="麻衣神相",
                score=0.8,
                chunk_id=f"c{i}",
                category="mianxiang_features",
                title="麻衣神相·手相篇",
            )
            for i in range(self.n)
        ]


# ── 1. 读取契约（唯一实现，禁止消费点各自 isinstance / 读 .content）──────

class TestRefAccessContract:
    def test_chunkresult_has_no_content_field(self):
        """契约前提：ChunkResult 没有 content 字段（所以 r.content 必然崩）。"""
        r = ChunkResult(text="t", source="s", score=0.5, chunk_id="c")
        assert not hasattr(r, "content")

    def test_ref_text_and_title_on_chunkresult(self):
        r = ChunkResult(text="乙木生于申月", source="古籍库", score=0.5,
                        chunk_id="c", title="正官格案例十一")
        assert ref_text(r) == "乙木生于申月"
        assert ref_title(r) == "正官格案例十一"  # title 优先于 source

    def test_ref_text_and_title_on_faiss_chunk(self):
        from src.bot.handler import _FaissChunk
        c = _FaissChunk({"text": "乾造：癸卯 庚申", "source": "daizhige",
                         "title": "滴天髓阐微命例011", "score": 0.7})
        assert ref_text(c) == "乾造：癸卯 庚申"
        assert ref_title(c) == "滴天髓阐微命例011"  # 书名优先于 slug
        assert c.title == "滴天髓阐微命例011"

    def test_faiss_chunk_slots_carry_title(self):
        """_FaissChunk.__slots__ 必须含 title（k24 补丁：漏掉则书名被丢弃）。"""
        from src.bot.handler import _FaissChunk
        assert "title" in _FaissChunk.__slots__

    def test_ref_access_dict_form(self):
        d = {"text": "正文", "title": "书名", "source": "slug"}
        assert ref_text(d) == "正文"
        assert ref_title(d) == "书名"

    def test_ref_access_tolerates_missing_and_empty(self):
        assert ref_text({}) == ""
        assert ref_title({}) == "古籍"

        class _Bare:
            pass
        assert ref_text(_Bare()) == ""
        assert ref_title(_Bare()) == "古籍"

    def test_ref_title_priority_consistent_between_dict_and_object(self):
        """k24 补丁：两种形态优先级必须一致（此前 dict 支 title→source、
        对象支 source→title，同一份数据会渲染出不同出处）。"""
        from src.bot.handler import _FaissChunk
        payload = {"text": "x", "source": "slug_source", "title": "真书名"}
        assert ref_title(payload) == ref_title(_FaissChunk(payload)) == "真书名"

    def test_handler_helper_delegates_to_contract(self):
        from src.bot.handler import _ref_title_text
        r = ChunkResult(text="正文", source="s", score=0.5, chunk_id="c",
                        title="出处")
        assert _ref_title_text(r) == ("出处", "正文")
        assert _ref_title_text({"text": "正文", "title": "出处"}) == ("出处", "正文")


# ── 2. 手相/面相报告生成：非空 refs 下不得崩溃（P0 回归本体）────────────

class TestPalmReportWithNonEmptyRefs:
    def test_generate_palm_report_renders_refs_without_crash(self):
        """复现原崩溃：refs 非空 → 原读 r.content → AttributeError。"""
        from src.engines.palm_reader import PalmMetrics, generate_palm_report
        metrics = PalmMetrics(life_line={"detected": True, "length": "长"},
                              wisdom_line={"detected": True},
                              feeling_line={"detected": False},
                              fate_line={"detected": False},
                              palm_shape="掌方", palm_color="红润",
                              finger_type="修长")
        retriever = _NonEmptyRetriever()
        report = generate_palm_report(metrics, retriever=retriever, api_key="")
        assert retriever.calls, "必须真的发起过检索"
        assert "古籍依据" in report, "非空 refs 必须渲染出古籍依据段落"
        assert "麻衣神相" in report, "必须带上真实出处"
        assert "ChunkResult(" not in report, "不得把对象 repr 渲染给用户"


class TestFaceReportWithNonEmptyRefs:
    def test_generate_report_renders_refs_without_crash(self):
        from src.engines.face_reader import FaceMetrics, generate_report
        metrics = FaceMetrics(best_features=["三停均匀"], improvement_areas=["作息"],
                             nose_type="直鼻", eye_type="杏眼", face_shape="鹅蛋脸")
        retriever = _NonEmptyRetriever()
        report = generate_report(metrics, retriever=retriever, api_key="")
        assert retriever.calls, "必须真的发起过检索"
        assert "古籍依据" in report
        assert "麻衣神相" in report
        assert "ChunkResult(" not in report


class TestXuetangWithNonEmptyRefs:
    def test_xuetang_renders_ref_text_not_object_repr(self):
        """学堂 get_lesson：此前 hasattr(r,'content') 恒假 → 渲染 str(r) 对象转储
        （用户看到的是 dataclass repr 而非古籍原文）。"""
        from src.engines.xuetang import get_lesson

        out = get_lesson("什么是八字", retriever=_NonEmptyRetriever())
        assert "ChunkResult(" not in out, "不得把对象 repr 渲染给用户"
        assert "古籍参考" in out, "非空 refs 必须渲染古籍参考段落"
        assert "生命线深长者生命力强" in out, "必须渲染出检索到的原文"


# ── 3. 真实语料：真实 chroma + 真实 bge-m3，端到端走一遍 ────────────────

def _real_retriever_or_skip():
    """真实非空 retriever（27,115 条古籍库）。语料/模型不可用才 skip。"""
    try:
        from src.rag.retriever import Retriever
        from src.rag.embedder import Embedder
        from src.config import load_settings
        settings = load_settings()
        embedder = Embedder(model_name="BAAI/bge-m3")
        embedder.load()
        retriever = Retriever(str(settings.vectordb_dir), embedder,
                              collection_name=settings.embedding_collection)
        if retriever.count() <= 0:
            pytest.skip("本地古籍库为空")
        return retriever
    except Exception as e:  # 环境缺模型/向量库 → skip，不影响测试基线
        pytest.skip(f"本地向量库/模型不可用: {e}")


class TestRealCorpusReportGeneration:
    """真实语料回归：refs 真实非空（这正是触发原崩溃的条件）。"""

    def test_real_palm_report(self):
        from src.engines.palm_reader import PalmMetrics, generate_palm_report
        retriever = _real_retriever_or_skip()
        metrics = PalmMetrics(life_line={"detected": True, "length": "长"},
                              wisdom_line={"detected": True},
                              feeling_line={"detected": True},
                              fate_line={"detected": False},
                              palm_shape="掌方", palm_color="红润",
                              finger_type="修长")
        report = generate_palm_report(metrics, retriever=retriever, api_key="")
        assert "古籍依据" in report, "真实非空 refs 必须渲染出古籍依据"
        assert ref_title(retriever.search("手相 生命线", top_k=2)[0]) != ""
        assert "ChunkResult(" not in report

    def test_real_face_report(self):
        from src.engines.face_reader import FaceMetrics, generate_report
        retriever = _real_retriever_or_skip()
        metrics = FaceMetrics(best_features=["三停均匀"], improvement_areas=["作息"],
                             nose_type="直鼻", eye_type="杏眼", face_shape="鹅蛋脸")
        report = generate_report(metrics, retriever=retriever, api_key="")
        assert "古籍依据" in report
        assert "ChunkResult(" not in report


# ── 4. 类目映射：dream 死映射已删，行为经透传保持不变 ──────────────────

def test_dream_category_still_resolves_via_passthrough():
    """CATEGORY_ALIASES 不再列 dream（无调用方=死映射），但未知词原样透传
    使 resolve_categories("dream") 仍得 ("dream",) —— 删除不改变行为。"""
    from src.book_categories import CATEGORY_ALIASES, resolve_categories
    assert "dream" not in CATEGORY_ALIASES, "dream 是死映射，不应再列"
    assert resolve_categories("dream") == ("dream",)
