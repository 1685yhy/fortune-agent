"""易理明灯 Knowledge Graph module (GraphRAG Phase 3).

Provides a complete knowledge graph pipeline for the fortune-telling system:
  - Static entities: 天干, 地支, 五行, 十神, 神煞, 格局
  - Bazi relations: 生, 克, 合, 冲, 刑, 害
  - Chart entities: 44k+ 问真命盘 extracted from SQLite
  - Competitor data: cleaned Q&A from multiple platforms
  - SQLite-backed graph store with fast traversal
  - Community detection (Leiden / Louvain / connected-components)
  - LLM-generated community summaries (DeepSeek flash)
  - Graph retrieval for RAG (local search, global search, chart similarity)
"""

from __future__ import annotations

from typing import Any

from src.kg.builder import (
    BaziRelationBuilder,
    ChartEntityBuilder,
    ClassicBuilder,
    StaticEntityBuilder,
)
from src.kg.community import CommunityDetector
from src.kg.retriever import GraphRetriever
from src.kg.schema import Entity, EntityType, Relation, RelationType, Triple
from src.kg.store import GraphStore


class KnowledgeGraph:
    """Top-level interface for the fortune-telling knowledge graph.

    Usage:
        kg = KnowledgeGraph("kg_data/fortune_kg.db")
        kg.build()                     # Static entities + relations
        kg.build_charts(limit=500)     # Add chart data
        kg.build_classics()            # Add competitor/knowledge data
        print(kg.stats())
        context = kg.retrieve("甲", year_pillar="甲子", ...)
    """

    def __init__(self, db_path: str = "kg_data/fortune_kg.db"):
        self.db_path = db_path
        self.store = GraphStore(db_path)
        self.static_builder = StaticEntityBuilder()
        self.relation_builder = BaziRelationBuilder()
        self.chart_builder = ChartEntityBuilder()
        self.classic_builder = ClassicBuilder()
        self.retriever = GraphRetriever(self.store)
        self.community = CommunityDetector(self.store)

    # ------------------------------------------------------------------
    # Build pipeline
    # ------------------------------------------------------------------

    def build(self) -> KnowledgeGraph:
        """Build the static portion of the KG: entities + relations.

        This is always called first and creates the foundational
        knowledge graph with 天干, 地支, 五行, 十神, 神煞, 格局
        and their inter-relations.
        """
        # Static entities
        entities = self.static_builder.build()
        self.store.add_entities(entities)
        print(f"  Added {len(entities)} static entities")

        # Static relations
        relations = self.relation_builder.build()
        self.store.add_relations(relations)
        print(f"  Added {len(relations)} static relations")

        return self

    def build_charts(self, limit: int = 500) -> KnowledgeGraph:
        """Extract entities and relations from 问真命盘 charts."""
        entities, relations = self.chart_builder.build(limit=limit)
        if entities:
            self.store.add_entities(entities)
            self.store.add_relations(relations)
            print(f"  Added {len(entities)} chart entities, {len(relations)} chart relations")
        else:
            print("  No chart data found (wenzhen_charts.db not available)")
        return self

    def build_classics(self, data_dir: str = "data") -> KnowledgeGraph:
        """Extract entities and citations from competitor/knowledge data."""
        entities, relations = self.classic_builder.build(data_dir=data_dir)
        if entities:
            self.store.add_entities(entities)
            self.store.add_relations(relations)
            print(f"  Added {len(entities)} knowledge entities, {len(relations)} knowledge relations")
        else:
            print("  No knowledge data found")
        return self

    # ------------------------------------------------------------------
    # Query & retrieval
    # ------------------------------------------------------------------

    def retrieve(self, day_master: str, **kwargs: Any) -> str:
        """Build a structured context string for LLM prompt injection."""
        return self.retriever.build_llm_context(day_master, **kwargs)

    def find_similar(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Find similar charts by entity overlap."""
        return self.retriever.find_similar_charts(**kwargs)

    def search(self, query: str) -> dict[str, Any]:
        """Global keyword search with subgraph extraction."""
        return self.retriever.global_search(query)

    # ------------------------------------------------------------------
    # Community detection
    # ------------------------------------------------------------------

    def detect_communities(self) -> list[dict[str, Any]]:
        """Run community detection on the graph."""
        return self.community.detect()

    def summarize_communities(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Generate LLM summaries for each community."""
        return self.community.generate_summaries(api_key=api_key)

    # ------------------------------------------------------------------
    # Stats & debug
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return self.store.stats()

    def clear(self) -> None:
        self.store.clear()

    def close(self) -> None:
        self.store.close()

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"KnowledgeGraph(db={self.db_path})\n"
            f"  Entities: {s['entities']}\n"
            f"  Relations: {s['relations']}\n"
            f"  Entity types: {dict(s['entity_types'][:10])}\n"
            f"  Relation types: {dict(s['relation_types'][:10])}"
        )
