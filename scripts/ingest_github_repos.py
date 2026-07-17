"""Extract classical Chinese divination texts from cloned GitHub repos and add to RAG.

Cloned repos expected at:
  /tmp/suangua/
  /tmp/szbazi/
  /tmp/zhiming-ai/
  /tmp/bazi-yijing/  (may be empty)
"""
import ast
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_settings
from src.rag.chunker import chunk_text
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever

REPOS = {
    "suangua": "/tmp/suangua",
    "szbazi": "/tmp/szbazi",
    "zhiming-ai": "/tmp/zhiming-ai",
    "bazi-yijing": "/tmp/bazi-yijing",
}

OUTPUT_DIR = Path("/tmp/extracted_texts")

# Track results for reporting
extracted_sources = {}  # source -> (total_chars, file_paths)


def extract_chinese_text(text: str) -> str:
    """Extract only Chinese classical text, filtering out code/symbols."""
    # Keep Chinese characters, common punctuation, newlines
    cleaned = re.sub(r'[^一-鿿　-〿＀-￯\n。，、；：？！（）【】《》""''「」『』—…·]', '', text)
    # Collapse multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def extract_python_strings(filepath: Path) -> List[Tuple[str, str]]:
    """Extract multi-line strings and docstrings from Python files."""
    results = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(filepath))

        # Find all string literals > 100 chars
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value.strip()
                # Check if it's Chinese classical text (contains Chinese chars and is substantial)
                chinese_chars = len(re.findall(r'[一-鿿]', s))
                if chinese_chars >= 50 and len(s) >= 100:
                    # Filter out purely code strings
                    if any(keyword in s for keyword in [
                        "天干", "地支", "五行", "八字", "六爻", "易经", "周易", "奇门",
                        "紫微", "风水", "滴天髓", "三命通会", "穷通宝鉴", "子平真诠",
                        "渊海子平", "命理", "卜筮", "梅花易数", "纳甲", "六壬",
                        "太乙", "择日", "面相", "阴阳", "八卦", "用神", "日主",
                        "大运", "流年", "四柱", "格局", "比肩", "劫财", "食神",
                        "伤官", "正财", "偏财", "正官", "七杀", "正印", "偏印",
                        "长生", "沐浴", "冠带", "临官", "帝旺", "衰", "病",
                        "死", "墓", "绝", "胎", "养", "十二宫",
                        "禄", "刃", "文昌", "桃花", "驿马", "华盖", "孤辰", "寡宿",
                        "青龙", "朱雀", "勾陈", "螣蛇", "白虎", "玄武",
                    ]) or chinese_chars >= 200:
                        results.append((filepath.name, s))
    except (SyntaxError, Exception) as e:
        pass
    return results


def extract_json_chinese(filepath: Path) -> List[Tuple[str, str]]:
    """Extract Chinese text values from JSON files."""
    results = []
    try:
        data = json.loads(filepath.read_text(encoding="utf-8", errors="replace"))
        texts = []
        def _extract(obj, depth=0):
            if depth > 10:
                return
            if isinstance(obj, str):
                chinese = len(re.findall(r'[一-鿿]', obj))
                if chinese >= 50 and len(obj) >= 100:
                    texts.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _extract(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _extract(item, depth + 1)
        _extract(data)
        for t in texts:
            results.append((filepath.name, t))
    except Exception:
        pass
    return results


def extract_yaml_chinese(filepath: Path) -> List[Tuple[str, str]]:
    """Extract Chinese text from YAML files."""
    try:
        import yaml
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        # Try manual extraction
        content = filepath.read_text(encoding="utf-8", errors="replace")
        # Extract YAML string values that are long enough
        texts = []
        for match in re.finditer(r':\s*["\'](.{100,}?)["\']', content, re.DOTALL):
            t = match.group(1).strip()
            chinese = len(re.findall(r'[一-鿿]', t))
            if chinese >= 50:
                texts.append((filepath.name, t))
        # Also extract multi-line YAML strings (| and > blocks)
        for match in re.finditer(r':\s*[|>]\s*\n((?:\s+.+\n?)+)', content):
            t = "\n".join(line.strip() for line in match.group(1).split("\n") if line.strip())
            chinese = len(re.findall(r'[一-鿿]', t))
            if chinese >= 50:
                texts.append((filepath.name, t))
        return texts

    texts = []
    def _extract(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, str):
            chinese = len(re.findall(r'[一-鿿]', obj))
            if chinese >= 100:
                texts.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _extract(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _extract(item, depth + 1)
    _extract(data)
    return [(filepath.name, t) for t in texts]


def extract_md_chinese(filepath: Path) -> List[Tuple[str, str]]:
    """Extract Chinese text from markdown files."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    texts = []
    # Remove code blocks
    content_clean = re.sub(r'```[\s\S]*?```', '', content)
    # Split into sections by headers
    sections = re.split(r'^#{1,4}\s+', content_clean, flags=re.MULTILINE)
    for section in sections:
        chinese = len(re.findall(r'[一-鿿]', section))
        if chinese >= 100 and len(section) >= 150:
            texts.append(section.strip())
    return [(filepath.name, t) for t in texts]


def extract_any_file(filepath: Path) -> List[Tuple[str, str]]:
    """Try to extract Chinese text from any file type."""
    suffix = filepath.suffix.lower()
    if suffix == '.py':
        return extract_python_strings(filepath)
    elif suffix == '.json':
        return extract_json_chinese(filepath)
    elif suffix in ('.yaml', '.yml'):
        return extract_yaml_chinese(filepath)
    elif suffix in ('.md', '.mdx'):
        return extract_md_chinese(filepath)
    elif suffix == '.txt':
        text = filepath.read_text(encoding="utf-8", errors="replace")
        chinese = len(re.findall(r'[一-鿿]', text))
        if chinese >= 100:
            return [(filepath.name, text)]
    elif suffix == '.csv':
        text = filepath.read_text(encoding="utf-8", errors="replace")
        chinese = len(re.findall(r'[一-鿿]', text))
        if chinese >= 100:
            return [(filepath.name, text)]
    return []


def process_repo(repo_name: str, repo_path: str) -> List[Tuple[str, str, str]]:
    """
    Process a repo and return list of (source_name, category, text) tuples.
    """
    repo_dir = Path(repo_path)
    if not repo_dir.exists():
        print(f"  [SKIP] {repo_name}: directory not found")
        return []

    results = []  # (source_name, category, text)

    # Find all text-containing files
    all_files = list(repo_dir.rglob("*"))
    print(f"  Found {len(all_files)} total files in {repo_name}")

    for fp in all_files:
        if not fp.is_file() or fp.name.startswith('.'):
            continue

        # Skip non-text files
        suffix = fp.suffix.lower()
        if suffix not in ('.py', '.json', '.yaml', '.yml', '.md', '.mdx', '.txt', '.csv', '.toml', '.cfg'):
            continue

        # Skip __pycache__, node_modules, .git, venv
        parts = set(fp.relative_to(repo_dir).parts)
        if '__pycache__' in parts or 'node_modules' in parts or '.git' in parts or '.venv' in parts or '__init__' in fp.name:
            continue

        # Try to extract
        extracted = extract_any_file(fp)
        for src_name, text in extracted:
            # Determine category
            category = guess_category(text, src_name)
            # Clean text
            clean = extract_chinese_text(text)
            if len(clean) >= 100:
                source_key = f"{repo_name}:{src_name}"
                results.append((source_key, category, clean))

                # Also save to output dir for inspection
                out_file = OUTPUT_DIR / repo_name / src_name
                out_file.parent.mkdir(parents=True, exist_ok=True)
                # Append if multiple texts from same file
                mode = 'a' if out_file.exists() else 'w'
                with open(out_file, 'a', encoding='utf-8') as f:
                    if mode == 'a':
                        f.write('\n\n---\n\n')
                    f.write(clean)

    # Also save a combined file for easy processing
    combined = OUTPUT_DIR / f"{repo_name}_combined.txt"
    with open(combined, 'w', encoding='utf-8') as f:
        for src_name, category, text in results:
            f.write(f"=== SOURCE: {src_name} ===\n")
            f.write(f"=== CATEGORY: {category} ===\n\n")
            f.write(text)
            f.write("\n\n")

    print(f"  Extracted {len(results)} text passages from {repo_name}")
    total_chars = sum(len(t) for _, _, t in results)
    print(f"  Total characters: {total_chars:,}")

    # Print summary
    from collections import Counter
    cats = Counter(c for _, c, _ in results)
    print(f"  Categories: {dict(cats.most_common())}")

    return results


def guess_category(text: str, filename: str) -> str:
    """Guess the category of a text passage."""
    # Check filename first
    fn_lower = filename.lower()
    if any(k in fn_lower for k in ['yijing', '周易', '易经', '卦', '六爻', 'liuyao', 'najia', 'bagua', '八卦']):
        if any(k in fn_lower for k in ['liuyao', '六爻', 'najia', '纳甲']):
            return 'liuyao'
        return 'yijing'
    if any(k in fn_lower for k in ['bazi', '八字', '四柱', 'ditian', '滴天', 'sanming', '三命', 'qiongtong', '穷通', 'ziping', '子平', 'yuánhǎi', '渊海', 'shensha', '神煞', 'shishen', '十神', 'day_master', '用神', '日主']):
        return 'bazi'
    if any(k in fn_lower for k in ['qimen', '奇门', '六壬', '太乙']):
        return 'qimen'
    if any(k in fn_lower for k in ['ziwei', '紫微', '紫薇', 'dou shu', '斗数']):
        return 'ziwei'
    if any(k in fn_lower for k in ['fengshui', '风水', 'feng shui', '地理', '阴宅', '阳宅', '堪舆']):
        return 'fengshui'
    if any(k in fn_lower for k in ['mianxiang', '面相', '手相', '人相']):
        return 'mianxiang'
    if any(k in fn_lower for k in ['zeri', '择日', '选择', '黄历', '协纪']):
        return 'zeri'
    if any(k in fn_lower for k in ['date_selection', 'date', 'calendar', 'calendar']):
        return 'zeri'
    if any(k in fn_lower for k in ['xingxiu', '星宿', '天文']):
        return 'zonghe'

    # Check text content
    bazi_keywords = ['八字', '四柱', '日主', '用神', '大运', '流年', '偏财', '正官', '七杀', '比肩', '劫财', '食神', '伤官', '正印', '偏印', '格局']
    yijing_keywords = ['卦象', '爻位', '六爻', '周易', '八卦', '乾坤', '坎离', '艮兑', '巽震']
    qimen_keywords = ['奇门', '八门', '九星', '遁甲', '六壬', '三传', '四课']
    ziwei_keywords = ['紫微', '天机', '太阳', '武曲', '天同', '廉贞', '命宫', '身宫']
    fengshui_keywords = ['风水', '龙脉', '砂水', '理气', '峦头', '山向', '罗盘']

    # Score each category
    scores = {
        'bazi': sum(1 for k in bazi_keywords if k in text),
        'yijing': sum(1 for k in yijing_keywords if k in text),
        'liuyao': sum(1 for k in ['六爻', '纳甲', '卦爻', '世应'] if k in text),
        'qimen': sum(1 for k in qimen_keywords if k in text),
        'ziwei': sum(1 for k in ziwei_keywords if k in text),
        'fengshui': sum(1 for k in fengshui_keywords if k in text),
    }

    best = max(scores, key=scores.get)
    if scores[best] >= 3:
        return best

    return 'zonghe'


def main():
    """Main extraction and ingestion."""
    print("=" * 60)
    print("EXTRACTING CLASSICAL CHINESE TEXTS FROM GITHUB REPOS")
    print("=" * 60)

    # Process each repo
    all_results = {}  # repo_name -> list of (source, category, text)
    combined_sources = {}

    for repo_name, repo_path in REPOS.items():
        print(f"\n--- Processing {repo_name} ({repo_path}) ---")
        results = process_repo(repo_name, repo_path)
        if results:
            all_results[repo_name] = results
            for src, cat, text in results:
                combined_sources[src] = (cat, text)

    print(f"\n{'=' * 60}")
    print(f"TOTAL EXTRACTED: {len(combined_sources)} unique text passages")
    total_chars = sum(len(t) for _, t in combined_sources.values())
    print(f"TOTAL CHARACTERS: {total_chars:,}")

    # Chunk and add to RAG
    print(f"\n{'=' * 60}")
    print("CHUNKING AND ADDING TO RAG VECTOR DB")
    print(f"{'=' * 60}")

    settings = load_settings()
    embedder = Embedder(model_name=settings.embedding_model)
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    # Check existing count
    existing_count = retriever.count()
    print(f"Current DB count: {existing_count}")

    # Get existing IDs to avoid duplicates
    try:
        existing_ids = set(retriever.collection.get()["ids"])
    except Exception:
        existing_ids = set()

    all_chunks = []
    for src, (cat, text) in combined_sources.items():
        chunks = chunk_text(
            text, source=src, author="", category=cat,
            chunk_size=800, overlap=80,
        )
        for c in chunks:
            if c.chunk_id not in existing_ids:
                all_chunks.append(c)

    print(f"New unique chunks to add: {len(all_chunks)}")

    if not all_chunks:
        print("No new chunks to add. All already in DB.")
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

        print(f"  Batch {batch_start}-{batch_end}/{len(all_chunks)}: "
              f"embedded in {t1-t0:.2f}s, stored in {t2-t1:.2f}s | "
              f"{total_added}/{len(all_chunks)} chunks | elapsed {elapsed:.1f}s")

    total_elapsed = time.time() - t_start
    final_count = retriever.count()
    new_count = final_count - existing_count

    print(f"\n{'=' * 60}")
    print(f"INGESTION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Previous DB size: {existing_count}")
    print(f"New DB size:      {final_count}")
    print(f"New chunks added: {new_count}")
    print(f"Time elapsed:     {total_elapsed:.1f}s ({total_elapsed/max(new_count,1)*1000:.1f}ms/chunk)")

    # Save combined extracted text for inspection
    combined_path = Path("/tmp/all_github_extractions.json")
    with open(combined_path, 'w', encoding='utf-8') as f:
        json.dump({
            "sources": list(combined_sources.keys()),
            "total_sources": len(combined_sources),
            "total_chars": total_chars,
            "new_chunks": new_count,
            "final_db_size": final_count,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nSummary saved to: {combined_path}")


if __name__ == "__main__":
    main()
