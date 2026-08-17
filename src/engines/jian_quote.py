"""古籍金句管线:干支→RAG 候选→规则筛选(书名/长度/30天不重复)→LLM 核对→None 兜底。"""
import logging, os, re
import httpx

from src.utils.text_clean import strip_emoji

logger = logging.getLogger(__name__)
_QUOTE_RECENT: list = []          # 最近 30 天已用金句
_RECENT_MAX = 30
QUOTE_CACHE: dict = {}

def _reset_cache():
    _QUOTE_RECENT.clear(); QUOTE_CACHE.clear()

def _retrieve_candidates(day_ganzhi: str) -> list:
    """RAG 检索:按干支+宜忌主题检索古籍块。返回 [{text, book}]。

    生产主路径:276 万 FAISS 检索器(get_faiss_retriever 进程级单例,惰性加载
    index.faiss 238MB + docs.db + bge-m3 embedder,首次调用约 10~30s;
    管线每日一次,代价可接受)。失败一律返回 [] → 宁缺毋滥,不降级。
    候选 20 条,字段映射:hit['text']→text, hit['source'](书名)→book。
    """
    try:
        from src.rag.faiss_retriever import get_faiss_retriever
        fr = get_faiss_retriever()
        if not fr.ensure_ready():
            logger.warning("古籍金句 RAG 检索器不可用: %s", fr.load_error)
            return []
        hits = fr.search(
            f"{day_ganzhi} 命理 宜忌 古籍",
            top_k=20,
            original_query=f"{day_ganzhi} 命理 宜忌",
            expand=True, rerank=True,
        )
    except Exception as e:
        logger.error("古籍金句 RAG 检索异常: %s", e, exc_info=True)
        return []
    return [{"text": h.get("text", ""), "book": h.get("source") or h.get("title") or ""}
            for h in (hits or [])]

def _filter_candidates(cands: list) -> list:
    out = []
    for c in cands:
        t = (c.get("text") or "").strip().replace("\n", "")
        book = (c.get("book") or "").strip()
        if not book or not 8 <= len(t) <= 45:
            continue
        if any(p in t for p in ("http", "www.", "{{", "}}")):
            continue
        out.append({"text": t, "book": book})
    return out

def _llm_verify(quote: str, book: str, day_ganzhi: str) -> str:
    """LLM 核对金句与当日干支是否呼应;不呼应返回空串。
    失败时宽容通过(仅日志),不阻断当日金句。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return quote
    try:
        r = httpx.post("https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat",
                  "messages": [{"role": "user", "content":
                      f"今日干支:{day_ganzhi}。金句:「{quote}」出自《{book}》。"
                      "这句话与今日干支/宜忌是否呼应?只回答:是 或 否"}],
                  "max_tokens": 10, "temperature": 0},
            timeout=15)
        ans = strip_emoji(r.json()["choices"][0]["message"]["content"].strip())
        return quote if "否" not in ans else ""
    except Exception as e:
        logger.warning("金句核对失败(宽容通过): %s", e)
        return quote

def generate_daily_quote(date_str: str, day_ganzhi: str) -> dict | None:
    if date_str in QUOTE_CACHE:
        return QUOTE_CACHE[date_str]
    cands = _filter_candidates(_retrieve_candidates(day_ganzhi))
    for c in cands:
        if c["text"] in _QUOTE_RECENT:
            continue
        if not _llm_verify(c["text"], c["book"], day_ganzhi):
            continue
        _QUOTE_RECENT.append(c["text"])
        if len(_QUOTE_RECENT) > _RECENT_MAX:
            _QUOTE_RECENT.pop(0)
        result = {"quote": strip_emoji(c["text"]), "book": c["book"],
                  "translation": "", "matched_advice": True, "date": date_str}
        QUOTE_CACHE[date_str] = result
        return result
    QUOTE_CACHE[date_str] = None
    logger.info("金句管线: %s 无合格金句(宁缺毋滥)", date_str)
    return None
