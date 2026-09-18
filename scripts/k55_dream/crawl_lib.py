#!/usr/bin/env python3
"""k55 解梦语料扩充 —— 合规抓取基础库。

硬要求（brief §1.1）：
1. **遵守 robots.txt**：抓取前取 robots.txt，按 RFC 9309 判定；取不到
   （404 / 非 robots 文本）视为无限制；明确 Disallow 的路径一律不抓。
2. **单站限速 ≥1s/请求**：同一 host 的相邻请求间隔不小于 `delay` 秒
   （默认 1.2s，可配），由进程内单线程串行保证。
3. **真实 UA**：默认桌面 Chrome UA，不做伪装成搜索引擎的欺骗。
4. **不绕任何登录 / 验证码 / 反爬**：本库只发普通 GET；遇到 401/403/429
   或 JS 挑战页只做「有限重试 + 如实记录」，**绝不实现绕过逻辑**。
5. **失败重试**：网络错误 / 5xx / 429 退避重试（指数退避，上限可配）；
   4xx（除 429）不重试，直接记为该 URL 失败。
6. **断点续爬**：`Checkpoint` 落盘（原子写），重跑自动跳过已完成项。

输出统一格式（与既有 `{title, content}` 对齐，另加 `source`/`url` 便于溯源）：
    {"title": str, "content": str, "source": str, "url": str, "fetched_at": str}
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

# ── 默认配置 ────────────────────────────────────────────────────────
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_DELAY = 1.2          # 秒/请求（≥1s 硬要求，留 0.2s 余量）
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
BACKOFF_BASE = 2.0           # 退避基数：2s, 4s, 8s

DATA_ROOT = Path("/mnt/d/fortune-data/books/k55_dream")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ── 编码修复 ────────────────────────────────────────────────────────
_CHARSET_RE = re.compile(rb'charset\s*=\s*["\']?([\w-]+)', re.I)


def decode_html(raw: bytes, content_type: str = "") -> str:
    """编码修复：HTTP 头 charset → meta charset → utf-8 → gb18030 兜底。

    老站（佛滔 sosuo.name）返回 GBK 却声明 ISO-8859，直接 utf-8 解会满屏
    乱码；统一走本函数。
    """
    candidates: List[str] = []
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))
    m = _CHARSET_RE.search(raw[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "gb18030", "big5"]

    for enc in candidates:
        enc = (enc or "").strip().lower()
        if not enc or enc in ("iso-8859-1", "latin-1", "ascii"):
            continue  # 这两者恒不报错，等于没解码，跳过
        try:
            text = raw.decode(enc)
            # 乱码特征：解码结果里含大量替换符或典型乱码汉字
            if text.count("�") > len(text) * 0.01:
                continue
            if "锟斤拷" in text or "ï¿½" in text:
                continue
            return text
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ── robots.txt 门禁 ─────────────────────────────────────────────────
class RobotsGate:
    """按站点 robots.txt 判定可抓性（RFC 9309）。

    - 404 / 空 / 非文本（返回 HTML 404 页的老站）→ 无 robots 规则，放行；
    - 有规则 → 用 urllib.robotparser 对 **我们实际使用的 UA** 判定；
    - robots.txt 自身取不到（网络问题）→ 保守起见本次仍按「无规则」处理，
      但把该情况记进 `notes` 供报告披露（避免因网络抖动整站停摆）。
    """

    def __init__(self, base_url: str, user_agent: str = DEFAULT_UA,
                 timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.notes: List[str] = []
        self.rp: Optional[RobotFileParser] = None
        self._disallow_all = False
        self._fetch()

    def _fetch(self) -> None:
        url = self.base_url + "/robots.txt"
        raw, ctype, status = b"", "", 0
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True,
                              headers={"User-Agent": self.user_agent}) as c:
                r = c.get(url)
                raw, ctype, status = r.content, r.headers.get("content-type", ""), r.status_code
        except Exception as e:  # 网络失败
            self.notes.append(f"robots.txt 取用失败（{type(e).__name__}）：按无规则处理")
            return

        if status == 404 or not raw:
            self.notes.append("robots.txt 不存在（404）→ 按无规则处理")
            return
        if "html" in ctype.lower() or raw.lstrip()[:1] == b"<":
            # 老站 robots.txt 404 返回 HTML 页面
            self.notes.append("robots.txt 返回 HTML（老站 404 页）→ 按无规则处理")
            return

        text = decode_html(raw, ctype)
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        self.rp = rp
        self.notes.append(f"robots.txt 已加载（{len(text)} 字节）")

    def allowed(self, url: str) -> bool:
        if self.rp is None:
            return True
        try:
            return self.rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def crawl_delay(self) -> Optional[float]:
        if self.rp is None:
            return None
        try:
            d = self.rp.crawl_delay(self.user_agent)
            return float(d) if d else None
        except Exception:
            return None


# ── 礼貌抓取器 ──────────────────────────────────────────────────────
@dataclass
class FetchStats:
    requests: int = 0
    ok: int = 0
    not_found: int = 0
    errors: int = 0
    blocked: int = 0          # 401/403/429 等反爬响应（不绕过，只记录）
    robots_skipped: int = 0
    retries: int = 0
    bytes: int = 0
    started_at: str = field(default_factory=now_iso)

    def as_dict(self) -> dict:
        return {
            "requests": self.requests, "ok": self.ok, "not_found": self.not_found,
            "errors": self.errors, "blocked": self.blocked,
            "robots_skipped": self.robots_skipped, "retries": self.retries,
            "bytes": self.bytes, "started_at": self.started_at,
            "updated_at": now_iso(),
        }


class PoliteFetcher:
    """单站串行、限速、重试、robots 门禁的抓取器。

    单线程 + 相邻请求间隔门禁 → 严格满足「单站限速 ≥1s/请求」。
    """

    def __init__(self, base_url: str, delay: float = DEFAULT_DELAY,
                 user_agent: str = DEFAULT_UA, timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES, log=print):
        self.base_url = base_url.rstrip("/")
        self.host = urlparse(self.base_url).netloc
        self.ua = user_agent
        self.timeout = timeout
        self.retries = retries
        self.log = log
        self.gate = RobotsGate(self.base_url, user_agent)
        rdelay = self.gate.crawl_delay()
        # 站点 robots 指定了更长的 Crawl-delay 时服从之
        self.delay = max(delay, rdelay or 0.0)
        self.stats = FetchStats()
        self._last = 0.0
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": user_agent,
                     "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                     "Accept-Language": "zh-CN,zh;q=0.9"},
        )

    # -- 限速门禁 --
    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def get(self, url: str, *, required_prefix: Optional[str] = None) -> Optional[str]:
        """抓单个 URL，返回解码后文本；永久失败返回 None。

        `required_prefix`：只允许抓该前缀下的 URL（防适配器拼错 URL 窜到站外）。
        """
        if required_prefix and not url.startswith(required_prefix):
            self.stats.robots_skipped += 1
            self.log(f"[skip] 越界 URL（非 {required_prefix}）：{url}")
            return None
        if not self.gate.allowed(url):
            self.stats.robots_skipped += 1
            self.log(f"[robots] 拒绝抓取：{url}")
            return None

        for attempt in range(self.retries + 1):
            self._wait()
            self.stats.requests += 1
            try:
                r = self._client.get(url)
            except Exception as e:
                self.stats.errors += 1
                if attempt < self.retries:
                    self.stats.retries += 1
                    back = BACKOFF_BASE ** (attempt + 1) + random.uniform(0, 0.5)
                    self.log(f"[retry {attempt+1}/{self.retries}] {type(e).__name__} {url} → {back:.1f}s")
                    time.sleep(back)
                    continue
                self.log(f"[fail] {type(e).__name__} {url}")
                return None

            if r.status_code == 200:
                self.stats.ok += 1
                self.stats.bytes += len(r.content)
                return decode_html(r.content, r.headers.get("content-type", ""))

            if r.status_code == 404 or r.status_code == 410:
                self.stats.not_found += 1
                return None

            if r.status_code in (401, 403, 429) or 500 <= r.status_code < 600:
                if r.status_code in (401, 403, 429):
                    self.stats.blocked += 1
                else:
                    self.stats.errors += 1
                if r.status_code in (401, 403):
                    # 反爬拒绝：**不绕过**，直接放弃该 URL
                    self.log(f"[blocked {r.status_code}] 不绕过，放弃：{url}")
                    return None
                if attempt < self.retries:
                    self.stats.retries += 1
                    back = BACKOFF_BASE ** (attempt + 1) + random.uniform(0, 0.5)
                    self.log(f"[retry {attempt+1}/{self.retries}] HTTP {r.status_code} {url} → {back:.1f}s")
                    time.sleep(back)
                    continue
                self.log(f"[fail] HTTP {r.status_code} {url}")
                return None

            self.stats.errors += 1
            self.log(f"[fail] HTTP {r.status_code} {url}")
            return None

        return None


# ── 断点续爬 ────────────────────────────────────────────────────────
class SiteLock:
    """单站抓取互斥锁（PID 文件）。

    限速是「单站 ≥1s/请求」的**站点级**要求：同一 host 跑两个进程就会变成
    2 req/s（k55 实施中真实踩到：误起两个好梦网实例）。本锁保证同一站点
    同时只有一个抓取进程；进程退出/被杀后由 PID 存活探测自动回收。
    """

    def __init__(self, path: Path, log=print):
        self.path = Path(path)
        self.log = log

    def _holder_alive(self) -> Optional[int]:
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip())
        except Exception:
            return None
        if pid <= 0:
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid
        return pid

    def acquire(self) -> bool:
        holder = self._holder_alive()
        if holder:
            self.log(f"[lock] 站点抓取已被 PID {holder} 占用（避免同站并发超速），本次退出")
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release(self) -> None:
        try:
            if self.path.exists() and int(self.path.read_text(encoding="utf-8").strip()) == os.getpid():
                self.path.unlink()
        except Exception:
            pass


class Checkpoint:
    """断点续爬状态（JSON 原子写）。

    三类进度：
    - `cursor`  ：顺序型任务（如列表页页码）当前进度；
    - `done`    ：集合型任务（如已抓 URL）已完成项，落盘时保持有序；
    - `frontier`：图遍历型任务（如「下一篇」链表）的待抓队列，落盘保序。
    """

    def __init__(self, path: Path, log=print):
        self.path = Path(path)
        self.log = log
        self.cursor: int = 0
        self.done: set = set()
        self.frontier: List[str] = []
        self.stats: dict = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.cursor = int(d.get("cursor", 0))
            self.done = set(d.get("done", []))
            self.frontier = [str(x) for x in d.get("frontier", [])]
            self.stats = dict(d.get("stats", {}))
            self.log(f"[resume] 断点载入 {self.path.name}：cursor={self.cursor} "
                     f"done={len(self.done)} frontier={len(self.frontier)}")
        except Exception as e:
            self.log(f"[resume] 断点文件损坏（{e}），从零开始")

    def save(self, **stats) -> None:
        self.stats.update(stats)
        payload = {
            "cursor": self.cursor,
            "done": sorted(self.done),
            "frontier": self.frontier,
            "stats": self.stats,
            "saved_at": now_iso(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)  # 原子替换，防中断写坏

    def mark(self, item) -> None:
        self.done.add(item)

    def is_done(self, item) -> bool:
        return item in self.done


# ── 统一输出 ────────────────────────────────────────────────────────
class JsonlWriter:
    """统一格式落盘：{title, content, source, url, fetched_at}，追加写。"""

    def __init__(self, path: Path, source: str, log=print, flush_every: int = 20):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.log = log
        self.flush_every = flush_every
        self._buf: List[str] = []
        self.written = 0

    def write(self, title: str, content: str, url: str = "", **extra) -> None:
        rec = {
            "title": title.strip(),
            "content": content.strip(),
            "source": self.source,
            "url": url,
            "fetched_at": now_iso(),
        }
        rec.update(extra)
        self._buf.append(json.dumps(rec, ensure_ascii=False))
        if len(self._buf) >= self.flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n".join(self._buf) + "\n")
        self.written += len(self._buf)
        self._buf.clear()


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ── 文本清洗 ────────────────────────────────────────────────────────
# 导航 / 广告 / 免责声明等噪音行（沿用既有 scrape_zgjmorg.py 的口径并扩充）
NOISE_MARKERS = [
    "当前位置", "首页", "上一篇", "下一篇", "相关文章", "相关阅读", "推荐阅读",
    "免责声明", "版权声明", "来源：", "责任编辑", "周公解梦官网", "周公解梦网",
    "网友评论", "热门文章", "扫一扫", "加微信", "微信公众号", "关注我们",
    "郑重声明", "仅供娱乐", "请勿盲目迷信", "本站", "转载请注明", "点击查看",
    "热门搜索", "最新文章", "推荐测算", "24小时最受欢迎", "推荐心理测试",
    "十二星座明日运势", "最新真实梦境", "上一篇：", "下一篇：", "分享到",
    "COPYRIGHT", "Copyright", "版权所有", "备案号", "ICP备", "登录", "注册",
    "全部评论", "我要评论", "猜你喜欢", "小编推荐", "广告", "下载APP",
]
_NOISE_RE = re.compile(
    r"(" + "|".join(re.escape(s) for s in NOISE_MARKERS) + r")"
)
_WS_RE = re.compile(r"[ \t　]+")
_MULTI_NL_RE = re.compile(r"\n{2,}")


def clean_content(text: str, min_len: int = 20) -> str:
    """去噪 + 规整空白；不足 min_len 返回空串（最短长度过滤）。"""
    if not text:
        return ""
    lines = []
    for raw in text.split("\n"):
        line = _WS_RE.sub(" ", raw).strip()
        if not line:
            continue
        if len(line) <= 60 and _NOISE_RE.search(line):
            continue
        lines.append(line)
    out = _MULTI_NL_RE.sub("\n", "\n".join(lines)).strip()
    return out if len(out) >= min_len else ""


__all__ = [
    "DEFAULT_UA", "DEFAULT_DELAY", "DATA_ROOT",
    "decode_html", "RobotsGate", "PoliteFetcher", "FetchStats",
    "SiteLock", "Checkpoint", "JsonlWriter", "read_jsonl", "clean_content", "now_iso",
]
