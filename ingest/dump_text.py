"""Dump each book's local text layer to a .txt file -- zero API calls.

Used for the manual ingestion workflow: Claude reads these text files
directly (as part of the coding session, not the app's API key) and extracts
recipes itself, then saves them via save_recipes.py.

Usage:
    python -m ingest.dump_text path/to/books.zip --out _scratch/book_text
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.ingest_book import (
    MIN_CHARS_PER_PAGE,
    _collect_book_files,
    _extract_pdf_text,
    _iter_epub_text,
)


def main():
    parser = argparse.ArgumentParser(description="Dump book text to local .txt files (no API calls).")
    parser.add_argument("path", type=Path, help="Book file, folder, or .zip")
    parser.add_argument("--out", type=Path, default=Path("_scratch/book_text"), help="Output directory")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Path not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)

    book_files, _ = _collect_book_files(args.path)
    if not book_files:
        print("No .pdf or .epub files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(book_files)} book(s).\n")

    for book_path in book_files:
        suffix = book_path.suffix.lower()
        if suffix == ".pdf":
            text, page_count = _extract_pdf_text(book_path)
            avg = len(text) / max(page_count, 1)
            flag = "" if avg >= MIN_CHARS_PER_PAGE else "  ** LOW TEXT YIELD -- likely scanned, check manually **"
        else:
            text = _iter_epub_text(book_path)
            page_count = None
            flag = ""

        out_path = args.out / f"{book_path.stem}.txt"
        out_path.write_text(text, encoding="utf-8")
        pages_note = f"{page_count} pages, " if page_count else ""
        print(f"  {book_path.name}: {pages_note}{len(text):,} chars -> {out_path}{flag}")

    print(f"\nDone. No API calls made. Text files are in {args.out}")


if __name__ == "__main__":
    main()
