"""来源体系与引用校验（阶段 5）— 四类来源统一角标 + 回答后引用校验。

来源类型（方案 §3.0，用户 8/8 确认）：
    book   📖 古籍库（276 万 FAISS 原典原文）
    engine ⚙  引擎数据（排盘结果/八字分析 → "你的命盘"）
    memory 🧠 对话记忆（"上次你说过…"）
    web    🌐 网络检索（智谱 Web Search，带来源 URL）

回答后校验（方案 §3.2 ③）：
    生成完成后检查回答中的引用 [n] 与用户问题相关性——轻量做法：
    引用片段 vs 用户问题 embedding 相似度（现有 bge-m3）→ 不相关剔除 [n] 标记。

每条引用统一格式（响应 citations 数组元素）：
    {index, type, title, source, text, url?}
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional
from src.security.log_redact import redact  # k86 必修2：日志不落对话明文

logger = logging.getLogger(__name__)

# 类型 → 展示名/图标（前端抽屉分类也用）
SOURCE_TYPE_LABELS = {"book": "古籍", "engine": "引擎", "memory": "记忆", "web": "网络"}
SOURCE_TYPE_ICONS = {"book": "📖", "engine": "⚙", "memory": "🧠", "web": "🌐"}

# 引用校验最低相似度（bge-m3 余弦）。经验校准（2026-08-08 实测）：
# 同主题短句 0.55~0.75，跨主题 0.35~0.45（如"梦见蛇解梦"×"天庭饱满主福寿"=0.42），
# 取 0.45 可干净分隔；引擎来源另有主题词兜底（_ENGINE_TOPIC_WORDS）。
CITATION_MIN_SIM = 0.45
# 引擎来源（排盘）技术文本与口语问题相似度天然偏低，用主题词兜底
_ENGINE_TOPIC_WORDS = ("财", "官", "印", "姻", "婚", "事业", "工作", "健康",
                       "病", "学业", "考试", "贵人", "桃花", "子女", "父母",
                       "流年", "大运", "运势", "性格", "搬家", "出行", "官司",
                       # 修复批次（2026-08-08）：通用命理提问（排盘/解梦/卦象等）
                       # 的技术文本相似度低，用意图主题词兜底防误删
                       "排盘", "命盘", "八字", "卦", "占卜", "解梦", "梦境",
                       "姓名", "起名", "合婚", "择日", "吉日", "风水", "面相",
                       "手相", "奇门", "紫微", "日历", "时辰")
# 文本重叠判定：引用片段与回答正文重叠字数 ≥ 此值视为"被使用"
_TEXT_OVERLAP_CHARS = 12


def make_citation(index: int, type_: str, text: str, title: str = "",
                  source: str = "", url: Optional[str] = None) -> dict:
    """构造统一来源条目。"""
    return {
        "index": int(index),
        "type": type_ if type_ in SOURCE_TYPE_LABELS else "book",
        "title": (title or "")[:120],
        "source": (source or "")[:120],
        "text": (text or "")[:600],
        "url": url,
    }


def type_label(type_: str) -> str:
    return f"{SOURCE_TYPE_ICONS.get(type_, '📖')}{SOURCE_TYPE_LABELS.get(type_, '古籍')}"


# ---------------------------------------------------------------------------
# 校验用 bge-m3 embedder（惰性单例；优先复用 FAISS 检索器已加载的实例）
# ---------------------------------------------------------------------------

_verifier_embedder = None
_verifier_lock = threading.Lock()


def _get_verifier_embedder():
    """惰性获取 bge-m3 embedder（校验用）。不可用返回 None（校验放行）。"""
    global _verifier_embedder
    if _verifier_embedder is not None:
        return _verifier_embedder
    with _verifier_lock:
        if _verifier_embedder is not None:
            return _verifier_embedder
        # 优先复用 FAISS 检索器已加载的 embedder（避免重复占显存/内存）
        try:
            from .faiss_retriever import get_faiss_retriever
            fr = get_faiss_retriever()
            if fr.ensure_ready() and fr._embedder is not None:
                _verifier_embedder = fr._embedder
                return _verifier_embedder
        except Exception:
            pass
        try:
            from .embedder import Embedder
            e = Embedder(model_name="BAAI/bge-m3")
            if e.load():
                _verifier_embedder = e
        except Exception as exc:  # noqa: BLE001
            logger.warning("引用校验 embedder 不可用（校验放行）: %s", str(exc)[:100])
            _verifier_embedder = None
        return _verifier_embedder


def _similarity(embedder, a: str, b: str) -> float:
    import numpy as np
    va = embedder.encode_single((a or "")[:300])
    vb = embedder.encode_single((b or "")[:300])
    return float(np.dot(va, vb))


def _engine_fallback(citation: dict, question: str) -> bool:
    """引擎来源兜底：排盘技术文本与口语问题相似度天然偏低，
    若问题与命盘文本共享主题词（财/官/姻/学业…）则视为相关。"""
    if citation.get("type") != "engine":
        return False
    text = (citation.get("text") or "") + (question or "")
    return any(w in text for w in _ENGINE_TOPIC_WORDS)


def _text_overlap(citation: dict, reply: str) -> bool:
    """引用片段与回答正文是否有实质重叠（判断"被使用"）。"""
    snippet = (citation.get("text") or "")[:200]
    if not snippet or not reply:
        return False
    # 按 8 字滑窗统计重叠
    win = 8
    hits = 0
    for i in range(0, len(snippet) - win + 1, win):
        if snippet[i:i + win] in reply:
            hits += win
    return hits >= _TEXT_OVERLAP_CHARS


def verify_citations(reply: str, question: str, citations: List[dict],
                     min_sim: float = CITATION_MIN_SIM) -> tuple:
    """回答后引用校验：引用片段 vs 用户问题相似度，不相关剔除 [n] 标记。

    Args:
        reply: 生成的回答（含 [n] 引用标记）
        question: 用户原问题
        citations: 本轮注册的来源列表（[{index, type, title, text, ...}]）
        min_sim: 最低相似度

    Returns:
        (cleaned_reply, kept_citations)
        - cleaned_reply: 剔除不相关 [n] 标记后的回答
        - kept_citations: 保留的来源列表（供响应 citations 数组 / 前端抽屉）
    """
    if not citations:
        return reply, []
    embedder = _get_verifier_embedder()
    cleaned = reply
    kept: List[dict] = []
    for c in citations:
        idx = c.get("index")
        marker = f"[{idx}]"
        referenced = marker in reply
        text = c.get("text") or ""
        relevant = True
        if embedder is not None and (text or "").strip():
            try:
                sim = _similarity(embedder, question, text)
                c["similarity"] = round(sim, 4)
                relevant = sim >= min_sim or _engine_fallback(c, question)
            except Exception:  # noqa: BLE001 — 校验异常放行
                relevant = True
        if referenced:
            if relevant:
                kept.append(c)
            else:
                cleaned = cleaned.replace(marker, "")
                # k86 必修2：text 为回复正文（由对话生成）⇒ 不落明文
                logger.info("引用校验剔除不相关 [%s]（sim=%.3f）: %s",
                            idx, c.get("similarity", 0), redact(text))
        elif relevant:
            # 相关但未显式标注 [n]：该来源是本轮分析实际注入的依据（引擎
            # 检索/排盘结果、古籍片段），LLM 是否书写 [n] 标记存在随机性
            # ——相关即保留供前端来源抽屉展示，不因未标注而丢失来源
            kept.append(c)
    return cleaned, kept


def public_citation(c: dict) -> dict:
    """输出给前端的引用条目（去掉内部字段）。"""
    return {
        "index": c.get("index"),
        "type": c.get("type"),
        "title": c.get("title"),
        "source": c.get("source"),
        "text": c.get("text"),
        "url": c.get("url"),
    }
