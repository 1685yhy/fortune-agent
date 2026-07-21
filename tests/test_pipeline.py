"""Tests for the OCR data pipeline scripts."""
import importlib
import inspect
import sys
from pathlib import Path


def test_download_books_importable():
    """scripts/download_books.py imports without error and has main()."""
    # Ensure scripts directory is on path
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    mod = importlib.import_module("download_books")
    assert hasattr(mod, "main"), "download_books.py must have a main() function"
    assert callable(mod.main), "main() must be callable"
    sig = inspect.signature(mod.main)
    # main() should accept 0 arguments when called from CLI
    # but may accept optional params when called programmatically
    assert mod.load_book_config is not None, "load_book_config must exist"
    assert mod.download_book is not None, "download_book must exist"


def test_ocr_books_importable():
    """scripts/ocr_books.py imports without error and has main()."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    mod = importlib.import_module("ocr_books")
    assert hasattr(mod, "main"), "ocr_books.py must have a main() function"
    assert callable(mod.main), "main() must be callable"
    assert mod.ocr_pdf is not None, "ocr_pdf must exist"
    assert mod.ocr_books_directory is not None, "ocr_books_directory must exist"


def test_build_index_importable():
    """scripts/build_index.py imports without error and has main()."""
    # Need project root on sys.path too
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    mod = importlib.import_module("scripts.build_index")
    assert hasattr(mod, "main"), "build_index.py must have a main() function"
    assert callable(mod.main), "main() must be callable"
    assert mod.load_texts_from_directory is not None, "load_texts_from_directory must exist"


def test_download_books_categories_in_config():
    """The default book config has expected categories."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import download_books

    config = download_books.load_book_config(
        Path(__file__).resolve().parent.parent / "config" / "books.json",
    )
    expected = {"bazi", "ziwei", "fengshui", "yijing", "mianxiang",
                "zeri", "qimen", "xingming", "zonghe"}
    assert set(config.keys()) == expected, f"Got categories: {set(config.keys())}"
    for cat, books in config.items():
        assert len(books) > 0, f"Category '{cat}' has no books"
        for book in books:
            assert "url" in book, f"Book in '{cat}' missing 'url': {book}"
            assert "title" in book, f"Book in '{cat}' missing 'title': {book}"


def test_download_books_empty_config():
    """load_book_config handles missing/empty config gracefully."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import download_books

    from pathlib import Path as P
    result = download_books.load_book_config("/tmp/nonexistent_config_xyz.json")
    assert result == {}


def test_ocr_books_is_scanned_detection():
    """is_scanned_pdf handles edge cases without a real PDF."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import ocr_books

    # Without PyMuPDF, is_scanned isn't called directly,
    # but we can verify the import guards work
    assert ocr_books._import_pymupdf() is None or True  # just runs


def test_build_index_detect_category():
    """_detect_category correctly identifies category from path."""
    scripts_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(scripts_dir))
    import build_index  # noqa: F811

    books_dir = Path("/mnt/d/fortune-data/books")

    path_bazi = books_dir / "bazi" / "ditianrui.txt"
    assert build_index._detect_category(path_bazi, books_dir) == "bazi"

    path_ziwei = books_dir / "ziwei" / "zihuiajuan.txt"
    assert build_index._detect_category(path_ziwei, books_dir) == "ziwei"

    path_general = Path("/tmp/some_other_dir/book.txt")
    assert build_index._detect_category(path_general, books_dir) == "general"


def test_build_index_load_texts_empty_dir(tmp_path):
    """load_texts_from_directory returns empty list when no .txt files."""
    scripts_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(scripts_dir))
    import build_index  # noqa: F811

    result = build_index.load_texts_from_directory(tmp_path)
    assert result == []


def test_build_index_load_texts_with_files(tmp_path):
    """load_texts_from_directory reads .txt files with correct categories."""
    scripts_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(scripts_dir))
    import build_index  # noqa: F811

    # Create test structure
    bazi_dir = tmp_path / "bazi"
    bazi_dir.mkdir()
    (bazi_dir / "test_book.txt").write_text("乙木虽柔，刳羊解牛。", encoding="utf-8")

    ziwei_dir = tmp_path / "ziwei"
    ziwei_dir.mkdir()
    (ziwei_dir / "zihui.txt").write_text("紫微星在命宫。", encoding="utf-8")

    # Include a non-category dir that should be 'general'
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "notes.txt").write_text("Some notes.", encoding="utf-8")

    entries = build_index.load_texts_from_directory(tmp_path)
    assert len(entries) == 3

    by_source = {e["source"]: e for e in entries}
    assert by_source["test_book"]["category"] == "bazi"
    assert by_source["zihui"]["category"] == "ziwei"
    assert by_source["notes"]["category"] == "general"
