"""Community detection and LLM summary generation for the fortune-telling KG.

Uses Leiden clustering to partition the graph into communities,
then generates human-readable summaries for each community using
an LLM (DeepSeek flash by default, for cost efficiency).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.kg.store import GraphStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency: networkx + leidenalg / python-louvain
# ---------------------------------------------------------------------------

try:
    import networkx as nx

    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

try:
    from networkx.algorithms.community import louvain_communities

    HAS_LOUVAIN = True
except ImportError:
    HAS_LOUVAIN = False

try:
    import igraph as ig

    HAS_IGRAPH = True
except ImportError:
    HAS_IGRAPH = False

# ---------------------------------------------------------------------------
# CommunityDetector
# ---------------------------------------------------------------------------


class CommunityDetector:
    """Detect communities in the KG and generate LLM summaries.

    Strategy (in order of preference):
      1. Leiden via igraph (fastest, best quality)
      2. Louvain via networkx (good quality, no extra dep)
      3. Connected-components fallback (always works)
    """

    def __init__(self, store: GraphStore, min_community_size: int = 3):
        self.store = store
        self.min_community_size = min_community_size
        self.communities: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self) -> list[dict[str, Any]]:
        """Run community detection on the full graph.

        Returns:
            List of community dicts: {id, name, entities, relations, entity_count}.
        """
        # Load graph from store
        entities, relations = self._load_full_graph()

        if not entities:
            logger.warning("No entities in graph, skipping community detection")
            return []

        if HAS_IGRAPH:
            communities = self._detect_leiden_igraph(entities, relations)
        elif HAS_NETWORKX and HAS_LOUVAIN:
            communities = self._detect_louvain_networkx(entities, relations)
        else:
            communities = self._detect_connected_components(entities, relations)

        # Build community summaries
        self.communities = self._build_communities(entities, relations, communities)
        return self.communities

    def _load_full_graph(
        self,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Load all entities and relations from store."""
        conn = self.store.conn

        entities: dict[str, dict[str, Any]] = {}
        for row in conn.execute("SELECT id, type, name, properties FROM entities"):
            entities[row[0]] = {
                "id": row[0],
                "type": row[1],
                "name": row[2],
                "properties": json.loads(row[3]) if row[3] else {},
            }

        relations: list[dict[str, Any]] = []
        for row in conn.execute("SELECT from_id, to_id, type FROM relations"):
            if row[0] in entities and row[1] in entities:
                relations.append({
                    "from_id": row[0],
                    "to_id": row[1],
                    "type": row[2],
                })

        return entities, relations

    # ------------------------------------------------------------------
    # Leiden via igraph
    # ------------------------------------------------------------------

    def _detect_leiden_igraph(
        self,
        entities: dict[str, dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> list[set[str]]:
        """Community detection using igraph's Leiden algorithm."""
        if not HAS_IGRAPH:
            return self._detect_connected_components(entities, relations)

        node_list = list(entities.keys())
        node_index = {nid: i for i, nid in enumerate(node_list)}

        g = ig.Graph(len(node_list), directed=False)
        edges = []
        for r in relations:
            src = node_index.get(r["from_id"])
            dst = node_index.get(r["to_id"])
            if src is not None and dst is not None:
                edges.append((src, dst))

        if edges:
            g.add_edges(edges)

        # Simplify: remove multi-edges and loops
        g.simplify(multiple=True, loops=True)

        try:
            vc = g.community_leiden(
                objective="modularity",
                weights=None,
                n_iterations=-1,
            )
            clusters: dict[int, set[str]] = {}
            for vid, membership in enumerate(vc.membership):
                clusters.setdefault(membership, set()).add(node_list[vid])
            return list(clusters.values())
        except Exception as exc:
            logger.warning("Leiden detection failed (%s), falling back to connected components", exc)
            return self._detect_connected_components(entities, relations)

    # ------------------------------------------------------------------
    # Louvain via networkx
    # ------------------------------------------------------------------

    def _detect_louvain_networkx(
        self,
        entities: dict[str, dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> list[set[str]]:
        """Community detection using networkx Louvain."""
        if not (HAS_NETWORKX and HAS_LOUVAIN):
            return self._detect_connected_components(entities, relations)

        G = nx.Graph()
        G.add_nodes_from(entities.keys())
        for r in relations:
            G.add_edge(r["from_id"], r["to_id"])

        try:
            communities = louvain_communities(G, seed=42)
            return [set(c) for c in communities]
        except Exception as exc:
            logger.warning("Louvain detection failed (%s), falling back to connected components", exc)
            return self._detect_connected_components(entities, relations)

    # ------------------------------------------------------------------
    # Connected-components fallback
    # ------------------------------------------------------------------

    def _detect_connected_components(
        self,
        entities: dict[str, dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> list[set[str]]:
        """Simple connected components as fallback community detection."""
        # Build adjacency
        adj: dict[str, set[str]] = {nid: set() for nid in entities}
        for r in relations:
            adj.setdefault(r["from_id"], set()).add(r["to_id"])
            adj.setdefault(r["to_id"], set()).add(r["from_id"])

        visited: set[str] = set()
        components: list[set[str]] = []

        for nid in entities:
            if nid in visited:
                continue
            component: set[str] = set()
            stack = [nid]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                for neighbor in adj.get(current, set()):
                    if neighbor not in visited:
                        stack.append(neighbor)
            components.append(component)

        return components

    # ------------------------------------------------------------------
    # Community building
    # ------------------------------------------------------------------

    def _build_communities(
        self,
        entities: dict[str, dict[str, Any]],
        relations: list[dict[str, Any]],
        raw_communities: list[set[str]],
    ) -> list[dict[str, Any]]:
        """Build community dicts from raw cluster node sets."""
        communities: list[dict[str, Any]] = []

        for idx, node_ids in enumerate(raw_communities):
            if len(node_ids) < self.min_community_size:
                continue

            # Get entity details
            comm_entities = [entities[nid] for nid in node_ids if nid in entities]

            # Get internal relations
            comm_relations = [
                r for r in relations
                if r["from_id"] in node_ids and r["to_id"] in node_ids
            ]

            # Determine dominant entity types
            type_counts: dict[str, int] = {}
            for e in comm_entities:
                t = e["type"]
                type_counts[t] = type_counts.get(t, 0) + 1
            dominant_types = sorted(type_counts, key=type_counts.get, reverse=True)[:3]

            # Community name derived from dominant types
            if dominant_types:
                community_name = "/".join(dominant_types)
            else:
                community_name = f"community_{idx}"

            community = {
                "id": idx,
                "name": community_name,
                "entity_count": len(comm_entities),
                "relation_count": len(comm_relations),
                "dominant_types": dominant_types,
                "type_distribution": type_counts,
                "entity_ids": list(node_ids),
                "entity_details": [
                    f"[{e['type']}] {e['name']}" for e in comm_entities[:50]
                ],
                "relation_details": [
                    f"({entities.get(r['from_id'], {}).get('name', r['from_id'])})"
                    f"--[{r['type']}]-->"
                    f"({entities.get(r['to_id'], {}).get('name', r['to_id'])})"
                    for r in comm_relations[:50]
                ],
                "summary": "",  # To be filled by LLM
            }
            communities.append(community)

        # Sort by size (largest first)
        communities.sort(key=lambda c: -c["entity_count"])
        return communities

    # ------------------------------------------------------------------
    # LLM summary generation
    # ------------------------------------------------------------------

    def generate_summaries(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        max_concurrent: int = 5,
    ) -> list[dict[str, Any]]:
        """Generate summaries for each community using an LLM.

        Uses DeepSeek flash for cost efficiency. Falls back to template
        summaries if no API key is provided.

        Args:
            api_key: DeepSeek API key. If None, uses template summaries.
            model: Model name.
            base_url: API base URL.
            max_concurrent: Max concurrent API calls.

        Returns:
            Updated communities list with summaries.
        """
        if not self.communities:
            logger.warning("No communities to summarize. Run detect() first.")
            return self.communities

        if not api_key:
            self._generate_template_summaries()
            return self.communities

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
        except ImportError:
            logger.warning("openai package not installed, using template summaries")
            self._generate_template_summaries()
            return self.communities

        import concurrent.futures

        def _summarize(community: dict[str, Any]) -> str:
            try:
                # Build a concise description of the community
                type_dist = community["type_distribution"]
                entities_sample = community["entity_details"][:15]
                relations_sample = community["relation_details"][:10]

                prompt = f"""你是一位命理学家和知识图谱分析师。请用中文为以下命理知识图谱社区生成简洁的摘要（100-150字）。

社区包含 {community['entity_count']} 个实体和 {community['relation_count']} 条关系。
实体类型分布: {json.dumps(type_dist, ensure_ascii=False)}

代表实体:
{chr(10).join(entities_sample[:10])}

代表关系:
{chr(10).join(relations_sample[:5])}

请总结这个社区的知识主题、核心概念和实际用途。"""

                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    temperature=0.3,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                logger.warning("LLM summary failed for community %s: %s", community["id"], exc)
                return self._template_summary(community)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(_summarize, comm): i
                for i, comm in enumerate(self.communities)
            }
            # 防卡死：整体等待设超时（120s），超时后跳过未完成项
            try:
                completed = concurrent.futures.as_completed(futures, timeout=120)
                for future in completed:
                    idx = futures[future]
                    try:
                        self.communities[idx]["summary"] = future.result(timeout=30)
                    except Exception as exc:
                        logger.warning("Summary task failed for community %s: %s", idx, exc)
                        self.communities[idx]["summary"] = self._template_summary(self.communities[idx])
            except concurrent.futures.TimeoutError:
                logger.warning("Community summarization timed out (>120s), %d tasks incomplete",
                               len(futures))

        return self.communities

    # ------------------------------------------------------------------
    # Template summaries (fallback)
    # ------------------------------------------------------------------

    def _generate_template_summaries(self) -> None:
        """Fill summaries with template text when no LLM is available."""
        for comm in self.communities:
            comm["summary"] = self._template_summary(comm)

    def _template_summary(self, community: dict[str, Any]) -> str:
        types = ", ".join(community["dominant_types"])
        return (
            f"此社区包含 {community['entity_count']} 个实体，主要类型为{types}。"
            f"共有 {community['relation_count']} 条关系连接这些实体。"
            f"该社区涵盖了命理知识中的{types}相关领域，"
            f"可用于八字排盘分析和命理推断。"
        )
