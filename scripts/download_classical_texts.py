"""Download classical Chinese divination texts from GitHub and other public sources.

Downloads full texts from the scripta-sinica repository (xurenfei/scripta-sinica),
which contains 13,000+ classical Chinese texts sourced from 殆知阁 (Daizhige).
Saves them to /mnt/d/fortune-data/books/{category}/ organized by topic.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

logger = logging.getLogger(__name__)

REPO_BASE = (
    "https://raw.githubusercontent.com/xurenfei/scripta-sinica/master"
)

# ── Book mapping: category -> list of (file_name_in_repo, display_name) ──
# File format: "书名-朝代-作者.txt" in the repo's 02术数-146部 folder
BAZI_TEXTS = [
    ("渊海子平-宋-徐子平", "渊海子平"),
    ("三命通会-明-万民英", "三命通会"),
    ("子平真诠评注-清-沈孝瞻", "子平真诠"),
    ("穷通宝鉴-明-余春台", "穷通宝鉴"),
    ("滴天髓阐微-清-任铁樵", "滴天髓阐微"),
    ("神峰通考-明-张楠", "神峰通考"),
    ("命理探源--袁树珊", "命理探源"),
    ("千里命稿--韦千里", "千里命稿"),
    ("珞琭子三命消息赋注-宋-徐子平", "珞琭子三命消息赋注"),
    ("李虚中命书-周-鬼谷子", "李虚中命书"),
    ("三命指迷赋-宋-岳珂", "三命指迷赋"),
    ("兰台妙选-明-西窗老人", "兰台妙选"),
    ("玉照神应真经-晋-郭璞", "玉照神应真经"),
    ("星命总括-辽-耶律纯", "星命总括"),
    ("星学大成-明-万民英", "星学大成"),
    ("月谈赋-明-佚名", "月谈赋"),
    ("徐氏珞琭子赋注-宋-徐子平", "徐氏珞琭子赋注"),
    ("五行精纪-宋-廖中", "五行精纪"),
    ("五行大义-隋-萧吉", "五行大义"),
    ("海底眼-宋-王鼎", "海底眼"),
    ("乾元秘旨-清-舒继英", "乾元秘旨"),
    ("演禽通纂--佚名", "演禽通纂"),
    ("张果星宗-明-佚名", "张果星宗"),
    ("乙巳占-唐-李淳风", "乙巳占"),
    ("开元占经-唐-瞿昙悉达", "开元占经"),
    ("马王堆汉墓帛书五星占--", "马王堆汉墓帛书五星占"),
]

YIJING_TEXTS = [
    ("梅花易数-宋-邵雍", "梅花易数"),
    ("增删卜易--野鹤老人", "增删卜易"),
    ("卜筮正宗-清-王洪绪", "卜筮正宗"),
    ("火珠林-宋-麻衣道者", "火珠林"),
    ("易冒-清-程良玉", "易冒"),
    ("周易尚占-元-李道纯", "周易尚占"),
    ("卜筮全书-明-姚际隆", "卜筮全书"),
    ("断易天机-明-佚名", "断易天机"),
    ("易林补遗-明-张世宝", "易林补遗"),
    ("易隐-清-曹九锡", "易隐"),
    ("焦氏易林-汉-焦延寿", "焦氏易林"),
    ("焦氏易林注-汉-焦延寿", "焦氏易林注"),
    ("黄金策-明-刘基", "黄金策"),
    ("文王金钱课-周-姬昌", "文王金钱课"),
    ("太玄经-汉-杨雄", "太玄经"),
    ("潜虚-宋-司马光", "潜虚"),
    ("皇极经世-宋-邵雍", "皇极经世"),
    ("皇极经世书-宋-邵雍", "皇极经世书"),
    ("皇极经世心易发微-明-杨体仁", "皇极经世心易发微"),
    ("正易心法-宋-麻衣道者", "正易心法"),
    ("秘本诸葛神数-三国蜀-诸葛亮", "秘本诸葛神数"),
    ("灵棋经-汉-东方朔", "灵棋经"),
    ("测字秘牒-清-程省", "测字秘牒"),
    ("周公解梦--佚名", "周公解梦"),
    ("吴园易解-宋-张根", "吴园易解"),
    ("大易断例卜筮元龟目--", "大易断例卜筮元龟目"),
]

FENGSHUI_TEXTS = [
    ("葬书-晋-郭璞", "葬书"),
    ("撼龙经-唐-杨筠松", "撼龙经"),
    ("疑龙经-唐-杨筠松", "疑龙经"),
    ("撼龙经疑龙经葬法倒杖-唐-杨筠松", "撼龙经疑龙经葬法倒杖"),
    ("葬法倒杖-唐-杨筠松", "葬法倒杖"),
    ("青囊奥语-唐-杨筠松", "青囊奥语"),
    ("青囊序-唐-杨筠松", "青囊序"),
    ("天玉经-唐-杨筠松", "天玉经"),
    ("天玉经外篇-唐-杨筠松", "天玉经外篇"),
    ("催官篇-宋-赖文俊", "催官篇"),
    ("地理辨正-清-蒋大鸿", "地理辨正"),
    ("地理辨惑-清-马泰清", "地理辨惑"),
    ("地理古镜歌-明-蒋大鸿", "地理古镜歌"),
    ("地理醒心录-明-天中星垣主人", "地理醒心录"),
    ("阳宅指南-明-蒋大鸿", "阳宅指南"),
    ("宅法举隅-清-锡山", "宅法举隅"),
    ("入地眼全书-宋-静道", "入地眼全书"),
    ("山洋指迷原本-明-周景一", "山洋指迷原本"),
    ("人子须知-明-徐继善", "人子须知"),
    ("灵城精义-南唐-何溥", "灵城精义"),
    ("平砂玉尺经-元-刘秉忠", "平砂玉尺经"),
    ("平砂玉尺辨伪-明-蒋平阶", "平砂玉尺辨伪"),
    ("青乌经-秦-樗里子", "青乌经"),
    ("插泥剑-明-蒋大鸿", "插泥剑"),
    ("天元五歌-明-蒋大鸿", "天元五歌"),
    ("蒋公字字金-清-蒋大鸿", "蒋公字字金"),
    ("红囊经--李三素", "红囊经"),
    ("本地姜地理峦头诀-清-梁凿贵", "本地姜地理峦头诀"),
    ("地理五诀", "地理五诀"),  # may not exist; will be skipped
]

MIANXIANG_TEXTS = [
    ("神相全编-宋-陈抟", "神相全编"),
    ("太清神鉴-五代-王朴", "太清神鉴"),
    ("柳庄相法-明-袁珙", "柳庄相法"),
    ("人伦大统赋-金-张行简", "人伦大统赋"),
    ("月波洞中记-宋-郑樵", "月波洞中记"),
    ("冰鉴-清-曾国藩", "冰鉴"),
    ("公笃相法--陈公笃", "公笃相法"),
    ("神相铁关刀-清-云谷山人", "神相铁关刀"),
    ("许负相法-汉-许负", "许负相法"),
    ("心相篇-宋-陈希夷", "心相篇"),
    ("鬼谷子神奇相法全书--", "鬼谷子神奇相法全书"),
    ("永乐百问-明-袁珙", "永乐百问"),
]

QIMEN_TEXTS = [
    ("遁甲演义-明-程道生", "遁甲演义"),
    ("遁甲符应经-宋-杨维德", "遁甲符应经"),
    ("奇门遁甲统宗-三国蜀-诸葛亮", "奇门遁甲统宗"),
    ("奇门遁甲秘笈大全-明-刘伯温", "奇门遁甲秘笈大全"),
    ("奇门旨归-清-朱浩文", "奇门旨归"),
    ("奇门法窍-清-锡孟樨", "奇门法窍"),
    ("奇门遁甲元灵经--许松如", "奇门遁甲元灵经"),
    ("奇门宝鉴御定-唐-徐道符", "奇门宝鉴御定"),
    ("太乙金镜式经-唐-王希明", "太乙金镜式经"),
    ("太乙秘书-宋-王佐", "太乙秘书"),
    ("六壬大全-明-郭载騋", "六壬大全"),
    ("六壬指南-明-陈公献", "六壬指南"),
    ("六壬指南注解-明-陈公献", "六壬指南注解"),
    ("六壬断案-宋-邵彦和", "六壬断案"),
    ("六壬神定经-宋-扬维德", "六壬神定经"),
    ("六壬心镜-唐-徐道符", "六壬心镜"),
    ("六壬一字诀玉连环-宋-徐汶滨", "六壬一字诀玉连环"),
    ("六壬大全-明-郭载騋", "六壬大全"),
    ("六壬粹言-清-刘赤江", "六壬粹言"),
    ("六壬存验-清-吴师青", "六壬存验"),
    ("六壬神课金口诀古本--佚名", "六壬神课金口诀"),
    ("六壬银河櫂--佚名", "六壬银河櫂"),
    ("六壬管辂神书-三国-管辂", "六壬管辂神书"),
    ("六壬直指御定-清-佚名", "六壬直指御定"),
    ("禽星易见-明-池本理", "禽星易见"),
    ("神机妙算一掌经-唐-张遂", "神机妙算一掌经"),
    ("秘本诸葛神数-三国蜀-诸葛亮", "秘本诸葛神数"),
    ("灵台秘苑-宋-王安礼", "灵台秘苑"),
    ("推背图-唐-李淳风", "推背图"),
]

ZERI_TEXTS = [
    ("钦定协纪辨方书-清-梅毂成", "协纪辨方书"),
]

ZIWEI_TEXTS = [
    ("张果星宗-明-佚名", "张果星宗"),
    ("星学大成-明-万民英", "星学大成"),
]

ZONGHE_TEXTS = [
    ("太玄经-汉-杨雄", "太玄经"),
    ("皇极经世-宋-邵雍", "皇极经世"),
    ("潜虚-宋-司马光", "潜虚"),
    ("数术记遗-汉-徐岳", "数术记遗"),
    ("二十四山八卦罗经图", "二十四山八卦罗经图"),
    ("辰州符咒大全--", "辰州符咒大全"),
]

# ── Category directory mapping ──
CATEGORY_TEXTS: dict[str, list[tuple[str, str]]] = {
    "bazi": BAZI_TEXTS,
    "yijing": YIJING_TEXTS,
    "fengshui": FENGSHUI_TEXTS,
    "mianxiang": MIANXIANG_TEXTS,
    "qimen": QIMEN_TEXTS,
    "zeri": ZERI_TEXTS,
    "ziwei": ZIWEI_TEXTS,
    "zonghe": ZONGHE_TEXTS,
}

REPO_FOLDER = "01易藏-0195部/02术数-146部"


def download_text(filename: str) -> str | None:
    """Download a single text file from the scripta-sinica repo using curl.

    Returns the text content, or None on failure.

    Note: The repo filenames all end with .txt extension.
    """
    import urllib.parse
    txt_name = filename if filename.endswith(".txt") else f"{filename}.txt"
    path = f"{REPO_FOLDER}/{txt_name}"
    # quote the path but keep / as-is to preserve directory structure
    url = f"{REPO_BASE}/{urllib.parse.quote(path, safe='/')}"

    for attempt in range(3):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w")
        tmp.close()
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "300", "-o", tmp.name, url],
                capture_output=True, text=True, timeout=360,
            )
            if result.returncode != 0:
                err = result.stderr.strip()
                logger.warning("curl failed for %s: %s (attempt %d)", filename, err, attempt + 1)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            text = Path(tmp.name).read_text(encoding="utf-8", errors="replace").strip()
            if len(text) < 100:
                # Check if it's a 404 page
                if "Not Found" in text or "404" in text[:100]:
                    logger.warning("Not found (404): %s", filename)
                    return None
                logger.warning("Text too short (%d chars): %s", len(text), filename)
                return None
            return text
        except Exception as e:
            logger.warning("Download error for %s: %s (attempt %d)", filename, e, attempt + 1)
            if attempt < 2:
                time.sleep(2 ** attempt)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return None


def download_all_texts(
    output_dir: Path,
    categories: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Download all texts from the repo.

    Args:
        output_dir: Base directory for saving text files.
        categories: Subset of categories to download, or None for all.

    Returns:
        dict mapping category -> list of {text, source, file_path} dicts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cat_filter = set(categories) if categories else None
    results: dict[str, list[dict[str, Any]]] = {}

    for category, texts in CATEGORY_TEXTS.items():
        if cat_filter and category not in cat_filter:
            continue

        cat_dir = output_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        downloaded: list[dict[str, Any]] = []
        results[category] = downloaded

        for filename, display_name in texts:
            filepath = cat_dir / f"{display_name}.txt"

            if filepath.exists() and filepath.stat().st_size > 100:
                logger.info("Already exists, skipping: %s", filepath.name)
                text = filepath.read_text(encoding="utf-8").strip()
                downloaded.append({
                    "text": text,
                    "source": display_name,
                    "file_path": str(filepath),
                })
                continue

            logger.info("Downloading: %s (%s)...", display_name, category)
            text = download_text(filename)

            if text:
                filepath.write_text(text, encoding="utf-8")
                downloaded.append({
                    "text": text,
                    "source": display_name,
                    "file_path": str(filepath),
                })
                logger.info(
                    "  Saved: %s (%d chars)",
                    filepath.name, len(text),
                )
            else:
                logger.warning("  Failed: %s", display_name)

            # Rate limiting - slow GitHub raw server needs breaks
            time.sleep(1.0)

    return results


def add_to_rag(
    results: dict[str, list[dict[str, Any]]],
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> dict[str, int]:
    """Add downloaded texts to the RAG vector database.

    Args:
        results: download_all_texts() output.
        chunk_size: Chunk size in characters.
        chunk_overlap: Overlap between chunks.

    Returns:
        dict with keys: total_chunks, total_new_chunks, per_category counts.
    """
    settings = load_settings()
    settings.vectordb_dir.mkdir(parents=True, exist_ok=True)

    print("\nLoading embedder...")
    embedder = Embedder(model_name=settings.embedding_model)

    print("Initializing vector database...")
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    # Get existing chunk IDs to avoid duplicates
    try:
        existing_ids = set(retriever.collection.get()["ids"])
        print(f"Existing chunks in DB: {len(existing_ids)}")
    except Exception:
        existing_ids = set()
        print("No existing chunks found, starting fresh")

    total_new = 0
    per_category: dict[str, int] = {}
    all_categories = set()

    for category, entries in results.items():
        cat_new = 0
        for entry in entries:
            all_categories.add(category)
            chunks = chunk_text(
                entry["text"],
                source=entry["source"],
                author="",
                category=category,
                chunk_size=chunk_size,
                overlap=chunk_overlap,
            )

            # Filter out chunks already in the DB
            new_chunks = [c for c in chunks if c.chunk_id not in existing_ids]
            if new_chunks:
                try:
                    retriever.add_chunks(new_chunks)
                    total_new += len(new_chunks)
                    cat_new += len(new_chunks)
                    # Track new IDs
                    for c in new_chunks:
                        existing_ids.add(c.chunk_id)
                    logger.info(
                        "  Added %d new chunks from %s",
                        len(new_chunks), entry["source"],
                    )
                except Exception as e:
                    logger.error("  Failed adding chunks from %s: %s", entry["source"], e)

        per_category[category] = cat_new

    return {
        "total_chunks": retriever.count(),
        "total_new_chunks": total_new,
        "per_category": per_category,
    }


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Download classical Chinese divination texts and add to RAG.",
    )
    parser.add_argument(
        "--output-dir",
        default="/mnt/d/fortune-data/books",
        help="Output directory for text files (default: /mnt/d/fortune-data/books)",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=None,
        help="Categories to download (default: all)",
    )
    parser.add_argument(
        "--skip-rag",
        action="store_true",
        help="Skip adding to RAG database (download only)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download, process existing files only",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Chunk size for RAG (default: 500)",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="Chunk overlap for RAG (default: 50)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Step 1: Download ──
    results: dict[str, list[dict[str, Any]]] = {}
    if args.skip_download:
        # Load existing files only
        output_dir = Path(args.output_dir)
        for category in (args.categories or list(CATEGORY_TEXTS.keys())):
            cat_dir = output_dir / category
            if not cat_dir.exists():
                continue
            entries = []
            for txt_file in sorted(cat_dir.glob("*.txt")):
                text = txt_file.read_text(encoding="utf-8").strip()
                if len(text) > 100:
                    entries.append({
                        "text": text,
                        "source": txt_file.stem,
                        "file_path": str(txt_file),
                    })
            if entries:
                results[category] = entries
                logger.info("Loaded %d existing texts from %s", len(entries), category)
    else:
        print("=" * 60)
        print("Step 1: Downloading classical Chinese divination texts")
        print(f"Source: xurenfei/scripta-sinica (02术数-146部)")
        print("=" * 60)
        results = download_all_texts(
            Path(args.output_dir),
            categories=args.categories,
        )

        # Print summary
        print("\n" + "=" * 60)
        print("Download Summary")
        print("=" * 60)
        total_texts = 0
        for category, entries in results.items():
            total_texts += len(entries)
            for entry in entries:
                text_len = len(entry["text"])
                print(f"  [{category}] {entry['source']}: {text_len:>6} chars")
        print(f"\nTotal: {total_texts} texts downloaded")

    if not results:
        logger.error("No texts downloaded or loaded. Nothing to do.")
        return

    # ── Step 2: Add to RAG ──
    if not args.skip_rag:
        print("\n" + "=" * 60)
        print("Step 2: Adding texts to RAG knowledge base")
        print("=" * 60)
        stats = add_to_rag(
            results,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )

        print("\n" + "=" * 60)
        print("RAG Indexing Complete")
        print("=" * 60)
        print(f"  New chunks added: {stats['total_new_chunks']}")
        print(f"  Total chunks in DB: {stats['total_chunks']}")
        for cat, count in sorted(stats["per_category"].items()):
            if count > 0:
                print(f"    [{cat}]: {count} new chunks")
        print()

    # ── Step 3: Print test queries ──
    if not args.skip_rag:
        test_queries = {
            "bazi": "乙木日主财运",
            "yijing": "梅花易数起卦方法",
            "fengshui": "风水龙脉砂水",
            "mianxiang": "面相气色吉凶",
            "zeri": "协纪辨方书择日",
            "qimen": "奇门遁甲八门",
            "ziwei": "紫微星在命宫",
            "zonghe": "五行相生相克",
        }
        settings = load_settings()
        embedder = Embedder(model_name=settings.embedding_model)
        retriever = Retriever(str(settings.vectordb_dir), embedder)

        print("\n" + "=" * 60)
        print("Test Searches")
        print("=" * 60)
        for cat, query in test_queries.items():
            results_q = retriever.search(query, category=cat, top_k=3)
            if results_q:
                print(
                    f"  [{cat}] '{query}' -> "
                    f"top: [{results_q[0].score:.3f}] "
                    f"{results_q[0].source}: {results_q[0].text[:60]}..."
                )
            else:
                print(f"  [{cat}] '{query}' -> (no results)")

        print("\nDone!")


if __name__ == "__main__":
    main()
