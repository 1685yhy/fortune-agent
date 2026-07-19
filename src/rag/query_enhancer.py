"""Query Enhancement Layer - transforms user colloquial questions into professional retrieval queries using DeepSeek LLM.

Uses the same httpx + DeepSeemk API pattern as src/llm/client.py.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 命理分类标签
CATEGORIES = [
    "bazi", "ziwei", "fengshui", "dream",
    "mianxiang", "qimen", "xingming", "zeri", "general",
]

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

# 系统提示词 - Query Enhancement
SYSTEM_PROMPT = """你是一位专业的中国命理学（玄学）查询增强助手。你的任务是将用户的日常口语化问题改写成专业、精准的命理学术语查询，以便后续的 RAG（检索增强生成）系统能检索到最相关的古籍和知识内容。

请严格遵循以下步骤处理用户的输入：

1. **改写（Rewrite）**：将用户的日常口语问题改写为专业的命理学术语表述。保持原意的同时，使用更正式、更专业的词汇。例如，"我最近运气不好" -> "近期运势低迷，询问流年气运变化及其对事业、财运的影响"。

2. **拆解子查询（Sub-queries）**：如果用户的问题涉及多个方面，拆解成最多3个独立的、更细粒度的子查询，从不同角度覆盖原问题。子查询应当独立完整，可直接用于搜索。

3. **分类（Category）**：判断用户问题属于以下哪一类：bazi（八字）、ziwei（紫微斗数）、fengshui（风水）、dream（梦境/解梦）、mianxiang（面相）、qimen（奇门遁甲）、xingming（姓名学）、zeri（择日）、general（通用/不属于上述分类）。如果跨类别，选择最相关的一个。

4. **关键词提取（Keywords）**：从原问题中提取3-6个核心关键词（术语），用于辅助检索。

请以 JSON 格式返回结果，不要包含任何其他文字。"""

USER_PROMPT_TEMPLATE = """请增强以下用户查询：

原问题: "{query}"

请严格按照以下 JSON 格式返回：
{{
    "rewritten": "改写后的专业查询语句",
    "sub_queries": ["子查询1", "子查询2", "子查询3"],
    "category": "分类标签",
    "keywords": ["关键词1", "关键词2", "关键词3"]
}}"""

FALLBACK_TEMPLATE = """{{
    "rewritten": "请根据以下问题进行专业命理分析: {query}",
    "sub_queries": [],
    "category": "general",
    "keywords": []
}}"""


@dataclass
class EnhancedQuery:
    """增强后的查询结果"""
    original: str           # 原始问题
    rewritten: str          # 改写为专业术语
    sub_queries: list[str]  # 拆解的子查询 (最多3个)
    category: str           # 分类: bazi/ziwei/fengshui/dream/mianxiang/qimen/xingming/zeri/general
    keywords: list[str]     # 抽取的关键词

    def to_dict(self) -> dict:
        return asdict(self)


class QueryEnhancer:
    """Query Enhancement Layer - transforms user colloquial questions into professional retrieval queries.

    Uses DeepSeek Flash model for fast, cheap structured rewriting.
    Follows the same httpx + API pattern as FortuneLLM in src/llm/client.py.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    async def enhance(self, query: str) -> EnhancedQuery:
        """Rewrite user query + decompose into sub-queries + tag category.

        Args:
            query: User's original colloquial question.

        Returns:
            EnhancedQuery with rewritten professional query, sub-queries, category, and keywords.
            On API failure, returns a fallback with original query as rewritten and category="general".
        """
        if not query or not query.strip():
            return EnhancedQuery(
                original=query,
                rewritten=query,
                sub_queries=[],
                category="general",
                keywords=[],
            )

        try:
            return await self._call_deepseek(query)
        except Exception as exc:
            logger.warning("Query enhancement failed for %r: %s", query[:50], exc)
            return self._build_fallback(query)

    def enhance_sync(self, query: str) -> EnhancedQuery:
        """Synchronous wrapper for enhance().

        Args:
            query: User's original colloquial question.

        Returns:
            EnhancedQuery with rewritten professional query, sub-queries, category, and keywords.
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an already-running event loop; create a new one in a separate thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self.enhance(query))
                return future.result()
        else:
            return asyncio.run(self.enhance(query))

    async def _call_deepseek(self, query: str) -> EnhancedQuery:
        """Call DeepSeek API with JSON mode for structured query enhancement."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        user_prompt = USER_PROMPT_TEMPLATE.format(query=query.strip())

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 600,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        resp = await self._client.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
        )
        data = resp.json()

        if "error" in data:
            logger.warning("DeepSeek API error for query enhancement: %s", data["error"])
            return self._build_fallback(query)

        content = data["choices"][0]["message"]["content"]
        return self._parse_response(content, query)

    def _parse_response(self, content: str, original_query: str) -> EnhancedQuery:
        """Parse JSON response from DeepSeek into EnhancedQuery.

        Args:
            content: JSON string from LLM response.
            original_query: Original user query for fallback fields.

        Returns:
            Parsed EnhancedQuery or fallback if parsing fails.
        """
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to parse query enhancement JSON: %s", exc)
            return self._build_fallback(original_query)

        rewritten = parsed.get("rewritten", "")
        if not rewritten:
            rewritten = self._build_fallback(original_query).rewritten

        sub_queries = parsed.get("sub_queries", [])
        if not isinstance(sub_queries, list):
            sub_queries = []

        category = parsed.get("category", "general")
        if category not in CATEGORIES:
            category = "general"

        keywords = parsed.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []

        return EnhancedQuery(
            original=original_query,
            rewritten=rewritten,
            sub_queries=sub_queries[:3],  # max 3 sub-queries
            category=category,
            keywords=keywords,
        )

    def _build_fallback(self, query: str) -> EnhancedQuery:
        """Build a safe fallback EnhancedQuery when API call fails."""
        return EnhancedQuery(
            original=query,
            rewritten=f"请根据以下问题进行专业命理分析: {query}",
            sub_queries=[],
            category="general",
            keywords=[],
        )

    async def close(self):
        """Close the underlying httpx client."""
        await self._client.aclose()
