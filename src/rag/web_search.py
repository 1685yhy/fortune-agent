"""网络检索工具（阶段 5）— Bing 免费搜索（唯一/主搜索源，无 key、免费）。

Bing 搜索页抓取：httpx GET https://cn.bing.com/search?q=...（浏览器 UA），
标准库 HTMLParser 解析 li.b_algo 结果（标题/URL/摘要）。免费、无 key、无需充值。

⚠️ 停用说明（2026-08-10）：原智谱 Web Search API（open.bigmodel.cn）欠费，
用户决定改用完全免费的 Bing 方案，不再使用智谱——search_web() 直接走 Bing；
原智谱调用逻辑完整保留在 _search_zhipu_legacy() 内，不再被调用，
若后续恢复付费可在此处重新启用。

用法（方案 §3.2 网络检索）：
    <tool_call>搜索: 关键词</tool_call> → search_web() → Top 5（title/url/text）→ 注入

- cn.bing.com 不可达 → web_search_available()=False，工具注册处标 unavailable
  （prompt 不宣传），search_web() 返回 []，不崩；可达性检测缓存 30s，
  失败后周期性重试，网络恢复后自动恢复
- 同查询结果缓存 5 分钟 TTL（cache_key 前缀 bing:）
- 结果统一带来源 URL 与站点名（{title, url, text, site_name}），
  供 citations（type="web"），与 src/bot/handler.py _tool_web_search 消费格式一致
"""
from __future__ import annotations

import base64
import html
import logging
import re
import time
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import quote, unquote

import httpx

logger = logging.getLogger(__name__)

# Bing 免费搜索通道（本机实测 cn.bing.com/search 可用，DuckDuckGo 被墙）
BING_SEARCH_URL = "https://cn.bing.com/search"
BING_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 【停用 2026-08-10】智谱 Web Search API（欠费，改用免费 Bing，不再调用）
ZHIPU_WEB_SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"

HEALTH_TIMEOUT = 5.0   # 接口可达性探测超时（秒）
SEARCH_TIMEOUT = 15.0  # 搜索请求超时（秒）
# 可用性检测结果缓存（秒）；失败后周期性重试
AVAIL_CACHE_TTL = 30.0
# 【停用 2026-08-10】智谱持续失败（鉴权失效/欠费/限流）重试间隔（秒），仅供 legacy 函数
PERM_FAIL_COOLDOWN = 300.0
# 同查询结果缓存（秒）— 5 分钟 TTL
RESULT_CACHE_TTL = 300.0
# 搜索结果截断长度
SNIPPET_CHARS = 200
# 结果缓存上限（条），超出后先清过期再整体清空
RESULT_CACHE_MAX = 256
# 搜索关键词长度上限
QUERY_MAX_CHARS = 70

_avail: Optional[bool] = None
_avail_at: float = 0.0
# 缓存: {cache_key: (expire_at, results)}
_result_cache: dict[str, tuple[float, List[dict]]] = {}


def _probe_bing_reachable(timeout: float = HEALTH_TIMEOUT) -> bool:
    """Bing 搜索通道可达性探测：GET cn.bing.com/search 结果页。

    任何正常 HTTP 响应（200/3xx 已跟随）都说明通道在线；只有网络层失败才不可达。
    """
    try:
        r = httpx.get(BING_SEARCH_URL, headers=BING_HEADERS,
                      timeout=timeout, follow_redirects=True)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — 网络不可达
        return False


def web_search_available(force: bool = False) -> bool:
    """cn.bing.com 搜索通道是否可用（可达性探测 + 30s 缓存）。

    失败后周期性重试，网络恢复后自动恢复；仅网络层不通才返回 False。
    """
    global _avail, _avail_at
    now = time.time()
    if not force and _avail is not None and now < _avail_at:
        return _avail
    _avail = _probe_bing_reachable()
    _avail_at = now + AVAIL_CACHE_TTL
    if not _avail:
        logger.info("cn.bing.com 不可达，网络检索标记 unavailable")
    return _avail


def _resolve_bing_url(url: str) -> str:
    """解析 Bing 结果链接：/ck/a 跳转（u 参数为 base64 编码的真实 URL）。

    u 参数用正则提取 + unquote（不用 parse_qs，避免 base64 中的 '+' 被当空格），
    补 padding 后 base64 解码；取不到则返回 ""（该条跳过）。
    """
    if "/ck/a" not in url:
        return url
    try:
        m = re.search(r"[?&]u=([^&]+)", url)
        if not m:
            return ""
        payload = unquote(m.group(1))
        payload += "=" * (-len(payload) % 4)  # 补齐 base64 padding
        return base64.urlsafe_b64decode(payload).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — 解码失败跳过该条
        return ""


class _BingResultParser(HTMLParser):
    """解析 Bing 结果页：提取 li.b_algo 内的 h2>a（href=url、文本=title）与首段 p（摘要）。

    标题内可能嵌套 <strong> 等标签，handle_data 逐段累积取全文本；
    convert_charrefs=True 自动转换 &#0183; 等字符引用。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[dict] = []
        self._in_algo = False
        self._in_h2 = False
        self._in_title_a = False
        self._in_p = False
        self._got_p = False
        self._title = ""
        self._url = ""
        self._text = ""

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        if tag == "li":
            if "b_algo" in classes:
                self._in_algo = True
                self._title = ""
                self._url = ""
                self._text = ""
                self._got_p = False
            return
        if not self._in_algo:
            return
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and not self._url:
            self._in_title_a = True
            self._url = html.unescape(attr_map.get("href") or "")
        elif tag == "p" and not self._got_p:
            self._in_p = True

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._title += data
        elif self._in_p:
            self._text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_a:
            self._in_title_a = False
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "p" and self._in_p:
            self._in_p = False
            self._got_p = True
        elif tag == "li" and self._in_algo:
            self._in_algo = False
            title = " ".join(self._title.split())
            url = _resolve_bing_url(self._url)
            if title and url and url.startswith("http"):
                self.results.append({
                    "title": title[:120],
                    "url": url,
                    "text": " ".join(self._text.split())[:SNIPPET_CHARS],
                    "site_name": "",
                })


def _search_bing(keywords: str, limit: int = 5,
                 timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """Bing 免费搜索：httpx GET cn.bing.com/search 结果页，标准库 HTMLParser 解析。

    返回 [{title, url, text, site_name}]（site_name 留空字符串）；
    失败/解析不到 → 返回 []（不抛异常）。
    """
    query = (keywords or "").strip()[:QUERY_MAX_CHARS]
    if not query:
        return []
    url = f"{BING_SEARCH_URL}?q={quote(query)}&mkt=zh-CN"
    try:
        r = httpx.get(url, headers=BING_HEADERS, timeout=timeout,
                      follow_redirects=True)
        r.raise_for_status()
        parser = _BingResultParser()
        parser.feed(r.text)
        results = parser.results[:limit]
        if results:
            logger.debug("Bing 检索 '%s' → %d 条", query[:40], len(results))
        return results
    except Exception as e:  # noqa: BLE001 — 抓取/解析失败 → 返回 [] 不崩
        logger.warning("Bing 检索 '%s' 失败: %s", query[:40], str(e)[:120])
        return []


def search_web(keywords: str, limit: int = 5,
               timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """Bing 免费搜索 → [{title, url, text, site_name}, ...]（最多 limit 条）。

    - 抓取 cn.bing.com/search 结果页并解析（无 key、免费；智谱已停用）
    - 结果缓存 5 分钟 TTL（cache_key 前缀 bing:）
    - 接口不可用/异常 → 返回 []（不抛异常，调用方按"查不到"处理）
    """
    keywords = (keywords or "").strip()
    if not keywords or not web_search_available():
        return []
    cache_key = f"bing:{keywords}:{limit}"
    hit = _result_cache.get(cache_key)
    if hit and time.time() < hit[0]:
        logger.info("网络检索 '%s' → 命中缓存 %d 条", keywords[:40], len(hit[1]))
        return hit[1]
    results = _search_bing(keywords, limit, timeout)
    if results:
        _result_cache[cache_key] = (time.time() + RESULT_CACHE_TTL, results)
        if len(_result_cache) > RESULT_CACHE_MAX:
            _trim_result_cache()
    logger.info("网络检索 '%s' → Bing %d 条", keywords[:40], len(results))
    return results


# ---------------------------------------------------------------------------
# 【停用 2026-08-10】智谱 Web Search API（欠费，用户决定免费方案 → 改用 Bing）
# 完整保留原逻辑供追溯/恢复；当前无任何调用方。恢复付费后可重新启用：
#   search_web() 内改为"智谱优先、Bing 兜底"（见 git 历史 2026-08-10 前一版）。
# ---------------------------------------------------------------------------
def _search_zhipu_legacy(keywords: str, limit: int = 5,
                         timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """（不再调用）智谱 Web Search API → [{title, url, text, site_name}, ...]。

    - 参数：search_query / count(5-8) / search_engine=search_std /
      search_recency_filter=noLimit / content_size=medium
    - 返回映射：title ← title，url ← link，text ← content，site_name ← media
    - 接口不可用/异常 → 返回 []（不抛异常）；失败标记 unavailable 周期性重试
    """
    global _avail, _avail_at
    import os
    key = os.getenv("ZHIPU_API_KEY", "").strip()
    if not keywords or not key:
        return []
    cache_key = f"{key}:{keywords}:{limit}"
    hit = _result_cache.get(cache_key)
    if hit and time.time() < hit[0]:
        return hit[1]
    try:
        r = httpx.post(
            ZHIPU_WEB_SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "search_query": keywords[:QUERY_MAX_CHARS],
                "count": max(5, min(limit, 8)),  # 5-8 条
                "search_engine": "search_std",
                "search_recency_filter": "noLimit",
                "content_size": "medium",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status not in (None, 200, 0):
            # 1701 并发上限 / 1702 无搜索引擎 / 1703 无有效数据
            logger.warning("网络检索 '%s' 返回状态 %s: %s",
                           keywords[:40], status, str(data.get("message") or "")[:120])
            return []
        results: List[dict] = []
        seen_urls: set[str] = set()
        for item in data.get("search_result") or []:
            title = str(item.get("title") or "").strip()
            url = str(item.get("link") or "").strip()
            desc = str(item.get("content") or "").strip()
            if not title and not url:
                continue
            if url in seen_urls:  # 去重
                continue
            seen_urls.add(url)
            results.append({
                "title": title[:120],
                "url": url,
                "text": desc[:SNIPPET_CHARS],
                "site_name": str(item.get("media") or "").strip()[:60],
            })
            if len(results) >= limit:
                break
        if results:
            _result_cache[cache_key] = (time.time() + RESULT_CACHE_TTL, results)
            if len(_result_cache) > RESULT_CACHE_MAX:
                _trim_result_cache()
            _avail = True
            _avail_at = time.time() + AVAIL_CACHE_TTL
        return results
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        body = e.response.text or ""
        # 鉴权失效(401/403)/欠费(1113)/限流(429)：大概率持续失败，冷却 5 分钟重试
        permanent = status in (401, 403, 429) or "1113" in body
        cooldown = PERM_FAIL_COOLDOWN if permanent else AVAIL_CACHE_TTL
        logger.warning("网络检索失败 '%s'（HTTP %s%s，%s 秒后重试）",
                       keywords[:40], status,
                       (": " + body[:120]) if body else "",
                       cooldown)
        _avail = False
        _avail_at = time.time() + cooldown
        return []
    except Exception as e:  # noqa: BLE001 — 网络层失败标记不可用
        logger.warning("网络检索 '%s': %s", keywords[:40], str(e)[:120])
        _avail = False
        _avail_at = time.time() + AVAIL_CACHE_TTL
        return []


def _trim_result_cache() -> None:
    """清理结果缓存：先丢过期项，仍超限则整体清空。"""
    now = time.time()
    expired = [k for k, (expire_at, _) in _result_cache.items() if now >= expire_at]
    for k in expired:
        _result_cache.pop(k, None)
    if len(_result_cache) > RESULT_CACHE_MAX:
        _result_cache.clear()


def reset_web_search() -> None:
    """测试用：重置可用性缓存与结果缓存。"""
    global _avail, _avail_at
    _avail = None
    _avail_at = 0.0
    _result_cache.clear()
