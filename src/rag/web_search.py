"""网络检索工具（阶段 5）— **k46 统一联网搜索能力**（多引擎瀑布，全免费零密钥）。

对外仍是两个稳定接口（调用方零改动）：
  - `search_web(keywords, limit)`   → [{title, url, text, site_name, ...}]
  - `web_search_available()`        → 配置中任一引擎可达即 True
内部收敛为**单一实现**：引擎适配器（多引擎）→ 瀑布式（达标即停）→
结构化检索包（去重合并 + 多源交叉验证 + 局部失败隔离 + 注入过滤）。

引擎（k46 本机实测，2026-09-14）：
  - `bing`  cn.bing.com/search   现有主源，实测可达（li.b_algo 解析）
  - `so360` www.so.com/s         实测可达，`data-mdurl` 内联真实 URL（无需二次跳转）
  - `baidu` www.baidu.com/s      实测可达；需先访问首页取 cookie + Referer，
                                 真实 URL 在容器 mu 属性（锚点是 /link?url= 跳转）
  - `sogou` www.sogou.com/web    **实测被反爬拦截**（302 → /antispider/）；H5 版
                                 为纯 JS 壳（结果 XHR 异步拉取，页面无可解析结果），
                                 故不在默认集，适配器只做「拦截检测 + 如实上报」，
                                 不编造解析逻辑（详见 _search_sogou）
零密钥、零付费源（用户红线：不花钱）；配置见 WEB_SEARCH_ENGINES。

⚠️ 停用说明（2026-08-10）：原智谱 Web Search API（open.bigmodel.cn）欠费，
用户决定改用完全免费的抓取方案，不再使用智谱；原智谱调用逻辑完整保留在
_search_zhipu_legacy() 内，不再被调用，若后续恢复付费可在此处重新启用。

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
import threading
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
SEARCH_TIMEOUT = 15.0  # 单引擎搜索请求超时（秒；**单次**，非整次调用）
# ---- 整次调用的挂钟预算（审查 Important-2：瀑布上界必须与工具执行超时预算一致）----
# 工具层预算：capability_registry「搜索」timeout_s=20s（handler fut.result 同值）。
# 工具通道单次调用 = ① `web_search_available()`（探测阶段 ≤ PROBE_TOTAL_BUDGET_S，
# 30s 缓存，健康时 0 次请求）+ ② `search_web_structured()`（含其内部探测与各引擎分片）。
# 两个常量按下式锁死：PROBE_TOTAL_BUDGET_S + SEARCH_TOTAL_BUDGET_S = 19s < 20s
# → 内部超时确定性先触发：返回已有结果，不触发 handler「执行超时」丢弃 + 重试
# （僵尸线程上界也回到该值，见 handler._run_with_timeout 注释）。
SEARCH_TOTAL_BUDGET_S = 14.0
# 可达性探测总预算（秒）：首个可达即停是常态，但多引擎都不可达时不能逐个叠满
# HEALTH_TIMEOUT（3×5s 会吃光瀑布预算）→ 探测阶段整体 ≤ 一次探测超时；
# 且单个引擎的探测分片 ≤ 剩余预算/剩余引擎数（复审 R4）：首个引擎黑洞不得
# 把预算吃光、让后续可达引擎从不被探测。
PROBE_TOTAL_BUDGET_S = 5.0
# 单引擎探测分片下限（秒）：低于此值不再细分（受 left 钳制，不会越预算）
MIN_PROBE_SLICE_S = 1.0
# 单个引擎至少要有的时间片（秒）：剩余预算低于此值 → 不再开新引擎（stop_reason=budget）
MIN_ENGINE_SLICE_S = 2.0
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

# ---------------------------------------------------------------------------
# C-3 搜索质量：长 query 关键词精简（2026-08-27，web_search.py 首次改动）
# ---------------------------------------------------------------------------
# 长中文 query（如「2026年教育行业政策 最新动向 双减 职业教育」）整段提交
# Bing 常返回垃圾结果——堆叠修饰词稀释相关性。精简策略（纯规则、无新依赖）：
#   ① 剥句首请求/语气前缀（请问/帮我查…）
#   ② 剥当前年份前缀/整段（2026-08-27 真实抓取复现：Bing 对含当前年份的
#      query 返回「年份专题」垃圾块——百科/日历/节假日通知，实质忽略其他
#      检索词；'2026年教育行业政策' 的 top3 全是 2026 百科/节假日/日历表。
#      剥离后 '教育行业政策 双减' 正常返回教育部/双减真结果。仅当前年份；
#      历史年份（1999年…）无此问题且是真实内容词，保留；纯年份 query 保留）
#   ③ 剥句尾问句尾巴（…是什么/…怎么样/…怎么办，可多重叠加剥离）
#   ④ 按空白/标点切段
#   ⑤ 移除修饰性堆叠段（整段匹配，绝不误删内容词——「怎么看八字」单段整体保留）
#   ⑥ 同义改写「人工智能」→「AI」（2026-08-27 冒烟复现：Bing CN 对含
#      「人工智能」的 query 返回「人工」词典释义垃圾块——百科/读音/组词，
#      引擎侧分词缺陷，任何长度精简都绕不开；改写后实测绕过，仅多段 query）
#   ⑦ 保留前 2-3 个内容段
# 短 query（≤1 段）原样返回——精简只针对长句堆叠场景。
# 误伤评估：精简丢失的只有高频修饰词与当前年份（内容词全部保留）；若 LLM
# 意图本就查年份本身（「2026年」），年份保留。宁简勿繁。
_QUERY_JUNK_PREFIXES = (
    "请问", "帮我查一下", "帮我搜一下", "帮我查查", "帮我找找",
    "帮我查", "帮我搜", "帮我找", "查一下", "搜一下", "查查",
    "搜搜", "帮我", "请",
)
_QUERY_JUNK_TAILS = (
    "的最新动向", "的最新动态", "的最新消息", "的最新情况",
    "怎么样", "怎么办", "是怎样", "怎样", "如何", "为什么",
    "是什么", "是啥", "咋样", "怎么看", "怎么分析", "怎么弄",
    "吗", "呢", "呀", "吧",
)
# 单独成段的修饰性堆叠词（整段匹配删除）
_QUERY_STOP_SEGMENTS = frozenset({
    "最新动向", "最新动态", "最新消息", "最新情况", "最新",
    "最近", "近期", "现在", "目前", "今年", "今天",
    "怎么样", "怎么办", "怎样", "如何", "为什么", "是啥",
    "是什么", "有没有", "有什么", "有哪些", "有啥", "哪些", "哪里",
    "情况", "消息", "动向", "动态",
})
_QUERY_SEG_SPLIT_RE = re.compile(r"[\s,，、;；。.!！?？:：|/]+")


# k46（真实对照暴露，2026-09-14）：**粘连**在内容词上的修饰词/疑问填充词。
# 上面 ⑤ 只处理「单独成段」的堆叠词；无空格的中文长句里它们直接粘在内容词上，
# 于是整句被引擎按修饰词检索——实测「最近AI监管有什么新规定」两个系统（本仓
# Bing/360/百度 + agent-search-mcp 的 bing）top1 全是歌曲《最近》/词典释义「最近」，
# 内容词完全没参与匹配 → 归一化后命中正常结果。
#
# ⚠️ 边界安全（2026-09-14 修复，审查 Important-1）：初版两个正则**串联单删**会切出
# 残句——「现在还有哪些国家对中国免签」删「有哪些」再删「现在」→「还国家对中国免签」、
# 「最新的政策」删「最新」→「的政策」、「最近的天气如何」→「的天气」。
# 现规则：修饰词与它带的连接词/助词**整簇原子匹配**（现在还有哪些 / 最新的 / 还有），
# 且剥离结果必须过边界校验（≥2 字、不以孤立虚词开头，见 _QUERY_ORPHAN_HEADS）；
# 校验不过 → 该次剥离作废、保留原值（宁可不剥，绝不切残句）。
_QUERY_GLUED_FILLER_RE = re.compile(
    r"(?:现在|目前|如今|眼下|最近|近期|今天|今年|当前)?"
    r"(?:还|又|也|都)?"
    r"(?:有|是)"
    r"(?:(?:一些|些)?(?:什么|哪些|啥|哪几种|哪几个|多少))"
)
_QUERY_GLUED_LEAD_RE = re.compile(
    r"^(?:最新|最近|近期|现在|目前|今天|今年|眼下)"
    r"(?:还有|又|也|都|的|地)?(?=.{2,})"
)
# 剥完**不允许**留在串首的孤立虚词/半截词（命中 → 剥离作废、保留原值）。
# 「确」为跨词边界护栏：「最近的确很热」剥出「确很热」（的确 被切开）→ 回退。
_QUERY_ORPHAN_HEADS = frozenset("的得了来款地确还就才而而且及或和与也都又是")
# 句首孤立助词可安全丢弃（「最新的政策」→「的政策」→「政策」；年份剥离同型）。
_QUERY_LEAD_PARTICLES = frozenset("的")


def _peel_leading_particle(text: str) -> str:
    """丢弃句首孤立助词（仅 的；且需剩余 ≥2 字）——防跨步骤残留残句。"""
    while len(text) >= 3 and text[0] in _QUERY_LEAD_PARTICLES:
        text = text[1:].lstrip()
    return text


def _peel_boundary_safe(text: str, pat: "re.Pattern[str]") -> str:
    """按 pat 剥离**一处**匹配；结果不过边界校验 → 换下一处匹配，全无 → 原样返回。"""
    for m in pat.finditer(text):
        cand = _peel_leading_particle(" ".join(
            (text[:m.start()] + text[m.end():]).split()))
        if len(cand) < 2 or cand[0] in _QUERY_ORPHAN_HEADS:
            continue    # 剥了会成残句（孤立虚词/失去主语）→ 该位置不剥
        return cand
    return text


def _strip_glued_modifiers(q: str) -> str:
    """剥离粘连的修饰前缀与疑问填充词（见上方注释）；边界安全，宁不剥不切残句。"""
    out = (q or "").strip()
    if not out:
        return out
    for pat in (_QUERY_GLUED_FILLER_RE, _QUERY_GLUED_LEAD_RE):
        out = _peel_boundary_safe(out, pat)
    return _peel_leading_particle(out)


def _strip_query_tail(q: str) -> str:
    """剥句尾问句尾巴（多重叠加：…怎么样吗 → 吗 → 怎么样）。"""
    while q:
        for t in _QUERY_JUNK_TAILS:
            if q.endswith(t):
                q = q[: -len(t)].rstrip("。？！!? ")
                break
        else:
            return q
    return q


def _simplify_query(keywords: str, max_keep: int = 3) -> str:
    """C-3 搜索质量：长 query 精简为最多 max_keep 个内容关键词段。

    策略见 _QUERY_STOP_SEGMENTS 等模块注释；短 query（≤1 段）原样返回。
    纯字符串规则，无网络/无新依赖；调用方（_search_bing）在 query 构造处接入。
    """
    from datetime import date

    q = (keywords or "").strip()
    if not q:
        return ""
    for pref in _QUERY_JUNK_PREFIXES:
        if q.startswith(pref):
            q = q[len(pref):].strip()
            break
    # 当前年份剥离（句首前缀，含粘连形态「2026年教育行业政策」）：
    # Bing「年份专题」垃圾块根因（见模块注释②）。剥离后为空（纯年份 query）
    # → 保留原值（本就查询年份本身，绝不产出空 query）。
    year_prefix_re = re.compile(rf"^{date.today().year}年?")
    stripped = year_prefix_re.sub("", q, count=1)
    if stripped.strip():
        q = stripped
    q = _strip_query_tail(q)
    segs = [s for s in _QUERY_SEG_SPLIT_RE.split(q) if s]
    if len(segs) <= 1:
        # k46：年份前缀剥离会留下前导空格（'2026年 教育行业政策' → ' 教育行业政策'）
        # ——空段交给引擎会稀释匹配（实测两引擎对该形态返回 0 条），统一 strip
        return _strip_glued_modifiers(q.strip()).strip()
    # 多段 query：「人工智能」→「AI」（绕过 Bing CN『人工』词典释义缺陷，见模块注释⑥）
    segs = [s.replace("人工智能", "AI") for s in segs]
    kept = [s for s in segs if s not in _QUERY_STOP_SEGMENTS]
    # 整段年份剥离（「教育政策 2026年」句尾/句中形态；仅当前年份）
    year_seg_re = re.compile(rf"^{date.today().year}年?$")
    if len(kept) > 1:
        kept = [s for s in kept if not year_seg_re.match(s)]
    if not kept:
        kept = segs[:1]  # 极端：全为修饰段 → 保第一段，绝不为空
    return _strip_glued_modifiers(" ".join(kept[:max_keep]))


_avail: Optional[bool] = None
_avail_at: float = 0.0
# 缓存: {cache_key: (expire_at, results|检索包)}
_result_cache: dict[str, tuple[float, dict]] = {}
# 百度会话客户端（进程内单例；预热 cookie，见 _baidu_client）
_baidu_client_obj: Optional["httpx.Client"] = None
_baidu_client_lock = threading.Lock()
# 引擎最近一次**成功**时刻（本进程内）：可达性判定里「冷却中仍算可用」的证据（Minor-3）
_engine_ok_at: dict[str, float] = {}
# 该证据的有效期（秒）：超过则不再当作「当前可达」的依据（长断网不许靠陈旧成功硬标可用）
ENGINE_OK_EVIDENCE_TTL = 600.0


def _engine_recently_ok(engine: str, now: Optional[float] = None) -> bool:
    """本进程内该引擎是否**最近**成功过（探测/检索成功都会记录）。"""
    ts = _engine_ok_at.get(engine)
    if not ts:
        return False
    return (time.time() if now is None else now) - ts <= ENGINE_OK_EVIDENCE_TTL

# ---------------------------------------------------------------------------
# k46 全局限速（审查 Important-3）：进程级、**每引擎**最小间隔（QPS 上限）+ 并发上限。
# ---------------------------------------------------------------------------
# 只有 ENGINE_SPACING_S 的顺序间隔挡不住多用户并发：10 个用户同秒各问一句不同问句
# → 20 个 bing/360 请求同时发出（缓存只对同 query 生效），对照对象的
# `dist/infrastructure/rate-limiter.js`（按引擎间隔，如 duckduckgo 1200ms）正是
# 我们唯一没移植的部件。这里做进程级令牌桶：拿到「下一个可发起时刻」才发；
# 拉不到槽位/等不到 → **快速降级到下一引擎**（不排队堆积、不打第三方）。
# 引擎级隔离：不同引擎各自独立（不互相互斥）；顺序间隔 ENGINE_SPACING_S 保留
# （它管单次调用内不同引擎之间，这里的 min_interval 管同引擎跨调用/跨线程）。
ENGINE_MIN_INTERVAL_S = {"bing": 1.0, "so360": 1.0, "baidu": 2.0, "sogou": 1.0}
ENGINE_MAX_CONCURRENCY = 1      # 每引擎同时在途请求上限
RATE_LIMIT_WAIT_S = 2.0         # 最长排队等待（秒；= 最严间隔）超出即降级到下一引擎


class _EngineRateLimiter:
    """进程级引擎限速（并发槽位 + 最小间隔，零依赖）。

    - `acquire(timeout)`：拿不到并发槽位 → 立刻 False（不排队）；间隔未到 → 最多等
      `timeout` 秒，仍等不到 / 预约失败 → False。True 表示已占用槽位，调用方必须
      在 finally 里 `release()`。
    - 预约在锁内完成：并发线程各自拿到**错开**的发起时刻，实测速率 ≤ 1/min_interval。
    """

    __slots__ = ("min_interval", "_sem", "_lock", "_next_at")

    def __init__(self, min_interval: float, max_concurrency: int = 1) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._sem = threading.BoundedSemaphore(max(1, int(max_concurrency)))
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self, timeout: float = RATE_LIMIT_WAIT_S) -> bool:
        if not self._sem.acquire(blocking=False):
            return False        # 并发上限已满 → 快速降级（不堆积）
        wait = 0.0
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            if wait > max(0.0, timeout):
                self._sem.release()
                return False
            self._next_at = max(now, self._next_at) + self.min_interval
        if wait > 0:
            time.sleep(wait)
        return True

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:      # 防御：重复 release
            pass


_rate_limiters: dict[str, _EngineRateLimiter] = {}
_rate_limiters_lock = threading.Lock()


def _engine_limiter(engine: str) -> _EngineRateLimiter:
    """取（懒建）某引擎的进程级限速器；配置从模块常量读（测试可改后 reset）。"""
    lim = _rate_limiters.get(engine)
    if lim is not None:
        return lim
    with _rate_limiters_lock:
        lim = _rate_limiters.get(engine)
        if lim is None:
            lim = _EngineRateLimiter(ENGINE_MIN_INTERVAL_S.get(engine, 1.0),
                                     ENGINE_MAX_CONCURRENCY)
            _rate_limiters[engine] = lim
        return lim


# ===========================================================================
# k46 统一搜索：引擎注册表 + 配置 + 结构化「检索包」公共件
# ===========================================================================

# 全量引擎（适配器实现齐全）；默认启用集见 DEFAULT_ENGINES
KNOWN_ENGINES = ("bing", "so360", "baidu", "sogou")
# 默认启用：本机实测可达的零密钥引擎（sogou 实测被反爬拦截 → 不入默认集，
# 用户可用 WEB_SEARCH_ENGINES 显式打开）
DEFAULT_ENGINES = ("bing", "so360", "baidu")
ENGINE_LABELS = {
    "bing": "Bing", "so360": "360搜索", "baidu": "百度", "sogou": "搜狗",
}
# 瀑布停止：结果数达标 **且** 至少来自 2 个引擎（多源交叉验证）即停
MIN_STOP_ENGINES = 2
# 引擎失败后的冷却（秒）——避免对已被反爬/故障的站点反复施压（抓取克制）
ENGINE_FAIL_COOLDOWN = 120.0
# 不进冷却的失败原因：站点是通的、只是这轮没解析出东西（不该罚站整个引擎）
NO_COOLDOWN_REASONS = frozenset({"parse_miss"})
# 引擎之间的最小间隔（秒）——顺序瀑布、不并发轰炸
ENGINE_SPACING_S = 0.25
# 单条结果摘要在入 prompt 前的截断长度上限
SNIPPET_HARD_MAX = 300


def _parse_engine_set(raw: Optional[str]) -> tuple[str, ...]:
    """解析 WEB_SEARCH_ENGINES 配置值 → 引擎名元组（未知名忽略、保序去重）。

    空/未设置 → DEFAULT_ENGINES。
    """
    if raw is None or not str(raw).strip():
        return tuple(DEFAULT_ENGINES)
    out: list[str] = []
    for name in str(raw).split(","):
        name = name.strip().lower()
        if name in KNOWN_ENGINES and name not in out:
            out.append(name)
    return tuple(out) if out else tuple(DEFAULT_ENGINES)


def configured_engines() -> tuple[str, ...]:
    """当前启用的引擎（WEB_SEARCH_ENGINES 环境变量，进程内缓存）。"""
    global _engines_cache
    if _engines_cache is None:
        import os
        _engines_cache = _parse_engine_set(os.getenv("WEB_SEARCH_ENGINES"))
        logger.info("k46 联网搜索引擎集: %s", ",".join(_engines_cache))
    return _engines_cache


# ---------------------------------------------------------------------------
# 注入特征过滤（参考 agent-search-mcp 的 Prompt 注入检测；k46）
# ---------------------------------------------------------------------------
# 抓来的网页文本会进 prompt（工具块/自动注入段）。恶意页面可埋「忽略以上指令」
# 类文本劫持模型，故在**入库前**（适配器出口、缓存前）统一过滤：
#   ① 指令劫持特征 → 中性化为「［已过滤］」（不整条丢弃：结果本身仍可能有价值）
#   ② 伪角色/伪协议标记（system: / <|im_start|> 等）→ 同上
#   ③ 控制字符与零宽字符 → 剔除（防用不可见字符绕行特征匹配）
#   ④ 形如 [n] 的角标 → 改写为 (n)：我们的引用体系用 [n] 编号，网页原文里的
#      [1]/[2] 会被模型误当成可用引用编号（污染引用校验）。
# ⚠️ 收紧（2026-09-14 修复，审查 Important-4）：初版多条正则**全部可选组**，等于对
# 「扮演/假装/你就是」等普通动词、以及任意行首 `system:`/`系统：` 做单字面替换 →
# 正常中文被就地打码（「你就是你，不一样的烟火」「他在电影里扮演一位医生」
# 「系统：iOS 17.4 正式版发布」「user: 如何配置代理服务器」），与我们批评 MCP
# 「按全角标点误报中文」同构。现规则：**只在真正的注入模板命中时过滤**——
# 角色劫持必须共现角色宾语（系统/助手/AI…）、伪角色行必须同行带注入线索、
# 索要提示词必须「你(的)+系统/prompt/提示词」共现；普通词汇不再单字命中。
_INJECTION_PATTERNS = (
    # ① 伪协议/特殊 token 标记（模型侧 token，正常网页文本不会出现）
    re.compile(r"<\s*\|?\s*(?:im_start|im_end|system|assistant|endoftext)\s*\|?\s*>", re.I),
    # ②③ 英文指令覆盖模板（必须共现 previous/above 等覆盖范围）
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|above|prior|foregoing)\s+"
               r"(?:instructions?|prompts?|rules?)", re.I),
    re.compile(r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|above|prior)\s+"
               r"(?:instructions?|prompts?|rules?)", re.I),
    # ④ 中文指令覆盖模板：**必须**带覆盖范围/归属（以上/之前/所有/你的…），
    #    否则「无视规则」这类正常措辞会被误伤
    re.compile(r"(?:忽略|无视|忘记|不要理会|抛弃|覆盖)(?:掉)?"
               r"(?:以上|上述|之前|前面|上面|先前|所有|全部|一切|任何|你(?:之前)?的|您的)"
               r"(?:(?:的|所有|全部|一切|任何))*"
               r"(?:指令|指示|要求|规则|设定|提示|prompt)", re.I),
    # ⑤ 角色劫持模板：**必须**共现角色宾语（系统/助手/AI/模型/越狱…）；
    #    「扮演一位医生」「假装成顾客」「你就是你」不再命中。
    #    ⚠️ 复审 R1 修复：动词必须**盖住「X 成」形态**（假装成/扮演成/伪装成）——
    #    收窄时漏了「成」，`假装成开发者模式` 等 4 例由「能拦」变「漏拦」，
    #    这里用 `(?:扮演|假装|伪装)成?` 把动词族收敛回来（含 root）。
    re.compile(r"(?:(?:扮演|假装|伪装)成?|你就是|你现在是)\s*(?:一个|一位|一名)?\s*"
               r"(?:新的?)?\s*"
               r"(?:系统|助手|AI|人工智能|模型|root|越狱|无限制|不受限制|开发者模式|管理员)",
               re.I),
    # ⑥ 伪角色行（system:/系统：）：仅当**同一行**带注入线索（忽略/扮演/接管/
    #    you are…）才算——「系统：iOS 17.4 正式版发布」这类正常正文不再命中
    re.compile(r"^[ \t]*(?:system|assistant|user|developer|系统|开发者|管理员)[ \t]*[:：][^\n]*?"
               r"(?:忽略|无视|忘记|不要理会|扮演|假装|伪装|泄露|接管|劫持|越狱|绕过|"
               r"从现在起|从现在开始|接下来你|你是|ignore|disregard|pretend|"
               r"you\s+are|act\s+as|from\s+now\s+on|instructions?)",
               re.I | re.M),
    # ⑦ 索要系统提示词：**必须**「（你/您）+ 的/所有 +（系统/初始…）+ 提示词/prompt…」共现；
    #    「输出指令」「告诉我你的设定」这类擦边不再命中
    re.compile(r"(?:输出|打印|泄露|重复|复述|展示|告诉)\s*(?:一下)?\s*(?:你|您)\s*"
               r"(?:的|所有|全部)\s*(?:系统|初始|原始|隐藏)?\s*"
               r"(?:提示词|prompt|指令|指示|规则|设定)", re.I),
    re.compile(r"<\s*(?:script|iframe)\b", re.I),
)
# 不可见/控制字符（保留 \t \n）
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ﻿]")
# 角标式引用编号（我们的引用体系占用了 [n]）
_CITE_LIKE_RE = re.compile(r"\[(\d{1,2})\]")
_INJECTION_PLACEHOLDER = "［已过滤］"


def _neutralize_citation_like(text: str) -> str:
    """把网页原文里的 [1]/[12] 改成 (1)/(12)，避免模型误当可用引用编号。"""
    return _CITE_LIKE_RE.sub(r"(\1)", text)


def sanitize_search_text(text: str, max_chars: int = SNIPPET_HARD_MAX) -> tuple[str, bool]:
    """网页文本入 prompt 前的注入过滤 → (清洗后文本, 是否命中注入特征)。

    - 命中注入特征 → 该片段替换为 ［已过滤］（结果保留，不让恶意文本进 prompt）
    - 剔除控制/零宽字符；[n] 角标改写为 (n)；折叠空白；按 max_chars 截断
    """
    raw = text or ""
    if not raw:
        return "", False
    clean = _CTRL_RE.sub("", raw)
    flagged = False
    for pat in _INJECTION_PATTERNS:
        if pat.search(clean):
            flagged = True
            clean = pat.sub(_INJECTION_PLACEHOLDER, clean)
    clean = _neutralize_citation_like(clean)
    clean = " ".join(clean.split())[:max_chars]
    return clean, flagged


# ---------------------------------------------------------------------------
# 去重合并 + 多源交叉验证置信度
# ---------------------------------------------------------------------------
# 归一化时丢弃的跟踪参数（同一落地页带不同跟踪参数 = 同一条）。
# Minor-4（审查修复）：初版把 f/us/sa/src/ref… 当**前缀**匹配，等于把 `?f=1`/`?f=2`、
# `?us=alice`/`?us=bob`、`?format=pdf`/`?format=html` 全并成一条（误合并丢真实差异结果，
# 如 Discuz `forum.php?f=1`/`?f=2`）→ 改精确参数名 + 明确的跟踪前缀。
# 另：`wd` 是百度检索词参数，`f`/`us` 是论坛真实参数 → 不再当跟踪参数丢。
_TRACKING_PARAM_PREFIXES = ("utm_", "spm", "rsv_")
_TRACKING_PARAM_EXACT = frozenset({
    "from", "fr", "src", "ref", "share", "sa", "ved", "eqid",
})


def normalize_url(url: str) -> str:
    """URL 归一化（去重键）：小写主机、去 www.、丢 fragment/跟踪参数、去尾斜杠。"""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(u)
        scheme = (parts.scheme or "http").lower()
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return u.lower()
        netloc = host + (f":{parts.port}" if parts.port else "")
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
                if not (k.lower() in _TRACKING_PARAM_EXACT
                        or k.lower().startswith(_TRACKING_PARAM_PREFIXES))]
        path = parts.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return urlunsplit((scheme, netloc, path, urlencode(kept), ""))
    except Exception:  # noqa: BLE001 — 归一化失败退回原串
        return u


# ---------------------------------------------------------------------------
# k46 相关性闸门（真实用户路径验收暴露，2026-09-14）
# ---------------------------------------------------------------------------
# 复现：搜「2026年新能源车销量」→ 归一化正确（`新能源车销量`），但 Bing 结果页顶部塞的是
# 「新（汉语汉字）」释义卡（`li.b_algo` 里就是 baike.baidu.com/item/新），旧「达标即停」只
# 看**条数**（`len(merged) >= limit` 且 ≥2 引擎出过结果）→ Bing 的 5 条（多为释义卡）就
# 达标停了，实测全相关的百度（9 条）/360（6 条）**根本没被问**。
# 现规则：① 引擎结果必须与查询**实质匹配**才算「贡献」（`_engine_rows_relevant`），
# 否则不算达标、继续问下一个引擎；② 合并后**按相关性排序**（相关优先），而不是
# 「谁先出结果谁说了算」；③ 全都不相关 → 包上标 `relevance="none"`（别当答案）。
# 相关性 = 查询词项在「标题+摘要」里的**命中数**（零依赖：中文 2-gram，无分词库；
# 拉丁/数字词整体作一项）。2-gram 命中是相关性**代理指标**、不做语义理解——
# 目标是拦「引擎侧释义卡/软性重定向」这类字面都不沾的结果，不追求语义级判定。
_QUERY_TERM_RE = re.compile(r"[A-Za-z0-9]{2,}")
_CJK_RUN_RE = re.compile("[\\u4e00-\\u9fff]+")
RELEVANCE_MIN_RATIO = 0.34      # 单条结果命中率下限
RELEVANCE_MAX_NEED = 2          # 命中数下限（词项少时不因 ratio 放宽）


def _query_terms(query: str) -> tuple:
    """查询 → 相关性命中的词项（中文 2-gram + 拉丁/数字词，去重保序）。"""
    q = (query or "").strip()
    if not q:
        return ()
    out: list[str] = []
    for tok in _QUERY_TERM_RE.findall(q):
        low = tok.lower()
        if low not in out:
            out.append(low)
    for run in _CJK_RUN_RE.findall(q):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram not in out:
                out.append(gram)
    return tuple(out)


def _min_relevance_hits(n_terms: int) -> int:
    """判「相关」的命中数下限：≤1 个词项 → 全命中；否则 ≥2 且 ≥34%。"""
    if n_terms <= 1:
        return n_terms
    return min(n_terms, max(RELEVANCE_MAX_NEED,
                            int(n_terms * RELEVANCE_MIN_RATIO + 0.999)))


def _relevance_hits(row, terms) -> int:
    """该结果命中的查询词项数（标题 + 摘要）。terms 为空 → 0（不判相关）。"""
    if not terms:
        return 0
    blob = f"{row.get('title') or ''} {row.get('text') or ''}".lower()
    return sum(1 for t in terms if t in blob)


def _engine_rows_relevant(rows, terms) -> bool:
    """引擎结果集是否与查询实质匹配（瀑布「达标」闸门）。

    - terms 为空（纯符号 query）→ 不判，按原有「非空即贡献」语义
    - 否则：相关条数 ≥ min(2, 返回条数)（只有 1 条时即那 1 条必须相关）
    """
    if not rows:
        return False
    if not terms:
        return True
    need = _min_relevance_hits(len(terms))
    hit = sum(1 for r in rows if _relevance_hits(r, terms) >= need)
    return hit >= min(2, len(rows))


def _confidence(source_engines: list[str], text: str) -> int:
    """单条结果置信度（1-3）：多引擎命中 = 3；单引擎有摘要 = 2；仅标题 = 1。"""
    if len({e for e in source_engines if e}) >= 2:
        return 3
    return 2 if (text or "").strip() else 1


def merge_engine_results(per_engine, limit: int, terms=None) -> list[dict]:
    """多引擎原始结果 → 去重合并后的结构化列表（k46 §3）。

    - 按 URL 归一化去重；同一 URL 多引擎命中 → 合并 source_engines（置信度 3）
    - 摘要取「更长的那个」（信息量优先），标题取首个非空
    - 排序：**相关优先**（真实路径验收修复，见 `_query_terms` 上方注释）→ 置信度降序
      → 命中数降序 → 首次出现顺序（稳定）；`terms` 省略（None）时全部同档
      → 单引擎配置下输出顺序仍与旧实现逐条一致（行为兼容有测试锁）
    - 每条带 `source_engines`（来源引擎，透传到对外结果）与 `relevance_hits`/`relevant`
    - 入 prompt 前统一注入过滤（sanitize_search_text）——**只覆盖 title/text**；
      url/site_name 原样进 citation（Minor-1：引擎侧 URL 已百分号编码且各适配器
      只收 http(s)，这里补一道协议白名单兜底：非 http(s) 行直接丢弃，防
      `javascript:`/`data:` 类伪 URL 进注入块）
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    need = _min_relevance_hits(len(terms)) if terms else 0
    for engine, rows in per_engine:
        for row in rows or []:
            url = (row.get("url") or "").strip()
            if not url or not url.lower().startswith(("http://", "https://")):
                continue
            key = normalize_url(url) or url
            title, _tf = sanitize_search_text(row.get("title") or "", 120)
            text, flagged = sanitize_search_text(row.get("text") or "")
            if not title and not text:
                continue
            hits = _relevance_hits(row, terms)
            item = merged.get(key)
            if item is None:
                item = {
                    "title": title,
                    "url": url,
                    "text": text,
                    "site_name": row.get("site_name") or "",
                    "source_engines": [engine],
                    "injection_flagged": flagged,
                    "relevance_hits": hits,
                    "relevant": (not terms) or hits >= need,
                }
                merged[key] = item
                order.append(key)
            else:
                if engine not in item["source_engines"]:
                    item["source_engines"].append(engine)
                if len(text) > len(item["text"]):
                    item["text"] = text
                if not item["title"] and title:
                    item["title"] = title
                item["injection_flagged"] = item["injection_flagged"] or flagged
    items = [merged[k] for k in order]
    for it in items:
        it["confidence"] = _confidence(it["source_engines"], it["text"])
    # 相关优先 → 置信度 → 命中数（稳定排序：全同档时保持首次出现顺序）
    items.sort(key=lambda x: (0 if x.get("relevant") else 1,
                              -x["confidence"], -x.get("relevance_hits", 0)))
    return items[:limit]


def _relevance_grade(results: list[dict]) -> str:
    """结果集相关性分级（对外标注）：ok / weak / none。

    - ok：达标条数 ≥ min(2, 条数)（正常可用）
    - weak：有字面沾边（命中 ≥1 词项）但未达标 → 可能只是改写措辞，**不判垃圾**
    - none：**一条都没沾上查询词**（如引擎侧释义卡/软性重定向）→ 消费方不得当答案
    """
    if not results:
        return "none"
    rel = sum(1 for it in results if it.get("relevant"))
    if rel >= min(2, len(results)):
        return "ok"
    touched = sum(1 for it in results if it.get("relevance_hits"))
    return "weak" if touched else "none"


def _result_package(results: list[dict], engines_tried, engines_ok,
                    partial_failures, stop_reason: str, query: str,
                    cache_hit: bool = False, relevance: Optional[str] = None) -> dict:
    """结构化「检索包」（k46 §3）：结果 + 元信息（搜了哪些引擎/停止原因/局部失败）。

    `relevance`：结果集与查询的相关性分级（ok/weak/none，见 `_relevance_grade`）——
    真实路径验收修复引入：`none` 表示「各引擎都只给了不相关结果」，消费方据此**不要把
    垃圾当答案**（如实告知/降级），而不是把释义卡之类当检索结果。
    """
    if len({e for it in results for e in it.get("source_engines", [])}) >= 2:
        confidence = 3
    elif results:
        confidence = 2
    else:
        confidence = 1
    return {
        "results": results,
        "query": query,
        "engines_tried": list(engines_tried),
        "engines_ok": list(engines_ok),
        "stop_reason": stop_reason,
        "partial_failures": list(partial_failures),
        "confidence": confidence,
        "cache_hit": cache_hit,
        "relevance": relevance if relevance is not None else _relevance_grade(results),
    }


class EngineError(Exception):
    """单引擎失败（局部失败隔离用）——reason 为机器可读短标识。"""

    def __init__(self, engine: str, reason: str, detail: str = ""):
        super().__init__(f"{engine}:{reason}:{detail}"[:200])
        self.engine = engine
        self.reason = reason
        self.detail = detail


_engine_cooldown: dict[str, float] = {}
_engines_cache: Optional[tuple[str, ...]] = None


def _engine_cooling(engine: str) -> bool:
    return time.time() < _engine_cooldown.get(engine, 0.0)


def reset_engine_state() -> None:
    """测试用：清空引擎配置缓存与失败冷却。"""
    global _engines_cache
    _engines_cache = None
    _engine_cooldown.clear()


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


def _probe_engine(engine: str, timeout: float = HEALTH_TIMEOUT) -> bool:
    """单引擎可达性探测（轻量：一次请求 + 反爬页识别）。"""
    try:
        if engine == "bing":
            return _probe_bing_reachable(timeout)
        if engine == "so360":
            r = httpx.get(SO360_SEARCH_URL + "?q=" + quote("测试"), headers=SO360_HEADERS,
                          timeout=timeout, follow_redirects=True)
            return r.status_code == 200 and "so.com/verify" not in str(r.url)
        if engine == "baidu":
            r = _baidu_client(timeout).get(
                BAIDU_SEARCH_URL + "?wd=" + quote("测试") + "&ie=utf-8",
                headers={"Referer": "https://www.baidu.com/"}, timeout=timeout)
            return r.status_code == 200 and not _is_baidu_challenge(r.text)
        if engine == "sogou":
            r = httpx.get(SOGOU_SEARCH_URL + "?query=" + quote("测试"), headers=SOGOU_HEADERS,
                          timeout=timeout, follow_redirects=True)
            return r.status_code == 200 and not _is_sogou_antispider(r.text, str(r.url))
    except Exception:  # noqa: BLE001 — 网络不可达
        return False
    return False


def web_search_available(force: bool = False) -> bool:
    """联网搜索是否可用：**配置中任一引擎可达**即 True（可达性探测 + 30s 缓存）。

    探测按配置顺序进行，首个可达即停（健康时只有 1 次探测请求，不给站点压力）；
    全部不可达 → False（工具注册处标 unavailable、prompt 不宣传、search_web 返回 []）。
    失败后周期性重试，网络恢复后自动恢复。**探测阶段整体不超过
    PROBE_TOTAL_BUDGET_S**（Important-2：多引擎都不可达时逐个叠满 HEALTH_TIMEOUT
    会吃光瀑布预算；调用方 search_web_structured 另按整次调用 deadline 收口）。

    冷却中的引擎：**不再拿「冷却」当可用证据**（Minor-3：整网断时冷却原因也可能是
    network，旧逻辑会在不探测的情况下返回 True，与 docstring 自相矛盾）——只有本进程
    内**最近（≤ ENGINE_OK_EVIDENCE_TTL）成功过**的引擎（`_engine_ok_at`）才在冷却期
    算可用；否则按引擎失败处理、继续看下一个引擎。冷却引擎本身仍不额外探测
    （不落井下石，保持 120s 冷却语义）。
    """
    global _avail, _avail_at
    now = time.time()
    if not force and _avail is not None and now < _avail_at:
        return _avail
    engines = configured_engines()
    probe_stop = time.monotonic() + PROBE_TOTAL_BUDGET_S
    ok = False
    for i, eng in enumerate(engines):
        left = probe_stop - time.monotonic()
        if left <= 0.05:
            # 探测预算耗尽：未探测的引擎按「最近成功过」证据兜底（Important-2：探测
            # 不得吃光瀑布预算；证据过期则不认——长时间断网不许靠陈旧成功硬标可用）
            ok = any(_engine_recently_ok(e, now) for e in engines)
            break
        if _engine_cooling(eng):
            if _engine_recently_ok(eng, now):
                ok = True   # 冷却中但最近成功过（曾通的、只是临时失败）
                break
            continue        # 冷却且无近期成功证据 → 不拿它当可用证据，继续下一个
        # ⚠️ 复审 R4 修复：探测也按**公平份额**分片（left/剩余引擎数，末位拿走全部剩余）
        # ——否则首个引擎黑洞（吃满 HEALTH_TIMEOUT）会把预算吃光，后续可达引擎**从不被
        # 探测** → available() 恒 False 且 30s 内无解除路径（旧版会探到下一个引擎返回 True）。
        probe_timeout = min(HEALTH_TIMEOUT, left,
                            max(MIN_PROBE_SLICE_S, left / (len(engines) - i)))
        if _probe_engine(eng, timeout=probe_timeout):
            ok = True
            _engine_ok_at[eng] = now
            break
    _avail = ok
    _avail_at = now + AVAIL_CACHE_TTL
    if not ok:
        logger.info("联网搜索不可达（引擎集 %s），网络检索标记 unavailable",
                    ",".join(engines))
    return ok


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

    返回 [{title, url, text, site_name}]（site_name 留空字符串）；解析不到结果
    → 返回 []（引擎无结果，不算失败）；请求/解析异常 → 抛 EngineError
    （k46：由瀑布层记录 partial_failures，局部失败不影响其它引擎）。
    """
    raw = (keywords or "").strip()
    if not raw:
        return []
    # C-3 搜索质量（2026-08-27）：长 query 精简为 2-3 个内容关键词段
    # （详见 _simplify_query）。精简只影响实际提交的 query；缓存键与日志
    # 仍用原始 keywords（结果与原始查询一一对应，行为对调用方透明）。
    query = _simplify_query(raw)[:QUERY_MAX_CHARS]
    url = f"{BING_SEARCH_URL}?q={quote(query)}&mkt=zh-CN"
    try:
        r = httpx.get(url, headers=BING_HEADERS, timeout=timeout,
                      follow_redirects=True)
        r.raise_for_status()
        parser = _BingResultParser()
        parser.feed(r.text)
        results = parser.results[:limit]
        if not results and "b_algo" in r.text:
            # 结果页形态在（有容器标记）却解析不到 → 如实上报解析失败（防静默劣化）
            raise EngineError("bing", "parse_miss")
        if results:
            logger.debug("Bing 检索 '%s'（精简自 '%s'）→ %d 条",
                         query[:40], raw[:40], len(results))
        return results
    except EngineError:
        raise
    except httpx.HTTPStatusError as e:
        raise EngineError("bing", f"http_{e.response.status_code}") from e
    except Exception as e:  # noqa: BLE001 — 抓取/解析失败 → 局部失败隔离
        raise EngineError("bing", "network", str(e)[:80]) from e


# ===========================================================================
# k46 引擎适配器：360搜索 / 百度 / 搜狗（Bing 见上）
# ===========================================================================
SO360_SEARCH_URL = "https://www.so.com/s"
SO360_HEADERS = {
    "User-Agent": BING_HEADERS["User-Agent"],
    "Accept-Language": "zh-CN,zh;q=0.9",
}
BAIDU_SEARCH_URL = "https://www.baidu.com/s"
BAIDU_HOME_URL = "https://www.baidu.com/"
BAIDU_HEADERS = {
    "User-Agent": BING_HEADERS["User-Agent"],
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
SOGOU_SEARCH_URL = "https://www.sogou.com/web"
SOGOU_HEADERS = {
    "User-Agent": BING_HEADERS["User-Agent"],
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.sogou.com/",
}
# 百度风控页特征（实测：短时间内重复请求会从结果页切到「百度安全验证」）
_BAIDU_CHALLENGE_MARKERS = ("百度安全验证", "wappass.baidu.com", "verify.baidu.com")
# 百度容器里的非内容占位域名（广告/推荐位，实测 mu 指向这些）
_BAIDU_PLACEHOLDER_HOSTS = ("nourl.ubs.baidu.com", "recommend_list.baidu.com",
                            "baidu.com/link", "baidu.php")

_VOID_TAGS = frozenset({"br", "img", "input", "meta", "link", "hr", "area", "base",
                        "col", "embed", "source", "track", "wbr", "param"})


class _BlockParser(HTMLParser):
    """容器块解析基类：按 class 命中起始容器，按标签深度闭合（k46 引擎适配器共用）。

    子类实现 `_open_container(tag, attr_map)`（返回 True 表示此标签开启一个新容器）、
    `_capture(attr_map)`（容器内每个起始标签调用，自行判定是否采集文本）、
    以及 `_close_container()`（容器闭合时产出结果）。
    文本采集用 `_text_<field>` 缓冲 + `_field_depth_<field>` 深度区间（含子标签文本）。
    """

    container_tag = "div"

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[dict] = []
        self._depth = 0
        self._cstart: Optional[int] = None      # 容器起始深度
        self._fields: dict[str, tuple[int, list]] = {}   # field → (深度, 缓冲)
        self._done: dict[str, str] = {}                  # field → 已闭合文本

    # --- 采集辅助 ---
    def _field_begin(self, field: str) -> None:
        if field not in self._fields and field not in self._done:
            self._fields[field] = (self._depth, [])

    def _field_end(self, field: str, depth: int) -> None:
        if self._fields.get(field, (None, None))[0] == depth:
            self._done[field] = self._field_text(field)
            self._fields.pop(field, None)

    def _field_text(self, field: str) -> str:
        if field in self._fields:
            return " ".join("".join(self._fields[field][1]).split())
        return self._done.get(field, "")

    def _in_field(self, field: str) -> bool:
        return field in self._fields

    # --- HTMLParser 钩子 ---
    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        attr_map = dict(attrs)
        if tag not in _VOID_TAGS:
            self._depth += 1
        if self._cstart is None:
            if tag == self.container_tag and self._open_container(tag, attr_map):
                self._cstart = self._depth
        else:
            self._capture(tag, attr_map)

    def handle_data(self, data: str) -> None:
        for depth, buf in self._fields.values():
            if self._depth >= depth:
                buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._cstart is not None:
            for field in list(self._fields):
                self._field_end(field, self._depth)
            if self._depth == self._cstart:
                self._close_container()
                self._cstart = None
                self._fields.clear()
                self._done.clear()
        if self._depth > 0:
            self._depth -= 1

    # --- 子类实现 ---
    def _open_container(self, tag: str, attr_map: dict) -> bool:
        raise NotImplementedError

    def _capture(self, tag: str, attr_map: dict) -> None:
        raise NotImplementedError

    def _close_container(self) -> None:
        raise NotImplementedError


class _So360ResultParser(_BlockParser):
    """360 搜索（so.com）结果页：li.res-list → 首个带 data-mdurl 的 <a> + 摘要。

    `data-mdurl` 是内联的**真实落地页**（锚点本体是 /link?m= 跳转）→ 无需二次
    请求即可拿到真实 URL（省一次抓取，符合"抓取克制"）。摘要取 res-list-summary
    （普通条目）/ g-des、res-desc（垂直卡）。

    ⚠️ Minor-5 限定：按 `li.res-list` 收容器，**垂直聚合卡**（如「某某_客服电话」
    这类卡片）也会被当作一条结果（URL 真实、非污染，但标题/摘要形态与自然结果
    不同、可能无摘要）——「标题最干净」仅对普通结果条目成立。
    """

    container_tag = "li"
    _SUMMARY_CLASSES = ("res-list-summary", "g-des", "res-desc", "res-desc-col")

    def __init__(self) -> None:
        super().__init__()
        self._url = ""

    def _open_container(self, tag: str, attr_map: dict) -> bool:
        self._url = ""
        return "res-list" in (attr_map.get("class") or "").split()

    def _capture(self, tag: str, attr_map: dict) -> None:
        classes = (attr_map.get("class") or "").split()
        if tag == "a" and not self._url:
            md = (attr_map.get("data-mdurl") or "").strip()
            if md.startswith("http"):
                self._url = md
                self._field_begin("title")
        elif (tag in ("p", "div", "span")
              and not self._in_field("title") and not self._in_field("text")
              and not self._field_text("text")):
            if any(c in self._SUMMARY_CLASSES for c in classes):
                self._field_begin("text")

    def _close_container(self) -> None:
        title = self._field_text("title")
        text = self._field_text("text")
        if title and self._url:
            self.results.append({"title": title[:120], "url": self._url,
                                 "text": text[:SNIPPET_CHARS], "site_name": ""})


class _BaiduResultParser(_BlockParser):
    """百度结果页：div.result.c-container[mu] → 标题/真实 URL/摘要。

    百度把**真实落地页**放在容器的 `mu` 属性（锚点本体是 /link?url= 或
    /baidu.php 跳转；逐条跟跳转 = 每条一次额外请求，违背抓取克制）→
    只收 mu 为 http 且非占位域名的容器；mu 缺失（广告/推荐位）整条跳过，
    绝不产出不可解析的跳转链接。摘要挂点是混淆类名（多套并存），按
    data-module="abstract" → class 含 summary-text → class 含 c-abstract
    → class 含 cos-line-clamp 的优先级取第一个非空（容器首块，h3 内不取）。
    """

    container_tag = "div"
    _mu = ""

    def _open_container(self, tag: str, attr_map: dict) -> bool:
        classes = (attr_map.get("class") or "").split()
        self._mu = ""
        # 自然结果容器：result / result-op（实测两种）；广告位类是 EC_result（非本规则）
        if "c-container" not in classes:
            return False
        if not any(c == "result" or c.startswith("result-") for c in classes):
            return False
        mu = (attr_map.get("mu") or "").strip()
        if not mu.startswith("http"):
            return False
        if any(h in mu for h in _BAIDU_PLACEHOLDER_HOSTS):
            return False
        self._mu = mu
        return True

    def _capture(self, tag: str, attr_map: dict) -> None:
        if tag == "h3" and not self._in_field("title") and not self._field_text("title"):
            self._field_begin("title")
            return
        if self._in_field("title") or self._in_field("text"):
            return
        module = (attr_map.get("data-module") or "")
        classes = (attr_map.get("class") or "").split()
        if module == "abstract" or "summary-text" in classes or "c-abstract" in classes:
            self._field_begin("text")

    def _close_container(self) -> None:
        title = self._field_text("title")
        text = self._field_text("text")
        if title:
            self.results.append({"title": title[:120], "url": self._mu,
                                 "text": text[:SNIPPET_CHARS], "site_name": ""})


def _is_baidu_challenge(html_text: str) -> bool:
    """百度风控页（「百度安全验证」）识别 → 该次请求视为被拦截。"""
    head = (html_text or "")[:4000]
    return any(m in head for m in _BAIDU_CHALLENGE_MARKERS)


def _is_sogou_antispider(html_text: str, final_url: str = "") -> bool:
    """搜狗反爬页识别（302 → /antispider/）。"""
    return "antispider" in (final_url or "") or "/antispider" in (html_text or "")[:4000]


def _baidu_client(timeout: float = HEALTH_TIMEOUT) -> httpx.Client:
    """百度会话客户端（进程内单例）：先访问首页取 cookie，再带 Referer 检索。

    实测（2026-09-14）：不带 cookie 直接检索数次即被切到「百度安全验证」；
    先取首页 cookie + Referer 后稳定返回结果页。单例复用避免每轮重新握手。

    ⚠️ Minor-2（审查修复）：单例只在**构造时**吃 timeout——若探测先用
    HEALTH_TIMEOUT=5s 建好客户端，后续真实检索的 15s 超时就被冻结成 5s
    （百度本应兜底的降级态反被 5s 掐死）。现在 timeout 参数仅用于**首次预热**；
    检索/探测一律在 `.get(..., timeout=...)` 上按请求传，单例不冻结超时。
    """
    global _baidu_client_obj
    client = _baidu_client_obj
    if client is not None:
        return client
    with _baidu_client_lock:
        if _baidu_client_obj is None:
            c = httpx.Client(headers=BAIDU_HEADERS, timeout=timeout,
                             follow_redirects=True)
            try:
                c.get(BAIDU_HOME_URL, timeout=timeout)   # 取 cookie（BAIDUID 等）
            except Exception as e:  # noqa: BLE001 — 预热失败不阻塞（检索再试）
                logger.debug("百度预热失败: %s", str(e)[:80])
            _baidu_client_obj = c
        return _baidu_client_obj


def reset_baidu_client() -> None:
    """测试用：丢弃百度会话客户端（下次调用重建）。"""
    global _baidu_client_obj
    with _baidu_client_lock:
        if _baidu_client_obj is not None:
            try:
                _baidu_client_obj.close()
            except Exception:  # noqa: BLE001
                pass
        _baidu_client_obj = None


def _search_so360(query: str, limit: int = 5,
                  timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """360 搜索（so.com）免费抓取 → [{title, url, text, site_name}]；失败抛 EngineError。"""
    url = f"{SO360_SEARCH_URL}?q={quote(query)}"
    try:
        r = httpx.get(url, headers=SO360_HEADERS, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise EngineError("so360", f"http_{e.response.status_code}") from e
    except Exception as e:  # noqa: BLE001
        raise EngineError("so360", "network", str(e)[:80]) from e
    parser = _So360ResultParser()
    parser.feed(r.text)
    results = parser.results[:limit]
    if not results and "res-list" in r.text:
        raise EngineError("so360", "parse_miss")
    return results


def _search_baidu(query: str, limit: int = 5,
                  timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """百度免费抓取 → [{title, url, text, site_name}]；失败/被风控抛 EngineError。

    先取首页 cookie（单例客户端），再带 Referer 检索；命中风控页 → EngineError
    （reason=anti_bot，调用方降级并冷却，不反复施压）。
    """
    url = f"{BAIDU_SEARCH_URL}?wd={quote(query)}&ie=utf-8"
    try:
        r = _baidu_client(timeout).get(url, headers={"Referer": BAIDU_HOME_URL},
                                       timeout=timeout)   # 按请求传，单例不冻结超时（Minor-2）
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise EngineError("baidu", f"http_{e.response.status_code}") from e
    except Exception as e:  # noqa: BLE001
        raise EngineError("baidu", "network", str(e)[:80]) from e
    if _is_baidu_challenge(r.text):
        raise EngineError("baidu", "anti_bot", "百度安全验证")
    parser = _BaiduResultParser()
    parser.feed(r.text)
    results = parser.results[:limit]
    if not results and "c-container" in r.text:
        raise EngineError("baidu", "parse_miss")
    return results


def _search_sogou(query: str, limit: int = 5,
                  timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """搜狗：**本机实测不可用**（桌面版被反爬拦截）→ 如实上报，不编造解析。

    实测（2026-09-14，本机）：www.sogou.com/web 一律 302 → /antispider/（带
    cookie 预热、Referer、多种 UA 均如此，IP 级风控）；H5 版
    wap.sogou.com/web/searchList.jsp 返回 200 但为纯 JS 壳（324KB 中无服务端
    渲染的结果节点，结果走 XHR 异步拉取）→ 无稳定可解析的结果形态。
    故：这里只做「拦截检测 + 如实上报」（EngineError reason=anti_bot），
    **不写没有真实样本支撑的解析逻辑**；引擎默认关闭，可用 WEB_SEARCH_ENGINES
    显式开启（在能直连的网络环境下若上游形态稳定，再按真实样本补解析）。
    """
    url = f"{SOGOU_SEARCH_URL}?query={quote(query)}"
    try:
        r = httpx.get(url, headers=SOGOU_HEADERS, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise EngineError("sogou", f"http_{e.response.status_code}") from e
    except Exception as e:  # noqa: BLE001
        raise EngineError("sogou", "network", str(e)[:80]) from e
    if _is_sogou_antispider(r.text, str(r.url)):
        raise EngineError("sogou", "anti_bot", "反爬拦截页")
    return []


_ENGINE_SEARCHERS = {
    "bing": _search_bing,
    "so360": _search_so360,
    "baidu": _search_baidu,
    "sogou": _search_sogou,
}


def search_web_structured(keywords: str, limit: int = 5,
                          timeout: float = SEARCH_TIMEOUT,
                          engines: Optional[list] = None,
                          budget: Optional[float] = None) -> dict:
    """k46 统一检索：**多引擎瀑布** → 结构化检索包（单一实现，调用方零改动）。

    - 按 `WEB_SEARCH_ENGINES` 顺序逐引擎尝试；每引擎独立超时 + 失败隔离
      （单引擎挂/被反爬/解析异常 → 记 partial_failures，继续下一引擎，不抛断主链）
    - **整次调用挂钟预算**（Important-2）：全程 ≤ SEARCH_TOTAL_BUDGET_S（含可达性
      探测），单引擎分片 = min(调用方 timeout, 剩余预算, 剩余预算/剩余引擎数)；
      预算不足 → 停开新引擎、`stop_reason=budget`、**返回已拿到的结果**，绝不越预算
    - **全局限速**（Important-3）：每引擎进程级最小间隔 + 并发上限，拉不到槽位/等不到
      → 快速降级到下一引擎（`partial_failures[].reason=rate_limited`，不排队堆积）
    - 瀑布停止：结果数 ≥ limit **且** 至少 2 个引擎出过结果 → stop_reason=enough；
      否则跑完配置集 → exhausted；无可用引擎 → unavailable
    - 去重合并 + 多源交叉验证（同 URL 多引擎命中 → source_engines 列表 + 置信度 3）
    - 结果入包前统一注入过滤（sanitize_search_text）
    - 结果缓存 5 分钟 TTL（键含查询与引擎集签名）；失败引擎冷却 120s
    返回 `{results, query, engines_tried, engines_ok, stop_reason,
          partial_failures, confidence, cache_hit}`。
    """
    t0 = time.monotonic()
    total_budget = SEARCH_TOTAL_BUDGET_S if budget is None else max(0.1, float(budget))
    deadline = t0 + total_budget
    raw = (keywords or "").strip()
    if not raw:
        return _result_package([], [], [], [], "empty_query", "")
    engine_list = [e for e in (engines if engines is not None else configured_engines())
                   if e in _ENGINE_SEARCHERS]
    if not engine_list:
        return _result_package([], [], [], [], "unavailable", "")
    if not web_search_available():
        return _result_package([], [], [], [], "unavailable", "")

    cache_key = f"v2:{','.join(engine_list)}:{raw}:{limit}"
    hit = _result_cache.get(cache_key)
    if hit and time.time() < hit[0]:
        logger.info("网络检索 '%s' → 命中缓存 %d 条", raw[:40], len(hit[1]["results"]))
        cached = dict(hit[1])
        cached["cache_hit"] = True
        return cached

    # 提交给各引擎的 query 与 Bing 现状一致（_simplify_query 精简长句）；
    # 同一 query 跨引擎 → 交叉验证/去重才有意义（k46 设计取舍，见报告）
    query = _simplify_query(raw)[:QUERY_MAX_CHARS]
    # 相关性闸门用的词项（真实路径验收修复）：按**实际提交给引擎的 query** 计算
    terms = _query_terms(query)
    per_engine: list = []
    engines_tried: list[str] = []
    engines_ok: list[str] = []
    # 「贡献」= 出过**相关**结果的引擎（空结果不算；只有不相关的垃圾也不算 → 达标判据）
    contributing: set[str] = set()
    failures: list[dict] = []
    stop_reason = "exhausted"

    def _safe_merge_rows() -> list[dict]:
        """合并兜底（Minor-6）：畸形行/未知异常 → 不抛，按空结果继续。"""
        try:
            return merge_engine_results(per_engine, limit, terms)
        except Exception as e:  # noqa: BLE001 — 绝不让异常越出本函数
            logger.warning("k46 结果合并异常（%s）→ 丢弃本轮结果", str(e)[:120])
            return []

    for idx, eng in enumerate(engine_list):
        engines_left = len(engine_list) - idx
        if idx:
            time.sleep(min(ENGINE_SPACING_S, max(0.0, deadline - time.monotonic())))
        remaining = deadline - time.monotonic()
        if remaining < MIN_ENGINE_SLICE_S:
            # 预算不足：不再开新引擎（已拿到的结果照常返回，见 Important-2）
            failures.extend({"engine": e, "reason": "budget"}
                            for e in engine_list[idx:])
            stop_reason = "budget"
            logger.info("k46 预算耗尽（剩余 %.2fs < %.1fs）→ 停开引擎 %s，"
                        "返回已有 %d 个引擎的结果",
                        remaining, MIN_ENGINE_SLICE_S, ",".join(engine_list[idx:]),
                        len(per_engine))
            break
        engines_tried.append(eng)
        if _engine_cooling(eng):
            failures.append({"engine": eng, "reason": "cooldown"})
            continue
        # 单引擎分片：不超过调用方 timeout、不超过剩余预算、且给后续引擎留份额
        slice_s = min(timeout, remaining,
                      max(MIN_ENGINE_SLICE_S, remaining / engines_left))
        limiter = _engine_limiter(eng)
        if not limiter.acquire(RATE_LIMIT_WAIT_S):
            failures.append({"engine": eng, "reason": "rate_limited"})
            logger.info("k46 引擎 %s 全局限速未拿到槽位 → 降级到下一引擎", eng)
            continue
        # 排队等待也算在整次调用预算里：拿到槽位后按剩余预算重新收敛分片
        slice_s = min(slice_s, deadline - time.monotonic())
        if slice_s <= 0:
            limiter.release()
            failures.append({"engine": eng, "reason": "budget"})
            failures.extend({"engine": e, "reason": "budget"}
                            for e in engine_list[idx + 1:])
            stop_reason = "budget"
            break
        try:
            try:
                rows = _ENGINE_SEARCHERS[eng](query, limit, slice_s)
            except EngineError as e:
                if e.reason not in NO_COOLDOWN_REASONS:
                    _engine_cooldown[eng] = time.time() + ENGINE_FAIL_COOLDOWN
                failures.append({"engine": eng, "reason": e.reason})
                logger.warning("k46 引擎 %s 失败（%s）→ 局部失败隔离，继续下一引擎",
                               eng, e.reason)
                continue
            except Exception as e:  # noqa: BLE001 — 适配器异常一律隔离
                _engine_cooldown[eng] = time.time() + ENGINE_FAIL_COOLDOWN
                failures.append({"engine": eng, "reason": "adapter_error"})
                logger.warning("k46 引擎 %s 异常：%s → 局部失败隔离", eng, str(e)[:100])
                continue
        finally:
            limiter.release()
        engines_ok.append(eng)
        _engine_ok_at[eng] = time.time()   # 可达性判定的「最近成功」证据（Minor-3）
        # 相关性闸门：只有**相关**结果才算「贡献」→ 不相关就继续问下一个引擎
        if rows and _engine_rows_relevant(rows, terms):
            contributing.add(eng)
        elif rows:
            failures.append({"engine": eng, "reason": "irrelevant"})
            logger.info("k46 引擎 %s 返回 %d 条但均与查询不相关 → 不计达标，继续下一引擎",
                        eng, len(rows))
        per_engine.append((eng, rows))
        merged = _safe_merge_rows()
        if len(merged) >= limit and len(contributing) >= MIN_STOP_ENGINES:
            stop_reason = "enough"
            break

    # Minor-6：合并/组包在 per-engine 的 try 之外，旧版一旦某适配器产出
    # 非 str/非 dict 行（如 text 为 dict）就会抛到调用方（工具层兜成「执行超时/
    # 异常」+ 重试）。这里兜底为「无结果」而不是异常——search_web() 的
    # 「不抛异常」由结构保证（`_safe_merge_rows` 已兜合并，这里再兜组包）。
    results = _safe_merge_rows()
    try:
        pkg = _result_package(results, engines_tried, engines_ok, failures,
                              stop_reason, query)
    except Exception as e:  # noqa: BLE001 — 绝不让异常越出本函数
        logger.warning("k46 检索包组包异常（%s）→ 按无结果返回", str(e)[:120])
        results = []
        pkg = _result_package([], engines_tried, engines_ok, failures, "error", query)
    if results:
        _result_cache[cache_key] = (time.time() + RESULT_CACHE_TTL, pkg)
        if len(_result_cache) > RESULT_CACHE_MAX:
            _trim_result_cache()
    logger.info("网络检索 '%s' → %d 条（引擎 %s；停止=%s；相关性=%s；局部失败 %d）",
                raw[:40], len(results),
                "+".join(ENGINE_LABELS.get(e, e) for e in engines_ok) or "-",
                stop_reason, pkg.get("relevance"), len(failures))
    if results and pkg.get("relevance") == "none":
        logger.warning("k46 各引擎结果与查询均不相关（疑似引擎侧释义卡/软性重定向）"
                       "→ 已标注 relevance=none，消费方不得当答案使用：'%s'", raw[:40])
    if failures:
        logger.info("k46 局部失败明细：%s",
                    "; ".join(f"{ENGINE_LABELS.get(f['engine'], f['engine'])}={f['reason']}"
                              for f in failures))
    return pkg


def search_web(keywords: str, limit: int = 5,
               timeout: float = SEARCH_TIMEOUT) -> List[dict]:
    """统一联网搜索 → [{title, url, text, site_name, source_engines, confidence}]。

    - **向后兼容**：仍返回结果列表（最多 limit 条），字段为旧字段的超集
      （新增 source_engines/confidence，旧消费方读 title/url/text 不受影响）
    - 多引擎瀑布 + 去重 + 交叉验证 + 局部失败隔离（见 search_web_structured）
    - 整次调用 ≤ SEARCH_TOTAL_BUDGET_S（含探测；工具层预算 20s，见 capability_registry）
    - 结果缓存 5 分钟 TTL；不可用/无结果/异常 → 返回 []（不抛异常；合并/组包异常
      也已兜底为 []，见 search_web_structured 内 Minor-6 注释）
    - 结构化元信息（搜了哪些引擎/停止原因/局部失败）走 search_web_structured
    """
    return search_web_structured(keywords, limit, timeout)["results"]


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
    """测试用：重置可用性缓存、结果缓存、引擎配置/冷却、限速器与百度会话。"""
    global _avail, _avail_at
    _avail = None
    _avail_at = 0.0
    _result_cache.clear()
    _engine_ok_at.clear()
    _rate_limiters.clear()      # 限速器按常量懒建：清掉才能让测试改的小间隔生效
    reset_engine_state()
    reset_baidu_client()
