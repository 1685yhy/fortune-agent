"""测试 LLM 完整流程."""
import sys
sys.path.insert(0, '.')
from src.config import load_settings
from src.engines.bazi import BaziEngine
from src.rag.retriever import Retriever
from src.rag.embedder import Embedder
from src.llm.client import FortuneLLM

settings = load_settings()

# 1. 排盘
engine = BaziEngine()
result = engine.calculate(1990, 5, 20, 15, 0, "北京", "男")
print(f"八字：{' '.join(result.bazi)} | 日主：{result.day_master}")

# 2. 检索古籍
embedder = Embedder()
retriever = Retriever(str(settings.vectordb_dir), embedder)
refs = retriever.search("乙木 财运 事业", top_k=10)
print(f"\n检索到 {len(refs)} 条相关古籍")

# 3. LLM 分析
llm = FortuneLLM(api_key=settings.claude_api_key, model=settings.claude_model)
analysis = llm.analyze(result, refs, "帮我看看事业和财运怎么样？")
print(f"\n{'='*50}")
print(analysis.response)
print(f"\n{'='*50}")
print(f"Tokens: {analysis.tokens_used}, Model: {analysis.model}")
