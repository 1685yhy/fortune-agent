"""OCR古籍PDF - 扫描/文本PDF提取文字并保存为txt."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

logger = logging.getLogger(__name__)

# Default books directory
DEFAULT_BOOKS_DIR = Path("/mnt/d/fortune-data/books")

# Supported image extensions (for PaddleOCR)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}


def _import_pymupdf():
    """Import PyMuPDF (fitz), returning None if not available."""
    try:
        import fitz  # type: ignore[import-untyped]
        return fitz
    except ImportError:
        return None


def _import_paddleocr():
    """Import PaddleOCR, returning None if not available."""
    try:
        from paddleocr import PaddleOCR  # type: ignore[import-untyped]
        return PaddleOCR
    except ImportError:
        return None


def is_scanned_pdf(doc: Any) -> bool:
    """Heuristic: a PDF is "scanned" if most pages have no extractable text."""
    text_pages = 0
    total_pages = len(doc)
    if total_pages == 0:
        return True

    # Sample up to 10 pages
    sample_size = min(total_pages, 10)
    for i in range(sample_size):
        page = doc[i]
        text = page.get_text().strip()
        if len(text) > 20:
            text_pages += 1

    return text_pages / sample_size < 0.3


def extract_text_pymupdf(pdf_path: Path) -> str | None:
    """Extract text from a PDF using PyMuPDF (fitz).

    Returns extracted text on success, None on failure.
    """
    fitz = _import_pymupdf()
    if fitz is None:
        logger.warning("PyMuPDF not installed, cannot extract: %s", pdf_path)
        return None

    try:
        doc = fitz.open(str(pdf_path))
        pages_text: list[str] = []
        for page in doc:
            text = page.get_text().strip()
            if text:
                pages_text.append(text)
        doc.close()

        if not pages_text:
            return None
        return "\n\n".join(pages_text)
    except Exception as e:
        logger.warning("PyMuPDF error for %s: %s", pdf_path, e)
        return None


def extract_text_paddleocr(pdf_path: Path) -> str | None:
    """Extract text from a scanned PDF using PaddleOCR.

    This converts each page to an image first (requires pdf2image) then OCRs.
    Returns extracted text on success, None on failure.
    """
    PaddleOCR = _import_paddleocr()
    if PaddleOCR is None:
        logger.debug("PaddleOCR not installed, skipping OCR for: %s", pdf_path)
        return None

    try:
        # pdf2image is needed to render pages
        from pdf2image import convert_from_path  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pdf2image not installed, cannot OCR scanned PDF: %s", pdf_path)
        return None

    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)

        images = convert_from_path(str(pdf_path), dpi=200)
        all_text: list[str] = []

        for img in images:
            result = ocr.ocr(img, cls=True)
            if result and result[0]:
                page_lines = [line[1][0] for line in result[0] if line and len(line) > 1 and line[1]]
                if page_lines:
                    all_text.extend(page_lines)

        return "\n".join(all_text) if all_text else None
    except Exception as e:
        logger.warning("PaddleOCR error for %s: %s", pdf_path, e)
        return None


def ocr_pdf(pdf_path: Path) -> str | None:
    """OCR a single PDF file and return extracted text.

    Strategy:
    1. Try PyMuPDF -- works for text-based PDFs.
    2. If text is empty / scanned, try PaddleOCR (if available).
    3. If PaddleOCR not available, log a warning and return None.
    """
    if not pdf_path.exists():
        logger.warning("File not found: %s", pdf_path)
        return None
    if pdf_path.suffix.lower() not in (".pdf",):
        logger.warning("Not a PDF: %s", pdf_path)
        return None

    fitz = _import_pymupdf()

    # Step 1: PyMuPDF text extraction
    if fitz:
        try:
            doc = fitz.open(str(pdf_path))
            scanned = is_scanned_pdf(doc)
            doc.close()
        except Exception:
            scanned = True

        text = extract_text_pymupdf(pdf_path)
        if text and len(text) > 20:
            return text

        if scanned:
            logger.info(
                "Scanned PDF (no extractable text): %s", pdf_path,
            )
    else:
        scanned = True
        text = None

    # Step 2: Fall back to PaddleOCR for scanned PDFs
    if scanned or not text or len(text) <= 20:
        paddle_text = extract_text_paddleocr(pdf_path)
        if paddle_text:
            return paddle_text

        if scanned:
            logger.warning(
                "Skipping scanned PDF (PaddleOCR not available): %s",
                pdf_path,
            )
        else:
            logger.warning(
                "No text extracted from PDF: %s",
                pdf_path,
            )

    return None


def ocr_books_directory(
    books_dir: str | Path,
    output_dir: str | Path | None = None,
    redo: bool = False,
) -> dict[str, Any]:
    """Walk a books directory, OCR every PDF, and save .txt alongside.

    Args:
        books_dir: Root directory containing category subdirs.
        output_dir: If set, save .txt files here mirroring the subdir structure.
            If None, save .txt alongside the PDF.
        redo: If True, re-OCR even if .txt already exists.

    Returns:
        dict with keys: total, success, skipped_scanned, failed
    """
    books_dir = Path(books_dir)
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(books_dir.rglob("*.pdf"))
    total = len(pdf_files)
    success = 0
    skipped_scanned = 0
    failed = 0

    logger.info("Found %d PDF files in %s", total, books_dir)

    for pdf_path in tqdm(pdf_files, desc="OCR books", unit="pdf"):
        # Determine output .txt path
        if output_dir:
            rel = pdf_path.relative_to(books_dir)
            txt_path = output_dir / rel.with_suffix(".txt")
            txt_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            txt_path = pdf_path.with_suffix(".txt")

        if txt_path.exists() and txt_path.stat().st_size > 0 and not redo:
            logger.debug("Already processed: %s", txt_path)
            success += 1  # count as success since we have the output
            continue

        text = ocr_pdf(pdf_path)

        if text:
            txt_path.write_text(text, encoding="utf-8")
            success += 1
        elif text is None:
            # Determine why it failed
            fitz = _import_pymupdf()
            if fitz:
                try:
                    doc = fitz.open(str(pdf_path))
                    scanned = is_scanned_pdf(doc)
                    doc.close()
                except Exception:
                    scanned = True
                if scanned:
                    skipped_scanned += 1
                else:
                    failed += 1
            else:
                skipped_scanned += 1

    result = {
        "total": total,
        "success": success,
        "skipped_scanned": skipped_scanned,
        "failed": failed,
    }
    logger.info(
        "OCR complete: %d success, %d scanned (skipped), %d failed / %d total",
        success, skipped_scanned, failed, total,
    )
    return result


def main() -> None:
    """Run OCR on all PDFs in the books directory."""
    import argparse

    parser = argparse.ArgumentParser(
        description="OCR fortune-telling PDFs and save as .txt files.",
    )
    parser.add_argument(
        "--books-dir",
        default=str(DEFAULT_BOOKS_DIR),
        help=f"Books directory (default: {DEFAULT_BOOKS_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for .txt files (default: same dir as PDFs)",
    )
    parser.add_argument(
        "--redo",
        action="store_true",
        help="Re-OCR even if .txt already exists",
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

    ocr_books_directory(
        books_dir=args.books_dir,
        output_dir=args.output_dir,
        redo=args.redo,
    )


if __name__ == "__main__":
    main()
