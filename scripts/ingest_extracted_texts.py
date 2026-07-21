"""Read pre-extracted classical Chinese texts from /tmp/extracted_texts/ and add to RAG.

Uses the output of the extraction agents: all .txt files under /tmp/extracted_texts/<repo>/
"""
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

EXTRACTED_DIR = Path("/tmp/extracted_texts")
CHUNK_SIZE = 800
CHUNK_OVERLAP = 80

# Repo-level category assignments based on agent findings
REPO_CATEGORIES = {
    "suangua": "zonghe",  # Has everything: yijing, bazi, qimen, fengshui, ziwei
    "szbazi": "bazi",
    "zhiming-ai": "zonghe",
    "bazi-yijing": "bazi",
}

# Keyword->category overrides based on content
CATEGORY_KEYWORDS = {
    "bazi": ["八字", "四柱", "日主", "用神", "大运", "流年", "偏财", "正官", "七杀", "比肩", "劫财",
             "食神", "伤官", "正印", "偏印", "格局", "滴天髓", "三命通会", "穷通宝鉴", "子平真诠",
             "渊海子平", "十神", "神煞", "天干", "地支", "长生", "沐浴", "冠带", "临官", "帝旺",
             "衰", "病", "死", "墓", "绝", "胎", "养", "排盘", "paipan"],
    "yijing": ["易经", "周易", "卦象", "八卦", "乾坤", "坎离", "艮兑", "巽震", "爻", "六十四卦",
               "说卦", "序卦", "系辞", "象曰", "彖曰"],
    "liuyao": ["六爻", "纳甲", "京房", "火珠林", "黄金策", "增删卜易", "卜筮正宗", "易隐",
               "世应", "六亲", "六兽", "青龙", "朱雀", "勾陈", "螣蛇", "白虎", "玄武"],
    "qimen": ["奇门", "八门", "九星", "遁甲", "六壬", "太乙", "三传", "四课", "烟波钓叟"],
    "ziwei": ["紫微", "天机", "太阳", "武曲", "天同", "廉贞", "命宫", "身宫", "紫微斗数",
              "十四主星", "南斗", "北斗"],
    "fengshui": ["风水", "龙脉", "砂水", "理气", "峦头", "山向", "罗盘", "阳宅", "阴宅",
                 "八宅", "玄空", "葬经", "堪舆"],
    "mianxiang": ["面相", "气色", "手相", "人相", "五官"],
    "zeri": ["择日", "协纪辨方", "黄历", "通书"],
}


def guess_category(text: str, filename: str, repo_name: str) -> str:
    """Guess the category from filename and content."""
    fn = filename.lower()

    # Check filename patterns first
    if any(k in fn for k in ["bazi", "ditian", "sanming", "ziping", "qiongtong", "yuanhai",
                              "shensha", "shishen", "day_master", "ganzhi", "wuxing"]):
        return "bazi"
    if any(k in fn for k in ["yijing", "yijing", "hexagram", "卦", "bagua"]):
        if any(k in fn for k in ["liuyao", "najia"]):
            return "liuyao"
        return "yijing"
    if any(k in fn for k in ["qimen", "liuren", "liu_ren"]):
        return "qimen"
    if any(k in fn for k in ["ziwei", "zi_wei", "dou_shu"]):
        return "ziwei"
    if any(k in fn for k in ["fengshui", "feng_shui", "kanyu"]):
        return "fengshui"
    if any(k in fn for k in ["mianxiang", "renxiang"]):
        return "mianxiang"
    if any(k in fn for k in ["zeri", "xuanze", "huangli"]):
        return "zeri"

    # Score based on content
    scores = {cat: 0 for cat in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text)

    best = max(scores, key=scores.get)
    if scores[best] >= 3:
        return best

    # Fall back to repo-level default
    return REPO_CATEGORIES.get(repo_name, "zonghe")


def find_all_text_files() -> List[Tuple[Path, str, str]]:
    """Find all .txt files in extracted dirs. Returns (filepath, repo_name, relative_path)."""
    results = []
    for repo_dir in EXTRACTED_DIR.iterdir():
        if not repo_dir.is_dir():
            continue
        repo_name = repo_dir.name

        # Find all .txt files recursively, but exclude combined files
        for fp in repo_dir.rglob("*"):
            if not fp.is_file():
                continue
            # Skip the combined file
            if fp.name.endswith("_combined.txt"):
                continue
            # Include: .txt, .md, .csv, .yaml files
            if fp.suffix.lower() in ('.txt', '.md', '.csv', '.yaml', '.yml'):
                # But skip if it starts with '.' or is __init__
                if fp.name.startswith('.'):
                    continue
                rel = fp.relative_to(repo_dir)
                results.append((fp, repo_name, str(rel)))

    return results


def main():
    """Main: read extracted texts, chunk, embed, and store."""
    logger.info("=" * 60)
    logger.info("FINDING EXTRACTED TEXT FILES")
    logger.info("=" * 60)

    files = find_all_text_files()
    logger.info(f"Found {len(files)} text files in {EXTRACTED_DIR}")

    # Group by repo
    by_repo = Counter(r for _, r, _ in files)
    for repo, count in by_repo.most_common():
        logger.info(f"  {repo}: {count} files")

    # Read all files and extract Chinese text
    text_sources = []  # (source_name, category, text)
    total_chars = 0
    skipped_short = 0

    for fp, repo_name, rel_path in files:
        try:
            content = fp.read_text(encoding="utf-8", errors="replace").strip()
        except Exception as e:
            logger.warning(f"  Cannot read {fp}: {e}")
            continue

        if not content:
            continue

        source_name = f"{repo_name}:{rel_path}"

        # Extract Chinese text (filter out non-Chinese content)
        # Keep Chinese chars, classical punctuation, newlines
        chinese_only = re.sub(r'[^\n一-鿿　-〿＀-￯。，、；：？！（）【】《》""''「」『』—…··]', '', content)

        # Remove python code artifacts
        chinese_only = re.sub(r'def\s+\w+\s*\([^)]*\):', '', chinese_only)
        chinese_only = re.sub(r'import\s+\w+', '', chinese_only)
        chinese_only = re.sub(r'from\s+[\w.]+\s+import', '', chinese_only)
        chinese_only = re.sub(r'print\([^)]*\)', '', chinese_only)

        # Collapse blank lines
        chinese_only = re.sub(r'\n{3,}', '\n\n', chinese_only)
        chinese_only = chinese_only.strip()

        if len(chinese_only) < 100:
            skipped_short += 1
            continue

        category = guess_category(chinese_only, fp.name, repo_name)
        text_sources.append((source_name, category, chinese_only))
        total_chars += len(chinese_only)

    logger.info(f"\nTotal text sources extracted: {len(text_sources)}")
    logger.info(f"Total Chinese characters: {total_chars:,}")
    logger.info(f"Skipped (too short): {skipped_short}")

    # Category breakdown
    cat_counts = Counter(c for _, c, _ in text_sources)
    logger.info("Category breakdown:")
    for cat, cnt in cat_counts.most_common():
        logger.info(f"  {cat}: {cnt}")

    # Now chunk everything
    logger.info(f"\n{'=' * 60}")
    logger.info("CHUNKING TEXTS")
    logger.info("=" * 60)

    settings = load_settings()
    embedder = Embedder(model_name=settings.embedding_model)
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    # Get existing count and IDs
    existing_count = retriever.count()
    logger.info(f"Current DB collection size: {existing_count}")

    try:
        existing_ids = set(retriever.collection.get()["ids"])
        logger.info(f"Existing IDs in DB: {len(existing_ids)}")
    except Exception as e:
        logger.warning(f"Could not get existing IDs: {e}")
        existing_ids = set()

    # Chunk all texts, deduplicating by ID
    all_chunks = []
    seen_ids = set(existing_ids)

    for source_name, category, text in text_sources:
        chunks = chunk_text(
            text, source=source_name, author="", category=category,
            chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP,
        )
        for c in chunks:
            # Ensure unique chunk ID by including source path hash
            if c.chunk_id in seen_ids:
                # Append a hash of the source to make it unique
                c.chunk_id = f"{c.chunk_id}_{hashlib.md5(source_name.encode()).hexdigest()[:4]}"
            if c.chunk_id not in seen_ids:
                seen_ids.add(c.chunk_id)
                all_chunks.append(c)

    logger.info(f"New unique chunks to add: {len(all_chunks)}")

    if not all_chunks:
        logger.info("Nothing new to add.")
        return

    # Embed and store in batches
    batch_size = 128
    t_start = time.time()
    total_added = 0

    for batch_start in range(0, len(all_chunks), batch_size):
        batch_end = min(batch_start + batch_size, len(all_chunks))
        batch = all_chunks[batch_start:batch_end]

        batch_texts = [c.text for c in batch]
        batch_ids = [c.chunk_id for c in batch]
        batch_metas = [{
            "source": c.source,
            "author": c.author,
            "category": c.category,
        } for c in batch]

        t0 = time.time()
        embeddings = embedder.encode(batch_texts)
        t1 = time.time()

        retriever.collection.upsert(
            embeddings=embeddings.tolist(),
            documents=batch_texts,
            ids=batch_ids,
            metadatas=batch_metas,
        )
        t2 = time.time()

        total_added += len(batch_texts)
        elapsed = time.time() - t_start

        logger.info(
            f"Batch {batch_start}-{batch_end}/{len(all_chunks)}: "
            f"embed {t1-t0:.1f}s, store {t2-t1:.1f}s | "
            f"{total_added}/{len(all_chunks)} chunks | {elapsed:.0f}s elapsed"
        )

    total_elapsed = time.time() - t_start
    final_count = retriever.count()
    new_count = final_count - existing_count

    logger.info(f"\n{'=' * 60}")
    logger.info("INGESTION COMPLETE")
    logger.info(f"{'=' * 60}")
    logger.info(f"Previous DB size:  {existing_count}")
    logger.info(f"New DB size:       {final_count}")
    logger.info(f"New chunks added:  {new_count}")
    logger.info(f"Time elapsed:      {total_elapsed:.1f}s ({total_elapsed/max(new_count,1)*1000:.1f} ms/chunk)")
    logger.info(f"Sources processed: {len(text_sources)}")
    logger.info(f"Total chars:       {total_chars:,}")

    # Print new category distribution
    all_metas = retriever.collection.get()["metadatas"]
    new_cats = Counter(m.get("category", "unknown") for m in all_metas)
    logger.info("Final category distribution:")
    for cat, cnt in new_cats.most_common():
        logger.info(f"  {cat}: {cnt}")

    # Print new source distribution (top 30)
    all_sources = Counter(m.get("source", "unknown") for m in all_metas)
    logger.info("Top sources in DB:")
    for src, cnt in all_sources.most_common(30):
        logger.info(f"  {src}: {cnt}")

    # Save report
    report = {
        "previous_db_size": existing_count,
        "new_db_size": final_count,
        "chunks_added": new_count,
        "sources_processed": len(text_sources),
        "total_chinese_chars": total_chars,
        "elapsed_seconds": round(total_elapsed, 1),
        "category_distribution": dict(cat_counts.most_common()),
        "final_category_distribution": dict(new_cats.most_common()),
        "repos_processed": dict(by_repo.most_common()),
    }
    report_path = Path("/tmp/github_repos_rag_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
