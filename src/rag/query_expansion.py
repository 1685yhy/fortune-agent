"""查询扩展（阶段 5·多路召回）— LLM 一次调用生成扩展查询 + 预置命理术语表。

设计（方案 §3.2）：
    用户原问题
      ↓ 查询扩展：LLM → 2-3 个扩展查询（命理术语/同义改写）+ 术语表命中词
      ↓ 多路 FAISS 召回 → 合并去重 → 候选池 Top50（recall 阶段只管找得全）
      ↓ Rerank 精排（用【用户原问题】打分，防偏题）→ Top5

本模块只负责「找得全」的查询列表生成：
- expand_queries(query, api_key) → [原问题, LLM 扩展×2~3, 术语表扩展×n]（去重，上限 6 条）
- LLM 失败/超时 → 静默降级为 [原问题 + 术语表扩展]（不阻塞检索主路径）
- 术语表 data/query_expansion.json（60+ 条核心术语映射，子串命中）
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 最多返回的检索查询数（含原问题）
MAX_QUERIES = 6
# LLM 扩展最多生成条数
LLM_EXPAND_COUNT = 3
# LLM 单次调用预算（快，不拖慢首字）
LLM_TIMEOUT = 12.0
LLM_MAX_TOKENS = 400

# LLM API key 环境变量候选（与 src/config.py load_settings 的取值一致）
_API_KEY_ENVS = ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "FORTUNE_API_KEY")


def _llm_api_key() -> str:
    for name in _API_KEY_ENVS:
        key = os.environ.get(name, "")
        if key:
            return key
    return ""

_TERM_CACHE: Optional[dict] = None
_TERM_LOCK = threading.Lock()


def load_term_table() -> dict:
    """加载预置命理术语同义词表（data/query_expansion.json），惰性 + 缓存。"""
    global _TERM_CACHE
    if _TERM_CACHE is not None:
        return _TERM_CACHE
    with _TERM_LOCK:
        if _TERM_CACHE is not None:
            return _TERM_CACHE
        path = (Path(__file__).resolve().parent.parent.parent / "data" / "query_expansion.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            terms = data.get("terms") or {}
            _TERM_CACHE = {str(k): [str(v) for v in vs] for k, vs in terms.items()}
        except Exception as e:  # noqa: BLE001 — 术语表异常不阻塞检索
            logger.warning("命理术语表加载失败: %s", e)
            _TERM_CACHE = {}
        return _TERM_CACHE


def table_expand(query: str, max_per_hit: int = 2) -> List[str]:
    """术语表扩展：子串命中 → 拼接专业术语查询。

    "钱不够花" → ["钱不够花 财帛 正财 偏财", "钱不够花 破财 漏财 财运", ...]
    """
    if not query:
        return []
    terms = load_term_table()
    extras: List[str] = []
    for phrase, mapped in terms.items():
        if phrase in query:
            for m in mapped[:max_per_hit]:
                extras.append(f"{query} {m}".strip()[:120])
    return extras[:MAX_QUERIES]


def llm_expand(query: str, api_key: str = "") -> List[str]:
    """LLM 一次调用生成 2-3 个扩展查询（命理术语/同义改写）。

    提示要求输出 JSON 字符串数组；解析失败/超时 → 返回 []（调用方降级）。
    api_key 缺省时按环境变量候选（ANTHROPIC/DEEPSEEK/FORTUNE）取值。
    """
    if not api_key:
        api_key = _llm_api_key()
    if not api_key or not query:
        return []
    prompt = (
        "你是命理检索查询改写助手。把用户的口语化问题改写为 2-3 个"
        "适合古籍库检索的专业查询（使用命理术语/同义改写，覆盖不同角度）。\n"
        f"用户问题：{query}\n"
        "只输出 JSON 字符串数组，如 [\"查询1\", \"查询2\", \"查询3\"]，"
        "不要输出任何其他文字。每条不超过 40 字。"
    )
    try:
        from ..llm.client import deepseek_anthropic_completion
        content = deepseek_anthropic_completion(
            api_key,
            [{"role": "user", "content": prompt}],
            model="deepseek-v4-flash",
            max_tokens=LLM_MAX_TOKENS,
            temperature=0.4,
            timeout=LLM_TIMEOUT,
        )
        content = (content or "").strip()
        # 兼容 LLM 偶尔输出代码块/前后缀
        m = re.search(r"\[.*\]", content, re.S)
        if not m:
            return []
        raw = json.loads(m.group(0))
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            s = str(item).strip()
            if s and s != query and s not in out:
                out.append(s[:120])
            if len(out) >= LLM_EXPAND_COUNT:
                break
        return out
    except Exception as e:  # noqa: BLE001 — LLM 扩展失败静默降级
        logger.info("LLM 查询扩展失败，仅用术语表: %s", str(e)[:100])
        return []


def expand_queries(query: str, api_key: str = "") -> List[str]:
    """完整查询扩展：原问题 + LLM 扩展 + 术语表扩展（去重，上限 MAX_QUERIES）。

    Args:
        query: 用户原问题/检索关键词
        api_key: LLM API key（为空则跳过 LLM 扩展）

    Returns:
        至少包含 [query] 的去重查询列表（第一项恒为原问题）。
    """
    query = (query or "").strip()
    if not query:
        return []
    queries = [query]
    # llm_expand 内部处理 key 缺省（环境变量候选），不在此处 gate
    queries.extend(llm_expand(query, api_key))
    queries.extend(table_expand(query))
    # 去重保序
    seen, out = set(), []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:MAX_QUERIES]


def reset_term_table() -> None:
    """测试用：清空术语表缓存。"""
    global _TERM_CACHE
    with _TERM_LOCK:
        _TERM_CACHE = None
