"""SQLite-based graph store for the fortune-telling knowledge graph.

Lightweight, no external database required. Stores entities and relations
in a single SQLite file with indexes for fast graph traversal.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from src.kg.schema import Entity, EntityType, Relation, RelationType


def _short_uuid() -> str:
    return uuid.uuid4().hex[:12]


class GraphStore:
    """SQLite-backed graph store for entities and relations."""

    def __init__(self, db_path: str = "kg_data/kg.db"):
        self.db_path = str(Path(db_path).resolve())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                properties TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                type TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                FOREIGN KEY (from_id) REFERENCES entities(id),
                FOREIGN KEY (to_id) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
            CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id);
            CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_id);
            CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(type);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def add_entity(self, entity: Entity) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO entities (id, type, name, properties) VALUES (?, ?, ?, ?)",
            (entity.id, entity.type.value, entity.name, json.dumps(entity.properties, ensure_ascii=False)),
        )
        self.conn.commit()
        return entity.id

    def add_entities(self, entities: list[Entity]) -> int:
        rows = [
            (e.id, e.type.value, e.name, json.dumps(e.properties, ensure_ascii=False))
            for e in entities
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO entities (id, type, name, properties) VALUES (?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, type, name, properties FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            return None
        return Entity(
            id=row[0],
            type=EntityType(row[1]),
            name=row[2],
            properties=json.loads(row[3]),
        )

    def get_entity_by_name(self, name: str, type_filter: EntityType | None = None) -> list[Entity]:
        if type_filter:
            rows = self.conn.execute(
                "SELECT id, type, name, properties FROM entities WHERE name = ? AND type = ?",
                (name, type_filter.value),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, type, name, properties FROM entities WHERE name = ?", (name,)
            ).fetchall()
        return [
            Entity(id=r[0], type=EntityType(r[1]), name=r[2], properties=json.loads(r[3]))
            for r in rows
        ]

    def get_by_type(self, entity_type: EntityType) -> list[Entity]:
        rows = self.conn.execute(
            "SELECT id, type, name, properties FROM entities WHERE type = ?",
            (entity_type.value,),
        ).fetchall()
        return [
            Entity(id=r[0], type=EntityType(r[1]), name=r[2], properties=json.loads(r[3]))
            for r in rows
        ]

    def search_entities(self, query: str, limit: int = 50) -> list[Entity]:
        rows = self.conn.execute(
            "SELECT id, type, name, properties FROM entities WHERE name LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [
            Entity(id=r[0], type=EntityType(r[1]), name=r[2], properties=json.loads(r[3]))
            for r in rows
        ]

    def entity_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    # ------------------------------------------------------------------
    # Relation CRUD
    # ------------------------------------------------------------------

    def add_relation(self, relation: Relation) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO relations (id, from_id, to_id, type, properties) VALUES (?, ?, ?, ?, ?)",
            (relation.id, relation.from_id, relation.to_id, relation.type.value, json.dumps(relation.properties, ensure_ascii=False)),
        )
        self.conn.commit()
        return relation.id

    def add_relations(self, relations: list[Relation]) -> int:
        rows = [
            (r.id, r.from_id, r.to_id, r.type.value, json.dumps(r.properties, ensure_ascii=False))
            for r in relations
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO relations (id, from_id, to_id, type, properties) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def get_neighbors(
        self,
        entity_id: str,
        relation_type: RelationType | None = None,
        direction: str = "both",
        max_results: int = 500,
    ) -> list[tuple[Relation, Entity]]:
        """Get neighboring entities connected by relations.

        Args:
            entity_id: The entity to find neighbors for.
            relation_type: Optional filter by relation type.
            direction: "outgoing", "incoming", or "both".
            max_results: Maximum results to return.

        Returns:
            List of (Relation, Entity) tuples.
        """
        results: list[tuple[Relation, Entity]] = []
        seen_ids: set[str] = set()

        if direction in ("outgoing", "both"):
            sql = """
                SELECT r.id, r.from_id, r.to_id, r.type, r.properties,
                       e.id, e.type, e.name, e.properties
                FROM relations r
                JOIN entities e ON r.to_id = e.id
                WHERE r.from_id = ?
            """
            params: list[Any] = [entity_id]
            if relation_type:
                sql += " AND r.type = ?"
                params.append(relation_type.value)
            sql += " LIMIT ?"
            params.append(max_results)
            for row in self.conn.execute(sql, params).fetchall():
                rel = Relation(id=row[0], from_id=row[1], to_id=row[2], type=RelationType(row[3]), properties=json.loads(row[4]))
                ent = Entity(id=row[5], type=EntityType(row[6]), name=row[7], properties=json.loads(row[8]))
                if ent.id not in seen_ids:
                    results.append((rel, ent))
                    seen_ids.add(ent.id)

        if direction in ("incoming", "both"):
            sql = """
                SELECT r.id, r.from_id, r.to_id, r.type, r.properties,
                       e.id, e.type, e.name, e.properties
                FROM relations r
                JOIN entities e ON r.from_id = e.id
                WHERE r.to_id = ?
            """
            params = [entity_id]
            if relation_type:
                sql += " AND r.type = ?"
                params.append(relation_type.value)
            sql += " LIMIT ?"
            params.append(max_results)
            for row in self.conn.execute(sql, params).fetchall():
                rel = Relation(id=row[0], from_id=row[1], to_id=row[2], type=RelationType(row[3]), properties=json.loads(row[4]))
                ent = Entity(id=row[5], type=EntityType(row[6]), name=row[7], properties=json.loads(row[8]))
                if ent.id not in seen_ids:
                    results.append((rel, ent))
                    seen_ids.add(ent.id)

        return results

    def find_path(
        self, from_id: str, to_id: str, max_depth: int = 4
    ) -> list[list[tuple[str, str, str]]]:
        """BFS shortest-path between two entities.

        Returns:
            List of paths, where each path is [(from_id, relation_type, to_id), ...].
            Empty list if no path found.
        """
        if from_id == to_id:
            return [[(from_id, "self", to_id)]]

        visited: set[str] = set()
        # queue entries: (current_id, path_so_far)
        queue: list[tuple[str, list[tuple[str, str, str]]]] = [(from_id, [])]
        visited.add(from_id)
        found_paths: list[list[tuple[str, str, str]]] = []

        while queue and len(found_paths) < 5:
            current, path = queue.pop(0)
            if len(path) >= max_depth:
                continue

            # outgoing
            rows = self.conn.execute(
                "SELECT r.type, r.to_id FROM relations r WHERE r.from_id = ?", (current,)
            ).fetchall()
            for rtype, nid in rows:
                if nid == to_id:
                    found_paths.append(path + [(current, rtype, nid)])
                elif nid not in visited:
                    visited.add(nid)
                    queue.append((nid, path + [(current, rtype, nid)]))

            # incoming
            rows = self.conn.execute(
                "SELECT r.type, r.from_id FROM relations r WHERE r.to_id = ?", (current,)
            ).fetchall()
            for rtype, nid in rows:
                if nid == to_id:
                    found_paths.append(path + [(current, rtype, nid)])
                elif nid not in visited:
                    visited.add(nid)
                    queue.append((nid, path + [(current, rtype, nid)]))

        return found_paths

    def get_subgraph(
        self, seed_ids: list[str], max_depth: int = 2, max_nodes: int = 200
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract a subgraph around seed entities within N hops.

        Returns:
            (entities, relations) in the subgraph.
        """
        entities_map: dict[str, Entity] = {}
        relations_list: list[Relation] = []
        frontier = set(seed_ids)
        visited: set[str] = set()
        depth = 0

        while frontier and depth < max_depth and len(entities_map) < max_nodes:
            new_frontier: set[str] = set()
            batch = list(frontier - visited)
            visited.update(batch)

            # Fetch entities in batch
            placeholders = ",".join("?" for _ in batch)
            rows = self.conn.execute(
                f"SELECT id, type, name, properties FROM entities WHERE id IN ({placeholders})",
                batch,
            ).fetchall()
            for r in rows:
                if r[0] not in entities_map:
                    entities_map[r[0]] = Entity(id=r[0], type=EntityType(r[1]), name=r[2], properties=json.loads(r[3]))

            if len(entities_map) >= max_nodes:
                break

            # Fetch relations
            rows = self.conn.execute(
                f"""SELECT id, from_id, to_id, type, properties FROM relations
                    WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})
                    LIMIT ?""",
                batch + batch + [max_nodes * 5],
            ).fetchall()
            for r in rows:
                rel = Relation(id=r[0], from_id=r[1], to_id=r[2], type=RelationType(r[3]), properties=json.loads(r[4]))
                relations_list.append(rel)
                for nid in (r[1], r[2]):
                    if nid not in visited and nid not in entities_map:
                        new_frontier.add(nid)

            frontier = new_frontier
            depth += 1

        return list(entities_map.values()), relations_list

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self.conn.executescript("DELETE FROM relations; DELETE FROM entities;")
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        return {
            "entities": self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "relations": self.conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
            "entity_types": self.conn.execute(
                "SELECT type, COUNT(*) FROM entities GROUP BY type ORDER BY COUNT(*) DESC"
            ).fetchall(),
            "relation_types": self.conn.execute(
                "SELECT type, COUNT(*) FROM relations GROUP BY type ORDER BY COUNT(*) DESC"
            ).fetchall(),
        }

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
