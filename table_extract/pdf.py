"""PDF access through pdfium: page counts and page images.

pdfium replaces Poppler (pdf2image), which has no reliable builds to ship on
Windows. pdfium is not thread-safe, so every call into it holds _LOCK.
"""

import threading
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

_LOCK = threading.RLock()


def page_count(pdf_path: Path) -> int:
    with _LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(pdf)
        finally:
            pdf.close()


def render_page(pdf_path: Path, page_number: int, dpi: int) -> Image.Image:
    """The 1-based page as an RGB image at the given resolution."""
    with _LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            page = pdf[page_number - 1]
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil().convert("RGB")  # a copy, independent of the bitmap
            bitmap.close()
            page.close()
            return image
        finally:
            pdf.close()
