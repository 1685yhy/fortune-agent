"""Claude API 客户端."""
from dataclasses import dataclass
from typing import List, Union
from anthropic import Anthropic

from .prompts import SYSTEM_PROMPT, USER_CONTEXT_TEMPLATE
from src.engines.bazi import BaziResult
from src.rag.retriever import ChunkResult


@dataclass
class AnalysisResult:
    response: str
    tokens_used: int
    model: str


class FortuneLLM:
    """算命助手 LLM 封装"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5"):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        """Always create a fresh client to avoid 'client has been closed' issues with uvicorn reload."""
        import httpx
        return Anthropic(
            api_key=self.api_key,
            http_client=httpx.Client(timeout=60.0),
        )

    def analyze(
        self,
        chart_data: Union[BaziResult, str],
        references: List[ChunkResult],
        user_question: str,
    ) -> AnalysisResult:
        """基于排盘数据和古籍引用，分析用户问题

        Args:
            chart_data: BaziResult or formatted chart string
        """
        if isinstance(chart_data, str):
            chart_str = chart_data
        else:
            chart_str = self._format_chart(chart_data)
        refs_str = self._format_references(references)

        user_message = USER_CONTEXT_TEMPLATE.format(
            chart_data=chart_str,
            references=refs_str,
            question=user_question,
        )

        # Try API call with retry on closed client
        max_retries = 2
        for attempt in range(max_retries):
            try:
                client = self._get_client()
                response = client.messages.create(
                    model=self.model,
                    max_tokens=2000,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                # Extract text from response (skip ThinkingBlock)
                text_blocks = [b for b in response.content if b.type == "text"]
                reply_text = text_blocks[0].text if text_blocks else response.content[0].text
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    continue  # retry with fresh client
                else:
                    raise

        return AnalysisResult(
            response=reply_text,
            tokens_used=response.usage.output_tokens,
            model=self.model,
        )

    def _format_chart(self, r: BaziResult) -> str:
        """格式化排盘数据为文本"""
        return f"""八字：{' '.join(r.bazi)}
日主：{r.day_master}
五行：{r.wuxing}
十神：{' '.join(r.shishen)}
格局：{r.geju}
用神：{r.yongshen}
大运：{' → '.join(f'{age}岁{ganzhi}' for age, ganzhi in r.dayun[:5])}
神煞：{'、'.join(r.shensha) if r.shensha else '无'}"""

    def _format_references(self, refs: List[ChunkResult]) -> str:
        """格式化古籍引用为文本"""
        lines = []
        for i, ref in enumerate(refs[:15], 1):
            lines.append(f"{i}. 【{ref.source}】\"{ref.text[:300]}...\" (相关度: {ref.score:.2f})")
        if not lines:
            return "（未找到直接相关古籍记载）"
        return "\n".join(lines)
