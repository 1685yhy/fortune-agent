"""k11-E：工具 JSON / 标签 流式 chunk 级过滤器——任何 {tool…} JSON 不落用户可见流。

事故背景（2026-09-06）：polish 带 search_hint 时 prompt 硬要求「先输出
<tool_calls>[{"tool":"web_search",…}]</tool_calls> 再继续」（handler.py:1960-1965）且全程
真流式；_run_tool_loop GLM 二次生成 1732 先流式、1747 才 strip；chat_stream.py:455
出口兜底只作用于最终整段——JSON 已逐 chunk 发到前端。落库零命中 → 纯展示态泄漏。

本类 = 纯规则跨 chunk 状态机（零 LLM、零网络），对每个"发送 chunk"做过滤：
- 完整工具块（<tool_calls>…</tool_calls> / <tool_call>…</tool_call>）任意位置整块移除；
- 流中出现未闭合起始标记 → 自标记处缓冲至闭合后才放行标记前正文：
    · <tool_calls>/<tool_call> → 至闭合标签（可跨 chunk）；
    · [{"tool" / {"tool" （JSON 工单裸形态）→ 按 { [ 括号深度 + 字符串字面量跳过
      跨 chunk 计数，深度归零即块结束；
    · TOOL:（文本标签兜底形态）→ 至行尾换行。
- 缓冲上限防悬挂（异常不闭合时宁可丢也不泄漏）；finish() 丢弃未闭合残块。
复用目标 = strip_tool_calls/_looks_like_tool_echo 同源红线，但本模块独立新建，
tool_calls.py 零改动。
"""
import logging

logger = logging.getLogger(__name__)

# 起始标记表：(open, close_or_None, kind)；kind: tag=闭合标签 / json=括号深度 / line=至换行
_MARKERS = (
    ("<tool_calls>", "</tool_calls>", "tag"),
    ("<tool_call>", "</tool_call>", "tag"),
    ("[{\"tool\"", None, "json"),
    ("{\"tool\"", None, "json"),
    ("TOOL:", None, "line"),
)
_MAX_BUF = 4096  # 悬挂保护上限：超过即丢弃缓冲（防正文被无闭合工单卡死）
_JSON_OPEN = set("{[")
_JSON_CLOSE = {"}": "{", "]": "["}


class ToolJsonChunkFilter:
    """流式块过滤器：feed(chunk_text) → 可见文本增量（可能为空串）。

    用法：每个 LLM 流式调用一个实例（fresh）；流结束后调用 finish() 丢弃残块。
    """

    def __init__(self):
        self._buf = ""

    # -- 内部工具 --------------------------------------------------

    @staticmethod
    def _tail_hold_len(s: str) -> int:
        """s 的尾部可能是某个起始标记的前缀（跨 chunk 切碎）时需扣留的长度。"""
        if not s:
            return 0
        hold = 0
        for mk, _close, _kind in _MARKERS:
            # 后缀是 mk 前缀：s[-k:] == mk[:k]
            for k in range(min(len(s), len(mk) - 1), 0, -1):
                if s[-k:] == mk[:k]:
                    hold = max(hold, k)
                    break
        return hold

    def _json_span(self, s: str) -> int:
        """s 假定以 json 起始标记开头（{[…]）；返回完整 JSON 消费长度，未闭合返回 -1。

        按 { [ 深度计数，跳过字符串字面量（含 \" 转义）——闭合判定不依赖
        "</tool_calls>"（裸工单形态无标签时同样可靠）。
        """
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(s):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in _JSON_OPEN:
                depth += 1
            elif ch in _JSON_CLOSE:
                depth -= 1
                if depth == 0:
                    return i + 1
        return -1

    # -- 对外接口 --------------------------------------------------

    def feed(self, text: str) -> str:
        """处理一段增量，返回可发给用户的文本（空串 = 本段全被过滤/缓冲）。"""
        if not text:
            return ""
        self._buf += text
        out: list = []
        while self._buf:
            # 1) 找最早起始标记
            found = None
            for mk, _close, _kind in _MARKERS:
                idx = self._buf.find(mk)
                if idx != -1 and (found is None or idx < found[1]):
                    found = (mk, idx)
            if found is None:
                # 无完整标记：扣留尾部可能的标记前缀，其余放行
                hold = self._tail_hold_len(self._buf)
                emit_len = len(self._buf) - hold
                if emit_len > 0:
                    out.append(self._buf[:emit_len])
                    self._buf = self._buf[emit_len:]
                    continue
                if len(self._buf) > _MAX_BUF:
                    # 理论上只有"尾部恒为前缀"才会到不了，防御性放行
                    out.append(self._buf)
                    self._buf = ""
                break
            mk, idx = found
            if idx > 0:
                out.append(self._buf[:idx])  # 标记前的正文先放行
            self._buf = self._buf[idx:]
            close, kind = None, None
            for _mk, _c, _k in _MARKERS:
                if _mk == mk:
                    close, kind = _c, _k
                    break
            if kind == "tag":
                j = self._buf.find(close, len(mk))
                if j == -1:
                    break  # 未闭合 → 继续缓冲等待
                self._buf = self._buf[j + len(close):]
                continue
            if kind == "json":
                span = self._json_span(self._buf)
                if span == -1:
                    break  # 未闭合 → 继续缓冲等待
                self._buf = self._buf[span:]
                continue
            if kind == "line":  # TOOL: … 至行尾
                j = self._buf.find("\n")
                if j == -1:
                    if len(self._buf) > _MAX_BUF:
                        self._buf = ""
                        logger.warning("stream_guard: TOOL: 行缓冲超限丢弃")
                    break
                self._buf = self._buf[j + 1:]
                continue
            break  # 理论不可达：防御退出
        if len(self._buf) > _MAX_BUF:
            # 悬挂保护：某标记已开始但超上限仍未闭合 → 整体丢弃（宁漏不泄）
            logger.warning("stream_guard: 工具块缓冲超限整体丢弃（len=%d）", len(self._buf))
            self._buf = ""
        return "".join(out)

    def finish(self) -> str:
        """流结束：丢弃未闭合残块（不落用户可见流），返回空串。"""
        if self._buf:
            logger.info("stream_guard: 流结束丢弃未闭合工具块残留（len=%d）", len(self._buf))
            self._buf = ""
        return ""


def wrap_chunk_filter(stream_cb) -> "callable":
    """给 stream_cb 包一层 chunk 级 JSON 过滤（每次 LLM 流式调用 fresh 实例）。

    返回的新 cb 保持 (evt_type, payload) 协议：chunk 事件的 text/content 过滤后转发，
    非 chunk 事件原样透传。stream_cb 为 None → 返回 None。
    """
    if stream_cb is None:
        return None
    f = ToolJsonChunkFilter()

    def _wrapped(evt_type: str, payload: dict) -> None:
        if evt_type == "chunk" and isinstance(payload, dict):
            raw = payload.get("text", "") if "text" in payload else payload.get("content", "")
            if isinstance(raw, str) and raw:
                cleaned = f.feed(raw)
                if not cleaned:
                    return  # 全过滤/缓冲中：本 chunk 不透传
                new_payload = dict(payload)
                if "text" in new_payload:
                    new_payload["text"] = cleaned
                else:
                    new_payload["content"] = cleaned
                stream_cb(evt_type, new_payload)
                return
        stream_cb(evt_type, payload)

    return _wrapped
