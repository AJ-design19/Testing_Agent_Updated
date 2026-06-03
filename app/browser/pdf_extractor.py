"""
PDF Text Extractor.

Extracts all text from a downloaded PDF file.
Uses pypdf (pure-Python, no system dependencies).
Falls back to pdfminer.six for complex PDFs with non-standard encodings.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text(pdf_path: str) -> dict:
    """
    Extract all text from a PDF file.

    Returns:
        {
            "path":       str,           # input path
            "success":    bool,
            "pages":      int,           # total pages
            "text":       str,           # full concatenated text
            "pages_text": [str, ...],    # per-page text
            "char_count": int,
            "error":      str | None,
        }
    """
    result = {
        "path":       pdf_path,
        "success":    False,
        "pages":      0,
        "text":       "",
        "pages_text": [],
        "char_count": 0,
        "error":      None,
    }

    if not os.path.exists(pdf_path):
        result["error"] = f"File not found: {pdf_path}"
        logger.error("[PDFExtractor] %s", result["error"])
        return result

    # ── Primary: pypdf ─────────────────────────────────────────────────────────
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        pages_text = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception as pe:
                logger.debug("[PDFExtractor] pypdf page %d error: %s", i, pe)
                text = ""
            pages_text.append(text)

        full_text = "\n\n".join(pages_text).strip()

        if full_text:
            result.update({
                "success":    True,
                "pages":      len(pages_text),
                "text":       full_text,
                "pages_text": pages_text,
                "char_count": len(full_text),
            })
            logger.info(
                "[PDFExtractor] pypdf extracted %d chars from %d pages: %s",
                len(full_text), len(pages_text), pdf_path,
            )
            return result

        logger.info("[PDFExtractor] pypdf returned empty text — trying pdfminer fallback")

    except ImportError:
        logger.warning("[PDFExtractor] pypdf not installed — trying pdfminer.six")
    except Exception as e:
        logger.warning("[PDFExtractor] pypdf failed: %s — trying pdfminer.six", e)

    # ── Fallback: pdfminer.six ─────────────────────────────────────────────────
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer

        # Per-page extraction
        pages_text = []
        try:
            for page_layout in extract_pages(pdf_path):
                page_text = ""
                for element in page_layout:
                    if isinstance(element, LTTextContainer):
                        page_text += element.get_text()
                pages_text.append(page_text.strip())
        except Exception:
            # Full-document fallback
            full = pdfminer_extract(pdf_path) or ""
            pages_text = [full]

        full_text = "\n\n".join(pages_text).strip()

        result.update({
            "success":    bool(full_text),
            "pages":      len(pages_text),
            "text":       full_text,
            "pages_text": pages_text,
            "char_count": len(full_text),
        })
        if full_text:
            logger.info(
                "[PDFExtractor] pdfminer extracted %d chars from %d pages: %s",
                len(full_text), len(pages_text), pdf_path,
            )
        else:
            result["error"] = "Both pypdf and pdfminer returned empty text"
            logger.warning("[PDFExtractor] Empty extraction: %s", pdf_path)
        return result

    except ImportError:
        result["error"] = (
            "No PDF library available. Install pypdf: pip install pypdf\n"
            "Or pdfminer.six: pip install pdfminer.six"
        )
        logger.error("[PDFExtractor] %s", result["error"])
    except Exception as e:
        result["error"] = f"pdfminer failed: {e}"
        logger.error("[PDFExtractor] %s", result["error"])

    return result


def extract_sections(text: str) -> dict:
    """
    Parse BRD section headers from extracted text.
    Returns a dict of {section_title: section_body}.
    Handles common BRD heading patterns: numbered (1. / 1.1), ALL CAPS, Title Case.
    """
    import re

    sections: dict[str, str] = {}
    if not text:
        return sections

    # Patterns: "1. Title", "1.1 Title", "## Title", "TITLE", "Title Case Heading"
    heading_re = re.compile(
        r"^(?:"
        r"(?:\d+\.)+\s+[A-Z].{2,60}"   # 1. / 1.1 numbered
        r"|#{1,3}\s+.{2,60}"            # markdown ##
        r"|[A-Z][A-Z\s]{4,60}$"         # ALL CAPS
        r"|(?:[A-Z][a-z]+\s){2,6}$"     # Title Case
        r")",
        re.MULTILINE,
    )

    lines = text.splitlines()
    current_section = "_intro"
    current_body: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            current_body.append("")
            continue
        if heading_re.match(stripped) and len(stripped) < 80:
            # Save previous section
            sections[current_section] = "\n".join(current_body).strip()
            current_section = stripped
            current_body = []
        else:
            current_body.append(line)

    sections[current_section] = "\n".join(current_body).strip()

    # Remove empty intro if nothing was captured before first heading
    if not sections.get("_intro"):
        sections.pop("_intro", None)

    logger.info("[PDFExtractor] Parsed %d sections from BRD text", len(sections))
    return sections
