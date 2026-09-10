"""古籍库集合名 + 类目映射 —— 单一事实源（k24）。

事故背景
--------
线上生产日志反复出现：

    Collection 'fortune_books' exists but is empty. Legacy fallback index has
    no docs (FAISS main path unaffected); legacy RAG fallback will return
    empty results.

八字/紫微/六爻/奇门/择吉/姓名/面相/风水 8 项能力全部 refs=0，LLM 拿不到任何
古籍原文，只能凭记忆输出引文（已产生《滴天髓》"印能化之…"这类库内查无此句
的编造引文）。两个互相独立的根因：

① 配置踩空：`load_settings` 只读 yaml 的 data_dir/claude_api_key/claude_model
   /push，**不读 `embedding_collection`** → 取 dataclass 默认值 `fortune_books`；
   而该集合经 chroma 实测 embeddings=0（真数据 27,115 条全在 `fortune_books_v2`，
   bge-m3 1024 维）。settings.yaml 里写 `fortune_v6` 同样被忽略，且该集合也是
   空集合——两处配置都踩空。
② 类目错配：handler 的类目过滤词是产品语义（bazi/fengshui/mianxiang…），库内
   元数据 `category` 实际值是数据语义（bazi_case/fengshui_guide/
   mianxiang_features…）。chroma 的 `where` 是精确匹配，对不上就 0 命中；且
   八字/风水/面相/择吉/姓名/合婚这几条路**没有无类目兜底**，只有紫微/六爻/
   奇门有。

本模块是集合名与类目映射的唯一事实源：config / rag / engine 一律引用此处常量
与函数，禁止再散落字面量（散落即分裂，分裂即再次踩空）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 集合名
# ---------------------------------------------------------------------------

# 权威古籍集合（bge-m3 / 1024 维 / 27,115 条，2026-08-15 chroma 精确计数）。
# 生产检索的唯一正确目标。
BOOKS_COLLECTION = "fortune_books_v2"

# 历史遗留空集合（chroma 实测 embeddings=0）。任何指向它们的配置都是踩空，
# 启动期守卫据此提前报错，避免再次静默返回 refs=0 再由 LLM 编造引文。
KNOWN_EMPTY_COLLECTIONS = frozenset({
    "fortune_books",   # chrome MiniLM 384 维时代的旧集合并未迁入数据
    "fortune_v6",      # settings.yaml 曾误配此名，实测 0 条
})


# ---------------------------------------------------------------------------
# 类目映射：handler 过滤词 → 库内元数据 category 实际值
# ---------------------------------------------------------------------------
# 右侧取值来自 fortune_books_v2 全量 27,115 条的精确 category 计数
# （见 k24 报告映射表）。左侧沿用各 handler 调用点现有的过滤词，调用方无需改动。
#
# 混合类目（`古典典籍`/`基础`/`应用` 同时含奇门与手相内容）在两处都列出：
# 类目过滤只是精度提示，语义排序负责最终相关性；漏列的代价是召回，故宁多勿少。
CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    # 八字：库内命例类目为 bazi_case（4934 条），`bazi` 实测 0 条
    "bazi": ("bazi_case",),
    # 紫微：直接对齐（3015 条）
    "ziwei": ("ziwei",),
    # 六爻/易经：直接对齐（302 条）
    "yijing": ("yijing",),
    # 奇门遁甲：库内按知识维度拆成多个中文类目（合计 182 条）
    "qimen": (
        "九星", "三奇六仪", "八神", "八门", "吉凶格局", "排盘",
        "应期判断", "预测应用", "其他体系", "历史人物", "案例",
        "古典典籍", "基础", "应用",
    ),
    # 风水：库内按场景拆成 fengshui_* 四类（合计 3038 条）
    "fengshui": (
        "fengshui_guide", "fengshui_home", "fengshui_office", "fengshui_theory",
    ),
    # 面相手相：面部两类 + 手相细分类目（合计 357 条）
    "mianxiang": (
        "mianxiang_features", "mianxiang_face",
        "三大主线", "次要纹线", "掌形", "掌丘", "手相宫位", "手指",
        "特殊掌纹", "手质地", "手色气色", "特殊纹符",
        "古典典籍", "基础", "应用",
    ),
    # 姓名学：直接对齐（1249 条）
    "xingming": ("xingming",),
    # 注意：不列 "dream"。库内确有 dream 类目（14083 条），但生产解梦路径走的是
    # FAISS 主检索器（handler._get_dream_retriever → _ChunkSearchAdapter），
    # 且 DreamEngine 调 search 时不传 category —— 写了也没有调用方（死映射）。
    # 万一将来有调用方传 category="dream"，resolve_categories 的「未知词原样
    # 透传」会得到完全相同的 ("dream",)，故删除不改变任何行为。
    # 合婚：库内无对应类目 → 空元组 = 明示「无类目可过滤」，走全库检索
    "hehun": (),
    # 择吉：库内无对应类目（择日内容散落在奇门/通书类文档中，无独立 category）
    # → 全库检索。注意这正是「无类目兜底」不可省的实证：类目过滤必然 0 命中。
    "zeri": (),
}


def resolve_categories(name: str | None) -> tuple[str, ...]:
    """过滤词 → 库内 category 实际值元组。

    - 已知过滤词 → 对应实际类目元组（可能为空元组）
    - 未知过滤词 → 原样返回单元素元组（保留旧行为：调用方可能直接传真实类目名）
    - None / 空串 → 空元组（调用方不做类目过滤）
    - 空元组一律表示「该类目无可过滤值」→ 调用方必须退回全库检索，不得返回空
    """
    if not name:
        return ()
    return CATEGORY_ALIASES.get(name, (name,))


# ---------------------------------------------------------------------------
# 检索结果读取契约（k24 补丁 — P0 回归修复）
# ---------------------------------------------------------------------------
# 全仓有两条检索链，返回两类形态：
#   - legacy chroma：`ChunkResult`（src/rag/retriever.py）—— text/source/title/…
#   - FAISS：`_FaissChunk`（src/bot/handler.py）或原始 dict —— text/source/title/…
# 历史教训（k24 P0 回归）：消费点各自 isinstance/getattr 判断，且有人读 `.content`
# —— 这个字段**两条链都不存在**。空库时代 refs 恒为空 → 该行永不执行 → 潜伏；
# 一旦检索修好（refs 非空）立刻炸：手相/面相报告生成 AttributeError，聊天路径
# 异常被 except 吞掉（报告整体消失），API 路径把异常文本透给前端。
# 结论：读取检索结果**只走本模块这两个函数**，禁止再直接访问属性名。

# 空出处伪字面量（k26）：title/source 均缺失时旧检索链注入的「未知」——
# 它不是书名，视同「无出处」（参见 ref_title）。
_NO_SOURCE_LITERALS = frozenset({"未知"})

# 正文行首分隔符（k26）：空 title 语料的 document 前缀（title + ": " + 正文）。
# 只匹配行首、只吃冒号（半角/全角）与其紧邻空白，正文中间的冒号不受影响。
_LEADING_SEPARATOR_RE = re.compile(r"^\s*[:：]+\s*")


def _defined_source(value) -> str:
    """出处取值归一：空/空白/伪字面量「未知」→ ""（无出处），其余原样（去空白）。"""
    s = str(value or "").strip()
    return "" if s in _NO_SOURCE_LITERALS else s


def ref_title(ref) -> str:
    """检索结果 → 引用出处（书名/标题）。

    优先级统一为 **title → source → "古籍"**：库内 `title` 是具体篇目（如
    「正官格案例十一：乙木生于申月，官星得地」），FAISS 侧 `title` 是书名（如
    「秘传刘伯温家藏接骨金疮禁方 - 殆知阁」），而 FAISS 的 `source` 是语料 slug
    （如 daizhige）——给人看的引用一律优先 title。

    裸字符串（解梦引擎等会传纯文本片段）单列一支：否则 `getattr(ref, "title")`
    会命中 `str.title` **方法对象**，渲染出「<built-in method title of str
    object at 0x…>」这种垃圾出处。字符串本身没有出处可言 → "古籍"。

    空出处哨兵（k26 收口，k24 遗留面）：检索链在 title/source 均缺失时曾把
    `source` 填成字面量「未知」（retriever.py 三处 + bm25_retriever.py 一处），
    它不是书名，却让本函数的 "古籍" 兜底永远不可达（渲染《未知》），并使
    handler 的「古籍 → 回落调用方分类标题」分支失效。契约内统一视同「无出处」
    —— 任何生产方再注入该字面量也不会泄漏到用户面。
    """
    if isinstance(ref, (str, bytes)):
        return "古籍"
    if isinstance(ref, dict):
        return (_defined_source(ref.get("title"))
                or _defined_source(ref.get("source")) or "古籍")
    return (_defined_source(getattr(ref, "title", ""))
            or _defined_source(getattr(ref, "source", "")) or "古籍")


def ref_text(ref) -> str:
    """检索结果 → 正文。兼容 dict 与对象两种形态（ChunkResult/_FaissChunk/dict）。

    裸字符串即正文本身（解梦引擎的 interpretations 就是纯文本列表）→ 原样返回；
    若不单列这一支，`getattr(ref, "text")` 取不到值会返回空串，该条引用被
    消费点按「无正文」静默丢弃。

    悬空冒号（k26）：237 条空 title 语料的 document 以 `": "` 开头（入库时
    title + ": " + 正文，title 空 ⇒ 前缀无处可落）——「空标题 + 悬空冒号」是
    同一个数据形态的两面。契约内在正文入口剥离行首分隔符（只剥行首、只剥
    冒号与其前后空白），消费点无需各自清理。
    """
    if isinstance(ref, (str, bytes)):
        text = str(ref)
    elif isinstance(ref, dict):
        text = str(ref.get("text") or ref.get("content") or "")
    else:
        text = str(getattr(ref, "text", "") or getattr(ref, "content", "") or "")
    return _LEADING_SEPARATOR_RE.sub("", text)
