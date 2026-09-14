"""LLM Response Validator — checks every AI response for accuracy violations."""
import re
from typing import List, Tuple


class ResponseValidator:
    """Validates LLM responses for accuracy, citation quality, and safety."""

    # Patterns that indicate hallucination
    HALLUCINATION_PATTERNS = [
        (r'(一定能|保证|绝对会|100%|百分之百|必定|必然)', '绝对化表述'),
        (r'(可以治疗|能治病|药方|偏方|秘方)', '医疗建议（禁止）'),
        (r'(保证.*收益|稳赚|必涨|内幕)', '投资建议（禁止）'),
        # k48 P1：「跟着我买」类诱导式承诺同属投资建议（brief 点名的真违规
        # 形态，原模式未覆盖；与上方同一条 detail 文案，仍进 violations）。
        (r'(跟着我买|跟我买|包赚|包您赚)', '投资建议（禁止）'),
    ]

    # ────────────────────────────────────────────────────────────────
    # k48 P1：否定/劝阻语境豁免（2026-09-15）。
    # 背景（用户实机 2026-09-14 20:11:24 实测日志）：
    #   `Accuracy issue in stream response:
    #     [{'type':'absolute_claim','severity':'error',
    #       'detail':'投资建议（禁止）: 稳赚'}]`
    # 而原文是**提醒用户别被"稳赚"话术带走**（劝阻/否定语境，非建仓建议）。
    # 守卫只记日志不改回复（chat_stream.py）→ 误报会淹没真违规、失去信噪比。
    # 判据（只收窄误报、不放走真违规）：
    #   命中片段的**紧前**≤_NEG_WINDOW 字符内出现劝阻/否定词（别被/不要/警惕/
    #   谨防/不要相信…，中间可夹"相信任何"等 ≤6 个非句读字符），或命中片段
    #   **紧后**同句 12 字内出现劝阻收尾词（话术/带走/不要信/别信/陷阱…）
    #   → 视为"提及/提醒"而非"承诺" → 豁免。
    # 「别错过，稳赚不赔」这类否定词与命中词隔着别的语义（"错过"）→ 仍判违规。
    # ────────────────────────────────────────────────────────────────

    # 劝阻/否定词（紧前窗口内，允许中间夹 ≤6 个非句读字符）
    _NEGATION_BEFORE = re.compile(
        r'(?:别被|别听|别信|别让|别上|不要被|不要听|不要相信|不要轻信|不要|'
        r'不能|不可|切勿|切莫|勿|莫|切忌|谨防|警惕|小心|当心|避免|防止|'
        r'远离|拒绝|劝阻|提醒)[^，。！？；;：:、\n]{0,6}$')
    # 劝阻/提醒收尾（紧后窗口内、同句内）——"稳赚"话术带走 / ，请务必警惕
    _NEGATION_AFTER = re.compile(
        r'^[^。！？!?\n]{0,12}?(?:话术|说法|陷阱|套路|带走|忽悠|骗人|骗子|'
        r'骗局|夸大|不可信|别当真|不要信|别信|别被|警惕|当心|小心|谨慎|'
        r'提防|防范)')
    _NEG_WINDOW = 8
    # 引号（"提及"形态标记）：命中词被引号包住 = 在**引用**该说法，不是在承诺
    _QUOTE_CHARS = '“”"\'「」『』《》'

    @staticmethod
    def _in_negation_context(response: str, start: int, end: int) -> bool:
        """命中片段是否处于劝阻/否定语境（k48 P1）。

        判据（其一即可）：
        - **前**：命中片段前 _NEG_WINDOW 字内有劝阻/否定词（同句内、允许
          中间夹"相信任何"等少量字）——最强证据（"别被'稳赚'话术带走"）；
        - **后**：命中词**被引号包住**（= 在引用该说法）且其后同句 12 字内
          有劝阻收尾词（"承诺'保证收益'的都是骗子" / "'稳赚'，请务必警惕"）。
          引号前置条件是必须的——否则「跟着我买，保证收益翻倍，请警惕风险」
          这种"真承诺 + 尾巴挂个警惕"会被误放行（k48-r2 Minor）。
        """
        before = response[max(0, start - ResponseValidator._NEG_WINDOW):start]
        if ResponseValidator._NEGATION_BEFORE.search(before):
            return True
        quoted = (start > 0
                  and response[start - 1] in ResponseValidator._QUOTE_CHARS) or (
                      end < len(response)
                      and response[end] in ResponseValidator._QUOTE_CHARS)
        if not quoted:
            return False
        after = response[end:end + 12]
        return bool(ResponseValidator._NEGATION_AFTER.match(after))

    # Missing required elements
    REQUIRED_CITATION_PATTERN = r'【.+?·.+?】".+?"'

    def validate(self, response: str, engine_data_used: bool = True) -> dict:
        """
        Validates an LLM response for accuracy violations.

        Args:
            response: The LLM response text to validate.
            engine_data_used: Whether engine data was used (for context).

        Returns:
            dict with keys:
                - passed: bool, True if no errors
                - score: int, 0-100 quality score
                - violations: list of violation dicts
                - has_citation: bool, whether classical citation found
                - engine_terms_preserved: list of engine terms found
        """
        violations = []

        # 1. Check for absolute claims
        for pattern, desc in self.HALLUCINATION_PATTERNS:
            matches = re.finditer(pattern, response)
            # k48 P1：只把"承诺性表述"当违规——命中片段处于劝阻/否定语境
            # （"别被'稳赚'话术带走"）时是提醒用户，不是建仓建议，跳过。
            hits = [m for m in matches
                    if not self._in_negation_context(response, m.start(), m.end())]
            if hits:
                violations.append({
                    "type": "absolute_claim",
                    "severity": "error",
                    "detail": f"{desc}: {hits[0].group(0)[:50]}"
                })

        # 2. Check for classical citations
        has_citation = bool(re.search(self.REQUIRED_CITATION_PATTERN, response))
        if not has_citation and len(response) > 200:
            violations.append({
                "type": "missing_citation",
                "severity": "warning",
                "detail": "长回复缺少古籍引用"
            })

        # 3. Check for engine data preservation
        # (engine_data_used flag indicates if engine was called)

        score = 100
        error_count = sum(1 for v in violations if v["severity"] == "error")
        warn_count = sum(1 for v in violations if v["severity"] == "warning")
        score -= error_count * 25
        score -= warn_count * 10

        return {
            "passed": error_count == 0,
            "score": max(0, score),
            "violations": violations,
            "has_citation": has_citation,
        }
