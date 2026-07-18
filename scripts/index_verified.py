#!/usr/bin/env python3
"""Index verified data into ChromaDB — only accepts staging/ verified files."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.rag.embedder import Embedder
from src.config import load_settings


def index_file(filepath: str, collection_name: str, batch_size: int = 50):
    """Index a verified JSONL file into ChromaDB."""
    settings = load_settings()
    embedder = Embedder()

    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(settings.vectordb_dir))
        collection = client.get_or_create_collection(collection_name)
    except Exception as e:
        print(f"ChromaDB error: {e}")
        return 0

    entries = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    indexed = 0
    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        ids = []
        documents = []
        metadatas = []

        for entry in batch:
            content = entry.get("content", entry.get("text", ""))
            title = entry.get("title", "")
            category = entry.get("category", "general")
            source = entry.get("source_url", entry.get("source", ""))
            text = f"{title}\n{content}"

            doc_id = f"verified_{category}_{hash(text) % 10**10}"
            ids.append(doc_id)
            documents.append(text)
            metadatas.append({
                "title": title,
                "category": category,
                "source_url": source,
                "verified": True,
            })

        try:
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            indexed += len(batch)
            print(f"  Indexed {indexed}/{len(entries)}...")
        except Exception as e:
            print(f"  Batch error: {e}")

    print(f"Done: {indexed}/{len(entries)} entries indexed into '{collection_name}'")
    return indexed


if __name__ == "__main__":
    staging = Path("/mnt/d/fortune-data/books/zonghe/staging")
    if not staging.exists():
        print("No staging directory found")
        sys.exit(1)

    total = 0
    for f in sorted(staging.glob("*_accepted.jsonl")):
        label = f.stem.replace("_accepted", "")
        print(f"Indexing {f.name} -> collection 'fortune_books'")
        n = index_file(str(f), "fortune_books")
        total += n
        print(f"  {label}: {n} entries")

    print(f"\nTotal indexed: {total} entries")
