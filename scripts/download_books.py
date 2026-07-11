"""下载古籍PDF - 从配置文件读取书籍URL并下载到本地."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm

logger = logging.getLogger(__name__)

# Default book URL config
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "books.json"

# Output base directory
DEFAULT_OUTPUT_DIR = Path("/mnt/d/fortune-data/books")

# Supported categories
CATEGORIES = [
    "bazi", "ziwei", "fengshui", "yijing", "mianxiang",
    "zeri", "qimen", "xingming", "zonghe",
]


def load_book_config(config_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load book URL configuration from a JSON file.

    Returns a dict mapping category -> list of {url, title, author}.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        logger.warning("Config file %s not found, returning empty config", config_path)
        return {}
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)
    # Validate & return only known categories
    result: dict[str, list[dict[str, Any]]] = {}
    for cat, books in raw.items():
        if cat in CATEGORIES:
            result[cat] = books
        else:
            logger.warning("Unknown category '%s' in config, skipping", cat)
    return result


def _aria2c_available() -> bool:
    """Check if aria2c is installed."""
    return shutil.which("aria2c") is not None


def download_with_aria2c(url: str, output_path: Path) -> bool:
    """Download a file using aria2c."""
    cmd = [
        "aria2c",
        "-x", "4",          # 4 connections
        "-s", "4",          # 4 splits
        "--continue",       # support resume
        "--quiet",
        "-d", str(output_path.parent),
        "-o", output_path.name,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.warning("aria2c failed for %s: %s", url, result.stderr.strip())
            return False
        return True
    except Exception as e:
        logger.warning("aria2c exception for %s: %s", url, e)
        return False


def download_with_requests(url: str, output_path: Path) -> bool:
    """Download a file using Python requests with chunked streaming."""
    try:
        resp = requests.get(url, stream=True, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FortuneAgent/1.0)",
        })
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        desc = output_path.name[:40]
        with open(output_path, "wb") as f:
            with tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                desc=desc,
                leave=False,
            ) as pbar:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        return True
    except Exception as e:
        logger.warning("requests download failed for %s: %s", url, e)
        return False


def download_book(
    url: str,
    output_path: Path,
    use_aria2: bool | None = None,
) -> bool:
    """Download a single book, returning True on success.

    If use_aria2 is None, auto-detect: prefer aria2c if installed.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and output_path.stat().st_size > 0:
        logger.info("Already exists: %s", output_path)
        return True

    if use_aria2 is None:
        use_aria2 = _aria2c_available()

    if use_aria2:
        logger.debug("Downloading with aria2c: %s", url)
        success = download_with_aria2c(url, output_path)
        if success:
            return True
        logger.info("aria2c failed, falling back to requests: %s", url)

    logger.debug("Downloading with requests: %s", url)
    return download_with_requests(url, output_path)


def download_category(
    books: list[dict[str, Any]],
    output_dir: Path,
    category: str,
) -> tuple[int, int]:
    """Download all books for a category.

    Returns (success_count, total_count).
    """
    success = 0
    for book in books:
        url = book.get("url", "")
        if not url:
            logger.warning("Book entry missing 'url': %s", book)
            continue
        title = book.get("title", url.rsplit("/", 1)[-1])
        filename = f"{title}.pdf"
        output_path = output_dir / category / filename

        ok = download_book(url, output_path)
        if ok:
            success += 1
        else:
            logger.error("Failed to download: %s (%s)", title, url)

    return success, len(books)


def main() -> None:
    """Download all books from the config file."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Download fortune-telling books (PDFs) from config URLs.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Book config JSON path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=None,
        help=f"Specific categories to download, e.g. bazi ziwei (default: all: {', '.join(CATEGORIES)})",
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

    config = load_book_config(args.config)
    if not config:
        logger.error("No book config loaded. Exiting.")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    categories = args.categories or CATEGORIES
    total_success = 0
    total_all = 0

    for cat in categories:
        books = config.get(cat)
        if not books:
            logger.warning("No books configured for category '%s', skipping", cat)
            continue

        logger.info("Downloading category: %s (%d books)", cat, len(books))
        success, count = download_category(books, output_dir, cat)
        total_success += success
        total_all += count

    logger.info(
        "Download complete: %d/%d books successful (output: %s)",
        total_success,
        total_all,
        output_dir,
    )


if __name__ == "__main__":
    main()
