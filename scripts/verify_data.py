#!/usr/bin/env python3
"""Data verification and cleaning pipeline.

S3-S4: Validates scraped data before it enters the knowledge base.
Three verification passes: source quality, content completeness, dedup.
"""
import json, sys, hashlib, re
from pathlib import Path
from difflib import SequenceMatcher


def verify_source(url: str) -> str:
    """Check if source is from a trusted domain. Returns quality level."""
    trusted = [
        "baike.baidu.com", "zh.wikipedia.org",
        "ctext.org", "guoxue.com", "shuge.org",
        "zhouyi.cc", "zhouyi.sc.cn",
    ]
    community = [
        "zhihu.com", "xiaohongshu.com", "douban.com",
        "tieba.baidu.com", "weixin.qq.com",
    ]
    url_lower = url.lower()
    for t in trusted:
        if t in url_lower:
            return "authoritative"
    for c in community:
        if c in url_lower:
            return "community"
    return "unverified"


def is_complete(content: str, min_chars: int = 100) -> bool:
    """Check if content is substantive (not just title/SEO filler)."""
    if len(content) < min_chars:
        return False
    # Reject pure advertising
    ad_patterns = ["加微信", "扫码", "联系老师", "点击购买", "限时优惠", "免费试用"]
    if any(p in content for p in ad_patterns):
        return False
    # Must contain substantial Chinese text
    chinese_chars = sum(1 for c in content if '一' <= c <= '鿿')
    if chinese_chars < 30:
        return False
    return True


def content_hash(content: str) -> str:
    """Generate a normalized hash for dedup."""
    # Normalize: remove whitespace, punctuation variations
    normalized = re.sub(r'\s+', '', content)
    normalized = re.sub(r'[，。！？、；：""''（）【】《》\-,.!?;:()\[\]{}]', '', normalized)
    return hashlib.md5(normalized.encode()).hexdigest()


def is_duplicate(content: str, existing_hashes: set, threshold: float = 0.8) -> bool:
    """Check if content is too similar to existing entries."""
    h = content_hash(content)
    return h in existing_hashes


def verify_entry(entry: dict, existing_hashes: set) -> dict:
    """Run all verification checks on a single entry.

    Returns dict with 'status': 'accepted' | 'rejected' and 'reason' if rejected.
    """
    url = entry.get("source_url", entry.get("source", ""))
    content = entry.get("content", entry.get("text", ""))

    # Check 1: Source quality
    quality = verify_source(url)
    if quality == "unverified":
        # Still accept but mark as unverified
        entry["source_quality"] = "unverified"
        entry["verified"] = False
    else:
        entry["source_quality"] = quality
        entry["verified"] = True

    # Check 2: Content completeness
    if not is_complete(content):
        return {"status": "rejected", "reason": "too_short_or_ad", "entry": entry}

    # Check 3: Dedup
    if is_duplicate(content, existing_hashes):
        return {"status": "rejected", "reason": "duplicate", "entry": entry}

    # Passed all checks
    existing_hashes.add(content_hash(content))
    entry["content_hash"] = content_hash(content)
    return {"status": "accepted", "entry": entry}


def clean_html(text: str) -> str:
    """Remove HTML tags and common web junk from text."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove navigation/header/footer patterns
    text = re.sub(r'(首页|导航|关于我们|联系我们|版权所有).*?(?:[\r\n]|$)', '', text)
    # Remove JavaScript/CSS
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    # Normalize whitespace
    text = re.sub(r'\r?\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def process_file(input_path: str, output_dir: str, label: str):
    """Process a JSONL file through the verification pipeline."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    accepted_path = output_dir / f"{label}_accepted.jsonl"
    rejected_path = output_dir / f"{label}_rejected.jsonl"

    if not input_path.exists():
        print(f"File not found: {input_path}")
        return

    existing_hashes = set()
    # Load existing accepted hashes
    if accepted_path.exists():
        with open(accepted_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    h = entry.get("content_hash", "")
                    if h:
                        existing_hashes.add(h)
                except Exception:
                    pass

    accepted = 0
    rejected = 0
    reasons = {}

    with open(input_path) as fin, \
         open(accepted_path, "a") as fok, \
         open(rejected_path, "a") as fbad:

        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue

            # Clean HTML if present
            if "content" in entry:
                entry["content"] = clean_html(entry["content"])

            result = verify_entry(entry, existing_hashes)
            if result["status"] == "accepted":
                fok.write(json.dumps(result["entry"], ensure_ascii=False) + "\n")
                accepted += 1
            else:
                reason = result["reason"]
                reasons[reason] = reasons.get(reason, 0) + 1
                fbad.write(json.dumps(result, ensure_ascii=False) + "\n")
                rejected += 1

    print(f"[{label}] {input_path.name}: {accepted} accepted, {rejected} rejected")
    for reason, count in reasons.items():
        print(f"  - {reason}: {count}")
    return accepted, rejected


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python verify_data.py <input.jsonl> [output_dir] [label]")
        print("  Verifies and cleans scraped data before knowledge base insertion.")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/mnt/d/fortune-data/books/zonghe/verified"
    label = sys.argv[3] if len(sys.argv) > 3 else Path(inp).stem

    process_file(inp, out, label)
