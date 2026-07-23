"""统一爬虫层 — 基于 Crawl4AI，所有文字抓取需求都走这里。

用法:
    from src.scraper import scrape_url, scrape_urls

    # 单页面
    result = await scrape_url("https://example.com/article")
    print(result.markdown)  # 干净的 Markdown

    # 批量
    results = await scrape_urls(["https://a.com", "https://b.com"])
"""

import asyncio
import logging
from typing import Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    HAS_CRAWL4AI = True
except ImportError:
    HAS_CRAWL4AI = False
    logger.warning("Crawl4AI not installed. Run: pip install crawl4ai")


@dataclass
class ScrapeResult:
    url: str
    success: bool
    markdown: str = ""
    title: str = ""
    error: str = ""
    content_length: int = 0

    def to_entry(self, category: str = "scraped") -> dict:
        return {
            "title": self.title or self.url,
            "content": self.markdown[:3000],
            "source": self.url,
            "category": category,
            "quality": "accepted" if len(self.markdown) >= 80 else "rejected",
        }


async def scrape_url(
    url: str,
    timeout: int = 30,
    wait_for: Optional[str] = None,  # CSS selector to wait for
) -> ScrapeResult:
    """抓取单个页面，返回干净 Markdown。

    Args:
        url: 目标 URL
        timeout: 超时秒数
        wait_for: 等待某个 CSS 选择器出现后再提取（用于 JS 渲染页面）
    """
    if not HAS_CRAWL4AI:
        return ScrapeResult(url=url, success=False, error="Crawl4AI not installed")

    try:
        config = CrawlerRunConfig(
            wait_for=wait_for,
            page_timeout=timeout * 1000,  # ms
        ) if wait_for else None

        # 反爬绕过：模拟真实浏览器
        browser_cfg = {
            "headless": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "extra_headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Referer": "https://www.google.com/",
            },
            "java_script_enabled": True,
        }

        async with AsyncWebCrawler(**browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=config)
            return ScrapeResult(
                url=url,
                success=result.success,
                markdown=result.markdown or "",
                title=getattr(result, 'title', '') or "",
                content_length=len(result.markdown) if result.markdown else 0,
            )
    except Exception as e:
        return ScrapeResult(url=url, success=False, error=str(e))


async def scrape_urls(
    urls: List[str],
    concurrency: int = 5,
    timeout: int = 30,
) -> List[ScrapeResult]:
    """并发抓取多个页面。

    Args:
        urls: URL 列表
        concurrency: 并发数
        timeout: 单页超时
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _scrape_one(url: str) -> ScrapeResult:
        async with semaphore:
            return await scrape_url(url, timeout=timeout)

    tasks = [_scrape_one(url) for url in urls]
    return await asyncio.gather(*tasks)


def scrape_sync(url: str, timeout: int = 30) -> ScrapeResult:
    """同步版 scrape_url，方便在非异步代码中调用。"""
    return asyncio.run(scrape_url(url, timeout))


def scrape_urls_sync(urls: List[str], concurrency: int = 5, timeout: int = 30) -> List[ScrapeResult]:
    """同步版 scrape_urls。"""
    return asyncio.run(scrape_urls(urls, concurrency, timeout))
