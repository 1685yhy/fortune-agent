"""Compare our dream interpretation against authoritative reference sites.

Tests common dreams against multiple sources and reports overlap.
"""
import urllib.request, urllib.parse, json, time, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.engines.dream import DreamEngine

# 50 common dream scenarios for testing
COMMON_DREAMS = [
    "梦见蛇", "梦见掉牙齿", "梦见水", "梦见死人", "梦见鱼",
    "梦见考试", "梦见飞", "梦见被追杀", "梦见鬼", "梦见血",
    "梦见结婚", "梦见怀孕", "梦见狗", "梦见猫", "梦见老虎",
    "梦见火", "梦见下雨", "梦见哭", "梦见钱", "梦见打架",
    "梦见坐车", "梦见迷路", "梦见从高处坠落", "梦见爬山", "梦见游泳",
    "梦见吃饭", "梦见洗澡", "梦见地震", "梦见花开", "梦见打雷",
    "梦见月亮", "梦见太阳", "梦见星星", "梦见雪", "梦见桥",
    "梦见房子倒塌", "梦见母亲", "梦见父亲", "梦见同学", "梦见老师",
    "梦见前任", "梦见明星", "梦见世界末日", "梦见自己死了",
    "梦见偷东西", "梦见捡钱", "梦见生病", "梦见医院",
    "梦见大象", "梦见虫子",
]

def search_buyiju(keyword):
    """Search buyiju.com dream dictionary."""
    try:
        url = f"https://m.buyiju.com/zgjm/{urllib.parse.quote(keyword)}.html"
        resp = urllib.request.urlopen(url, timeout=10)
        html = resp.read().decode("utf-8", errors="ignore")
        # Extract interpretation text
        match = re.search(r'<div class="content"[^>]*>(.*?)</div>', html, re.DOTALL)
        if match:
            text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            return text[:500]
        return ""
    except Exception:
        return ""

def search_zgjmorg(keyword):
    """Search zgjmorg.com dream dictionary."""
    try:
        url = f"https://www.zgjmorg.com/search.php?q={urllib.parse.quote(keyword)}"
        resp = urllib.request.urlopen(url, timeout=10)
        html = resp.read().decode("utf-8", errors="ignore")
        # Extract first result snippet
        match = re.search(r'<li[^>]*>.*?<a[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>', html, re.DOTALL)
        if match:
            title = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            text = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            return f"{title}: {text}"[:500]
        return ""
    except Exception:
        return ""

def our_interpretation(keyword):
    """Get our engine's interpretation."""
    try:
        from src.engines.dream import DreamEngine
        from src.rag.retriever import Retriever
        from src.rag.embedder import Embedder
        from src.config import load_settings

        settings = load_settings()
        embedder = Embedder()
        retriever = Retriever(str(settings.vectordb_dir), embedder)
        engine = DreamEngine()
        result = engine.analyze(keyword, retriever, api_key="")
        if result.interpretations:
            return result.interpretations[0][:500]
        return ""
    except Exception as e:
        return f"ERROR: {e}"


if __name__ == "__main__":
    print("=" * 70)
    print("解梦对比测试 — 50个常见梦境")
    print("=" * 70)

    buyiju_hits = 0
    zgjmorg_hits = 0

    for i, dream in enumerate(COMMON_DREAMS[:20], 1):  # Test first 20 first
        print(f"\n[{i}/20] {dream}")

        # Our result
        ours = our_interpretation(dream)
        has_ours = len(ours) > 20

        # Reference sites
        bj = search_buyiju(dream)
        zg = search_zgjmorg(dream)
        has_bj = len(bj) > 20
        has_zg = len(zg) > 20

        if has_bj:
            buyiju_hits += 1
        if has_zg:
            zgjmorg_hits += 1

        print(f"  我们: {'✅' if has_ours else '❌'} ({len(ours)}字)")
        print(f"  卜易居: {'✅' if has_bj else '❌'} ({len(bj)}字)")
        print(f"  周公解梦: {'✅' if has_zg else '❌'} ({len(zg)}字)")

        # Show samples
        if has_ours and has_bj:
            # Check keyword overlap
            our_words = set(ours[:100])
            bj_words = set(bj[:100])
            overlap = len(our_words & bj_words) / max(len(our_words | bj_words), 1)
            print(f"  重叠度: {overlap*100:.0f}%")

        time.sleep(0.5)

    print(f"\n{'='*70}")
    print(f"覆盖率: 卜易居 {buyiju_hits}/20 | 周公解梦 {zgjmorg_hits}/20")
