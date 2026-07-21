#!/usr/bin/env python3
"""Ingest scraped dream data from 12880.com and zgjmorg.com into RAG."""
import sys, os, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

INPUT_DIR = Path('/mnt/d/fortune-data/books/zonghe')
VECTORDB = '/mnt/d/fortune-data/vectordb'

FILES = {
    '12880': INPUT_DIR / '12880_dreams.txt',
    'zgjmorg': INPUT_DIR / 'zgjmorg_dreams.txt',
}

def main():
    embedder = Embedder()
    retriever = Retriever(VECTORDB, embedder)
    start_count = retriever.count()
    print(f"RAG before: {start_count} docs\n")

    total_new = 0

    for site, path in FILES.items():
        if not path.exists():
            print(f"[{site}] SKIP: file not found at {path}")
            continue

        text = path.read_text(encoding='utf-8')
        lines = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('#')]

        # Each line: "梦见XXX: YYY"
        entries = []
        for line in lines:
            match = re.match(r'梦见(.+?)[:：]\s*(.+)', line)
            if match:
                keyword = match.group(1).strip()
                interpretation = match.group(2).strip()
                entries.append(f"梦见{keyword}：{interpretation}")

        if not entries:
            print(f"[{site}] WARNING: No dream entries found in file")
            continue

        print(f"[{site}] {len(entries)} dream entries found")

        # Batch process: 100 entries per batch
        BATCH = 100
        for i in range(0, len(entries), BATCH):
            batch = entries[i:i+BATCH]
            batch_text = '\n'.join(batch)
            chunks = chunk_text(batch_text, source=f"{site}_dreams", author='周公解梦', category='dream', chunk_size=800)

            if chunks:
                retriever.add_chunks(chunks)
                total_new += len(chunks)
                print(f"  Batch {i//BATCH + 1}: {len(chunks)} chunks")

    end_count = retriever.count()
    print(f"\nRAG after: {end_count} docs (+{end_count - start_count})")
    print(f"Chunks added: {total_new}")

if __name__ == '__main__':
    main()
