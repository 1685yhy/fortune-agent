"""Graph retriever for fortune-telling knowledge graph.

Provides three retrieval modes:
  - Local search: traverse from user's bazi entities to related theories
  - Global search: match community summaries against queries
  - Chart similarity: find top-K similar 命例 by entity overlap
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from src.kg.schema import Entity, EntityType, Relation, RelationType
from src.kg.store import GraphStore


class GraphRetriever:
    """Retrieve knowledge from the KG using graph traversal and similarity."""

    def __init__(self, store: GraphStore):
        self.store = store

    # ------------------------------------------------------------------
    # Local search: traverse from user's bazi
    # ------------------------------------------------------------------

    def local_search(
        self,
        day_master: str,
        year_pillar: str = "",
        month_pillar: str = "",
        hour_pillar: str = "",
        max_depth: int = 2,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Traverse from user's bazi pillars to find related knowledge.

        Args:
            day_master: 日主 (e.g. "甲")
            year_pillar: 年柱 (e.g. "甲子")
            month_pillar: 月柱
            hour_pillar: 时柱
            max_depth: How many hops to traverse.
            max_results: Max results to return.

        Returns:
            List of {entity, relation, neighbor, path_depth} dicts.
        """
        seed_ids: list[str] = []

        # Day master
        dm_id = f"tg_{day_master}"
        seed_ids.append(dm_id)

        # Pillar heavenly stems and earthly branches
        for pillar in [year_pillar, month_pillar, hour_pillar]:
            if len(pillar) >= 2:
                seed_ids.append(f"tg_{pillar[0]}")
                seed_ids.append(f"dz_{pillar[1]}")

        # Deduplicate
        seed_ids = list(set(seed_ids))

        results: list[dict[str, Any]] = []
        seen_entity_ids: set[str] = set()

        for seed_id in seed_ids:
            if seed_id in seen_entity_ids:
                continue
            seed_entity = self.store.get_entity(seed_id)
            if seed_entity is None:
                continue

            neighbors = self.store.get_neighbors(seed_id, max_results=max_results)
            for rel, neighbor in neighbors:
                if neighbor.id in seen_entity_ids:
                    continue
                seen_entity_ids.add(neighbor.id)
                results.append({
                    "seed": seed_entity.name,
                    "seed_id": seed_id,
                    "relation_type": str(rel.type),
                    "relation_properties": rel.properties,
                    "neighbor_id": neighbor.id,
                    "neighbor_name": neighbor.name,
                    "neighbor_type": str(neighbor.type),
                    "neighbor_properties": neighbor.properties,
                    "depth": 1,
                })

        # Second hop: traverse deeper into knowledge types
        if max_depth >= 2:
            second_hop_ids = [r["neighbor_id"] for r in results]
            for nid in second_hop_ids:
                if nid in seen_entity_ids:
                    continue
                neighbors = self.store.get_neighbors(nid, max_results=max_results // 2)
                for rel, neighbor in neighbors:
                    if neighbor.id in seen_entity_ids:
                        continue
                    seen_entity_ids.add(neighbor.id)
                    results.append({
                        "seed": "depth2",
                        "seed_id": nid,
                        "relation_type": str(rel.type),
                        "relation_properties": rel.properties,
                        "neighbor_id": neighbor.id,
                        "neighbor_name": neighbor.name,
                        "neighbor_type": str(neighbor.type),
                        "neighbor_properties": neighbor.properties,
                        "depth": 2,
                    })

        # Sort: depth-1 results first
        results.sort(key=lambda x: x["depth"])
        return results[:max_results]

    # ------------------------------------------------------------------
    # Chart similarity: entity overlap
    # ------------------------------------------------------------------

    def find_similar_charts(
        self,
        day_master: str,
        year_pillar: str = "",
        month_pillar: str = "",
        hour_pillar: str = "",
        gender: str = "",
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Find top-K similar charts by entity overlap (Jaccard similarity).

        Compares the user's bazi entities against stored 命例 entities.
        """
        # Build query entity set
        query_ids: set[str] = set()
        query_ids.add(f"tg_{day_master}")
        for pillar in [year_pillar, month_pillar, hour_pillar]:
            if len(pillar) >= 2:
                query_ids.add(f"tg_{pillar[0]}")
                query_ids.add(f"dz_{pillar[1]}")

        # Get all chart entities
        chart_entities = self.store.get_by_type(EntityType.MING_LI)
        if not chart_entities:
            return []

        # Count entity overlap with each chart
        scored: list[tuple[float, Entity]] = []
        for chart in chart_entities:
            neighbors = self.store.get_neighbors(chart.id, max_results=200)
            chart_ids = {e.id for _, e in neighbors}

            intersection = query_ids & chart_ids
            union = query_ids | chart_ids
            if union:
                similarity = len(intersection) / len(union)
                if similarity > 0:
                    scored.append((similarity, chart))

        scored.sort(key=lambda x: -x[0])
        results = []
        for sim, chart in scored[:top_k]:
            results.append({
                "chart_id": chart.id,
                "chart_name": chart.name,
                "similarity": round(sim, 4),
                "properties": chart.properties,
            })
        return results

    # ------------------------------------------------------------------
    # Global search: keyword-based entity lookup with subgraph
    # ------------------------------------------------------------------

    def global_search(
        self,
        query: str,
        max_entities: int = 30,
    ) -> dict[str, Any]:
        """Search the KG by keyword and return a subgraph of results.

        This supports GraphRAG by finding entities matching the query,
        then extracting the surrounding subgraph for LLM context.
        """
        matched = self.store.search_entities(query, limit=max_entities)
        seed_ids = [e.id for e in matched]

        entities, relations = self.store.get_subgraph(seed_ids, max_depth=2, max_nodes=max_entities * 3)

        # Format for LLM consumption
        entity_lines = []
        for e in entities:
            props_str = json.dumps(e.properties, ensure_ascii=False)[:200]
            entity_lines.append(f"[{e.type}] {e.name} | {props_str}")

        rel_lines = []
        for r in relations:
            from_e = next((e for e in entities if e.id == r.from_id), None)
            to_e = next((e for e in entities if e.id == r.to_id), None)
            if from_e and to_e:
                rel_lines.append(f"({from_e.name})--[{r.type}]-->({to_e.name})")

        return {
            "query": query,
            "matched_entities": len(matched),
            "subgraph_entities": len(entities),
            "subgraph_relations": len(relations),
            "entities": [
                {"id": e.id, "type": str(e.type), "name": e.name}
                for e in matched[:20]
            ],
            "entity_details": entity_lines[:50],
            "relation_details": rel_lines[:50],
        }

    # ------------------------------------------------------------------
    # Scholar search: find 古籍 citations for a concept
    # ------------------------------------------------------------------

    def scholar_search(
        self,
        concept: str,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Find classic text citations related to a concept.

        Searches entity names and properties for the concept,
        then finds related 经典 entities.
        """
        entities = self.store.search_entities(concept, limit=50)
        results = []

        for ent in entities[:max_results]:
            # Get neighboring 经典 entities
            neighbors = self.store.get_neighbors(
                ent.id, relation_type=RelationType.GU_JI_CHU_CHU, max_results=10
            )
            for rel, classic_ent in neighbors:
                results.append({
                    "concept": ent.name,
                    "concept_type": str(ent.type),
                    "classic": classic_ent.name,
                    "classic_id": classic_ent.id,
                    "relation_properties": rel.properties,
                })

        return results

    # ------------------------------------------------------------------
    # Context builder for LLM prompts
    # ------------------------------------------------------------------

    def build_llm_context(
        self,
        day_master: str,
        year_pillar: str = "",
        month_pillar: str = "",
        hour_pillar: str = "",
        query: str = "",
    ) -> str:
        """Build a structured context string for LLM prompt injection.

        Combines local search results, similar charts, and global search
        into a single text block for RAG.
        """
        parts: list[str] = []

        # Local graph context
        local = self.local_search(day_master, year_pillar, month_pillar, hour_pillar)
        if local:
            lines = ["## 命理知识图谱关联"]
            for item in local[:20]:
                lines.append(
                    f"- {item['seed']} --[{item['relation_type']}]--> "
                    f"{item['neighbor_name']} ({item['neighbor_type']})"
                )
            parts.append("\n".join(lines))

        # Similar charts
        similar = self.find_similar_charts(day_master, year_pillar, month_pillar, hour_pillar)
        if similar:
            lines = ["\n## 相似命例"]
            for s in similar[:5]:
                props = json.dumps(
                    {k: v for k, v in s["properties"].items() if k != "full_text"},
                    ensure_ascii=False,
                )
                lines.append(f"- {s['chart_name']} (相似度: {s['similarity']:.2%}) {props}")
            parts.append("\n".join(lines))

        # Global search (if query provided)
        if query:
            global_result = self.global_search(query, max_entities=20)
            if global_result.get("relation_details"):
                parts.append("\n## 全局知识匹配\n" + "\n".join(global_result["relation_details"][:20]))

        return "\n\n".join(parts)
