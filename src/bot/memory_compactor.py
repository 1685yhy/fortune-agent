"""L2 会话增量摘要（方案 §5.4，Claude Code 式触发压缩）。

对话上下文超阈值时触发：早期消息分块滚动摘要，每块产出
`<summary>`（对话要点/用户状态/关键结论/未完事项）+ `<memories>`
（值得长期记住的持久事实 → 转 L3），新摘要替换旧摘要（摘要的摘要，多级收敛）。

关键设计：
- 触发式（非每轮）：输入 token 估算 > 窗口 × trigger_ratio 才压缩
- 尾部最近轮次始终原样保留（近期原文保真），只有"旧消息"被压缩
- 旧消息按窗口 25% 分块，块 N 的摘要带入块 N+1（滚动渐进式）
- 压缩失败降级：保留最近 3 轮 + 截断占位摘要（degraded=True）
- 纯函数式设计：llm_fn 可注入（测试用），不直接依赖 handler
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 摘要输出格式（Claude Code 式）：
# <summary> 对话要点（用户状态/关键结论/未完事项）
# <memories> 持久事实，每行一条：type|content|confidence|ttl_days
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)
_MEMORIES_RE = re.compile(r"<memories>(.*?)</memories>", re.S)
MEMORY_TYPES = ("profile", "preference", "topic", "event")


@dataclass
class CompactResult:
    """一次 L2 压缩的结果。"""
    summary_text: str                 # 新摘要（替换旧摘要）
    memories: List[dict] = field(default_factory=list)   # 持久事实 → L3
    old_count: int = 0                # 被压缩的旧消息条数
    recent_count: int = 0             # 原样保留的尾部消息条数
    total_tokens: int = 0             # 输入总 token 估算
    summary_tokens: int = 0           # 摘要 token 估算
    compression_pct: float = 0.0      # 压缩率（旧消息维度）
    degraded: bool = False            # True = LLM 失败降级（保留3轮+截断）
    blocks: int = 0                   # 分块数


def _estimate_tokens(text: str) -> int:
    """与 handler.estimate_tokens 相同的中文 token 估算（1 token ≈ 1.5 汉字）。

    独立实现避免与 handler 的循环导入；逻辑保持一致。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    ascii_n = len(text) - cjk
    return max(1, int(cjk / 1.5 + ascii_n / 4.0) + 1)


class MemoryCompactor:
    """L2 触发式增量摘要器。

    Args:
        api_key: LLM API key（llm_fn 注入时可不传）
        model: 摘要模型名
        window_limit: 上下文窗口上限（token），默认 16384
        trigger_ratio: 触发阈值（输入 > 窗口×该比例 → 压缩），默认 0.7
        chunk_ratio: 单块占窗口比例（分块上限），默认 0.25
        llm_fn: 可注入的 LLM 调用函数
               (api_key, messages, model=..., max_tokens=..., temperature=...,
                timeout=...) -> str；默认 deepseek_anthropic_completion
    """

    # 分块摘要提示词（中文，Claude Code 式输出格式）
    _CHUNK_PROMPT = """你是对话记忆压缩器。用户与命理助手（易理明灯）的对话很长，
请把"早期对话"压缩成高密度摘要，供后续对话注入上下文（压缩率目标 90%+）。

输出格式（严格使用下面的 XML 标签，不要 markdown 代码块、不要多余说明）：
<summary>
对话要点：…（3-6 条，每条一句话）
用户状态：…（情绪/处境/心态）
关键结论：…（已给出的命理结论/共识）
未完事项：…（用户还想知道的/未解决的问题）
</summary>
<memories>
持久事实，每行一条，格式：类型|内容|置信度0-1|TTL天数(空=长期)
类型只能是 profile(画像/八字) / preference(偏好) / topic(主题) / event(事件)
例如：
profile|用户八字已排盘：庚午 辛巳 乙酉 壬午（日主乙木）|1.0|
event|用户正在找工作|0.9|90
</memories>

规则：
- 只压缩"旧消息"；对话中明确出现的用户个人信息（八字、工作/感情状态等）务必写进 memories
- 摘要用简洁中文，保留关键数字/干支/结论，丢弃寒暄与重复表述
- 无持久事实时 <memories> 内写「无」
    """
    _ROLLING_LABEL = "此前已积累的摘要（压缩新块时参考，不要重复展开）："

    def __init__(
        self,
        api_key: str = "",
        model: str = "deepseek-v4-flash",
        window_limit: int = 16384,
        trigger_ratio: float = 0.7,
        chunk_ratio: float = 0.25,
        llm_fn: Optional[Callable] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.window_limit = max(512, int(window_limit))
        self.trigger_ratio = max(0.1, min(0.95, float(trigger_ratio)))
        self.chunk_ratio = max(0.1, min(0.5, float(chunk_ratio)))
        self._llm = llm_fn or self._default_llm

    # ------------------------------------------------------------
    # 触发判断
    # ------------------------------------------------------------

    def estimate_input_tokens(self, messages: List[dict]) -> int:
        """估算一组消息的总 token（role 开销 + 内容）。"""
        total = 4 * len(messages)  # 每条 role 标记等开销
        for m in messages:
            total += _estimate_tokens(m.get("content", "") or "")
        return total

    def should_compact(self, messages: List[dict]) -> bool:
        """是否触发压缩：输入 token > 窗口 × trigger_ratio。"""
        if not messages:
            return False
        return self.estimate_input_tokens(messages) > self.window_limit * self.trigger_ratio

    # ------------------------------------------------------------
    # 压缩主流程
    # ------------------------------------------------------------

    def compact(self, messages: List[dict], prev_summary: str = "",
                prev_memories: Optional[List[str]] = None) -> CompactResult:
        """对旧消息执行增量压缩。

        流程（方案 §5.4）：
        1. 拆分：从最新往回累计 token 到预算（窗口×(1-chunk_ratio)），
           之前的为"旧消息"；尾部最近轮次原样保留
        2. 分块：旧消息按窗口×chunk_ratio 分块（每块摘要调用不超限）
        3. 滚动渐进式：块 N 的摘要+memories 带入块 N+1
        4. 新摘要替换旧摘要（prev_summary 折叠进第一块）

        Args:
            messages: [{role, content}, ...] 按时间正序的完整对话
            prev_summary: 上次的摘要文本（摘要的摘要，多级收敛）
            prev_memories: 上次提取的持久事实内容列表

        Returns:
            CompactResult；LLM 全部失败时降级为最近 3 轮+截断（degraded=True）
        """
        n = len(messages)
        result = CompactResult(summary_text="", memories=[],
                               total_tokens=self.estimate_input_tokens(messages))
        if not messages:
            return result
        result.recent_count, old_messages = self._split(messages)
        result.old_count = len(old_messages)
        if not old_messages:
            # 全部在尾部预算内 → 无需压缩（触发条件满足但拆分后无旧消息）
            result.summary_text = prev_summary
            result.recent_count = n
            return result

        blocks = self._chunk(old_messages)
        result.blocks = len(blocks)

        rolling_parts: List[str] = []
        if prev_summary:
            rolling_parts.append(prev_summary)
        if prev_memories:
            rolling_parts.append("；".join(prev_memories))
        rolling = ("\n".join(rolling_parts)) if rolling_parts else ""

        memories_acc: List[dict] = []
        last_summary = ""
        ok_blocks = 0
        for block in blocks:
            try:
                summary, memories = self._summarize_block(block, rolling)
            except Exception as e:
                logger.warning("L2 摘要块失败（跳过，保留前序摘要）: %s", e)
                continue
            if not summary:
                continue
            ok_blocks += 1
            last_summary = summary
            memories_acc.extend(memories)
            # 滚动：本块摘要 + memories 带入下一块
            rolling = summary + "\n" + self._ROLLING_LABEL + "\n" + \
                "\n".join(m["content"] for m in memories_acc[-20:])
            result.memories = memories_acc

        result.blocks = ok_blocks or len(blocks)
        if last_summary:
            result.summary_text = last_summary
        else:
            # 降级：保留最近 3 轮原文 + 截断（方案 §5.4 降级路径）
            result.summary_text = self._fallback_summary(old_messages)
            result.degraded = True
        result.summary_tokens = _estimate_tokens(result.summary_text)
        old_tokens = self.estimate_input_tokens(old_messages)
        if old_tokens > 0:
            result.compression_pct = round(
                max(0.0, 1.0 - result.summary_tokens / old_tokens), 4)
        return result

    # ------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------

    def _split(self, messages: List[dict]) -> (int, List[dict]):
        """拆分新旧：从最新往回累计到尾部预算（窗口×(1-chunk_ratio)）。

        Returns:
            (recent_count, old_messages) — 尾部最近轮次原样保留
        """
        recent_budget = int(self.window_limit * (1.0 - self.chunk_ratio))
        recent_budget = max(64, recent_budget)
        used = 0
        split_idx = 0
        for i in range(len(messages) - 1, -1, -1):
            used += self.estimate_input_tokens([messages[i]])
            if used > recent_budget:
                split_idx = i + 1
                break
        # 尾部至少保留一条（当前轮保底）
        if split_idx >= len(messages):
            split_idx = len(messages) - 1
        return len(messages) - split_idx, messages[:split_idx]

    def _chunk(self, messages: List[dict]) -> List[List[dict]]:
        """按单块 token 上限（窗口×chunk_ratio）顺序分块。"""
        chunk_budget = max(64, int(self.window_limit * self.chunk_ratio))
        blocks: List[List[dict]] = []
        cur: List[dict] = []
        used = 0
        for m in messages:
            t = self.estimate_input_tokens([m])
            if cur and used + t > chunk_budget:
                blocks.append(cur)
                cur = []
                used = 0
            cur.append(m)
            used += t
        if cur:
            blocks.append(cur)
        return blocks

    def _summarize_block(self, block: List[dict], rolling: str) -> (str, List[dict]):
        """摘要单个消息块，返回 (summary, memories)。"""
        lines = []
        for m in block:
            role = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"{role}: {(m.get('content') or '')[:200]}")
        prompt_parts = [self._CHUNK_PROMPT]
        if rolling:
            prompt_parts.append(f"\n{self._ROLLING_LABEL}\n{rolling[:1500]}")
        prompt_parts.append("\n【对话内容】\n" + "\n".join(lines))
        text = self._llm(
            self.api_key,
            [{"role": "user", "content": "\n".join(prompt_parts)}],
            model=self.model,
            max_tokens=1200,
            temperature=0.3,
            timeout=60.0,
        )
        return self._parse_output(text or "")

    def _parse_output(self, text: str) -> (str, List[dict]):
        """解析 <summary> 与 <memories>。"""
        summary = ""
        m = _SUMMARY_RE.search(text)
        if m:
            summary = m.group(1).strip()
        memories: List[dict] = []
        m = _MEMORIES_RE.search(text)
        if m:
            for line in m.group(1).strip().splitlines():
                line = line.strip().lstrip("-").strip()
                if not line or line in ("无", "暂无"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                etype, content = parts[0], parts[1] if len(parts) > 1 else ""
                if etype not in MEMORY_TYPES or not content:
                    continue
                confidence = 0.8
                ttl = None
                try:
                    if len(parts) > 2 and parts[2]:
                        confidence = float(parts[2])
                except (ValueError, TypeError):
                    pass
                try:
                    if len(parts) > 3 and parts[3]:
                        ttl = int(parts[3])
                except (ValueError, TypeError):
                    pass
                memories.append({
                    "type": etype,
                    "subject": content.split("：", 1)[0][:20] if "：" in content else content[:20],
                    "content": content,
                    "confidence": confidence,
                    "ttl_days": ttl,
                })
        return summary, memories

    def _fallback_summary(self, old_messages: List[dict]) -> str:
        """降级摘要：保留最近 3 轮原文 + 截断。"""
        tail = old_messages[-6:]
        lines = ["[摘要降级]（摘要生成失败，保留最近内容）"]
        for m in tail:
            role = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"{role}: {(m.get('content') or '')[:100]}")
        text = "\n".join(lines)
        return text[:800]

    def _default_llm(self, api_key: str, messages: list, **kwargs) -> str:
        """默认 LLM 调用：DeepSeek Anthropic 兼容端点。"""
        from src.llm.client import deepseek_anthropic_completion
        return deepseek_anthropic_completion(api_key, messages, **kwargs)
