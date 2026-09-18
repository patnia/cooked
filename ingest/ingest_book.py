"""Offline recipe-library ingestion.

Run against one or more cookbooks (PDF/EPUB, a folder of them, or a .zip of
them) to populate the inbuilt recipe library. Extracts FACTS ONLY -- dish
name, servings, ingredients, and an auto-classified diet/cuisine tag -- never
the book's own instructional prose. Written cooking steps are always
generated live, per user, per request (see app/llm.py generate_steps) --
nothing generated here is ever stored or served. See the legal note in the
build spec.

Usage:
    python -m ingest.ingest_book path/to/book.pdf
    python -m ingest.ingest_book path/to/folder_of_books/
    python -m ingest.ingest_book path/to/books.zip
"""

import argparse
import base64
import io
import sys
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.llm import EXTRACTION_MODEL, client

PDF_PAGES_PER_CHUNK = 40  # only used for the (expensive) image-based fallback
TEXT_CHARS_PER_CHUNK = 15000
MIN_CHARS_PER_PAGE = 100  # below this, a PDF is probably scanned/image-only with no text layer
SUPPORTED_SUFFIXES = {".pdf", ".epub"}

_BOOK_EXTRACT_TOOL = {
    "name": "record_recipes",
    "description": "Record every distinct recipe found in this excerpt, as facts only.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recipes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dish_name": {"type": "string"},
                        "serves": {"type": "integer"},
                        "diet": {
                            "type": "string",
                            "description": (
                                "Classify from the ingredient list. jain = no onion, "
                                "garlic, or root vegetables. vegan = no dairy/egg/meat/fish. "
                                "vegetarian = no meat/fish/egg but may include dairy."
                            ),
                            "enum": ["vegetarian", "non_vegetarian", "jain", "vegan"],
                        },
                        "cuisine": {
                            "type": "string",
                            "enum": ["indian", "western", "other"],
                        },
                        "ingredients": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "quantity": {"type": "number"},
                                    "unit": {"type": "string"},
                                    "category": {
                                        "type": "string",
                                        "enum": ["produce", "spices_pantry", "dairy", "grains", "other"],
                                    },
                                },
                                "required": ["name", "quantity", "unit", "category"],
                            },
                        },
                    },
                    "required": ["dish_name", "serves", "diet", "cuisine", "ingredients"],
                },
            }
        },
        "required": ["recipes"],
    },
}

_PROMPT = (
    "Extract every distinct recipe in this excerpt from a cookbook. For each "
    "one, record ONLY the dish name, number of servings, ingredient list "
    "(name, quantity, unit, category), and a diet/cuisine classification "
    "based on the ingredients -- as structured facts. Do NOT include, quote, "
    "or paraphrase the book's instructional text/method -- facts only. If "
    "this excerpt contains no complete recipes, return an empty list."
)


def _extract_recipes_from_text(text: str) -> list[dict]:
    message = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=4096,
        tools=[_BOOK_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_recipes"},
        messages=[{"role": "user", "content": f"{_PROMPT}\n\n---\n{text}"}],
    )
    return _recipes_from_message(message)


def _extract_recipes_from_pdf_image_bytes(pdf_bytes: bytes) -> list[dict]:
    message = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=4096,
        tools=[_BOOK_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_recipes"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.b64encode(pdf_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )
    return _recipes_from_message(message)


def _recipes_from_message(message) -> list[dict]:
    if message.stop_reason == "max_tokens":
        print("    warning: chunk response was truncated (max_tokens) -- some recipes in it may be lost")
    for block in message.content:
        if block.type == "tool_use" and block.name == "record_recipes":
            return block.input.get("recipes", [])
    return []


def _extract_pdf_text(path: Path) -> tuple[str, int]:
    """Extract the embedded text layer locally (free) -- no API call."""
    reader = PdfReader(str(path))
    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            pages_text.append("")
    return "\n\n".join(pages_text), len(reader.pages)


def _iter_pdf_image_chunks(path: Path):
    """Expensive fallback for scanned/image-only PDFs: send page images to Claude."""
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    for start in range(0, total_pages, PDF_PAGES_PER_CHUNK):
        writer = PdfWriter()
        for page in reader.pages[start : start + PDF_PAGES_PER_CHUNK]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        yield buf.getvalue()


def _iter_text_chunks(text: str):
    for start in range(0, len(text), TEXT_CHARS_PER_CHUNK):
        yield text[start : start + TEXT_CHARS_PER_CHUNK]


def _iter_epub_text(path: Path) -> str:
    book = epub.read_epub(str(path))
    full_text = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        full_text.append(soup.get_text(separator="\n", strip=True))
    return "\n\n".join(full_text)


def ingest_one(path: Path, style_tag: str | None, allow_image_fallback: bool = False) -> int:
    """Ingest a single book file. Returns the number of recipes saved.

    PDFs are read locally for their embedded text layer (free) and sent to
    Claude as plain text -- far cheaper than sending page images, and these
    are text-heavy recipe books where the photos don't matter for
    extraction. If a PDF has little/no text layer (scanned pages), it's
    skipped by default rather than silently falling back to the expensive
    image path; pass allow_image_fallback=True to opt into that for a
    specific book.
    """
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        text, page_count = _extract_pdf_text(path)
        avg_chars_per_page = len(text) / max(page_count, 1)
        if avg_chars_per_page < MIN_CHARS_PER_PAGE:
            if not allow_image_fallback:
                print(
                    f"  SKIPPED: only ~{avg_chars_per_page:.0f} chars/page extracted "
                    f"({page_count} pages) -- likely a scanned/image-only PDF with no "
                    f"text layer. Re-run with --allow-image-fallback to use the more "
                    f"expensive image-based extraction for this book."
                )
                return 0
            print(f"  Low text yield (~{avg_chars_per_page:.0f} chars/page) -- using image-based fallback (more expensive).")
            chunks = _iter_pdf_image_chunks(path)
            extract = _extract_recipes_from_pdf_image_bytes
        else:
            chunks = _iter_text_chunks(text)
            extract = _extract_recipes_from_text
    elif suffix == ".epub":
        chunks = _iter_text_chunks(_iter_epub_text(path))
        extract = _extract_recipes_from_text
    else:
        raise ValueError(f"Unsupported file type: {suffix} (expected .pdf or .epub)")

    total_saved = 0
    for i, chunk in enumerate(chunks, start=1):
        print(f"  chunk {i}: extracting...")
        recipes = extract(chunk)
        for recipe in recipes:
            db.insert_recipe(
                dish_name=recipe["dish_name"],
                serves=recipe.get("serves") or 4,
                ingredients=recipe["ingredients"],
                diet=recipe.get("diet"),
                cuisine=recipe.get("cuisine"),
                style_tag=style_tag,
                source_book=path.name,
            )
            total_saved += 1
            print(f"    saved: {recipe['dish_name']} ({recipe.get('diet')}, {recipe.get('cuisine')})")

    return total_saved


def _collect_book_files(input_path: Path) -> tuple[list[Path], Path | None]:
    """Return (list of book files to ingest, temp dir to clean up if a zip was extracted)."""
    if input_path.is_dir():
        files = sorted(
            p for p in input_path.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES
        )
        return files, None

    if input_path.suffix.lower() == ".zip":
        extract_dir = input_path.parent / f"_{input_path.stem}_extracted"
        with zipfile.ZipFile(input_path) as zf:
            zf.extractall(extract_dir)
        files = sorted(
            p for p in extract_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES
        )
        return files, extract_dir

    if input_path.suffix.lower() in SUPPORTED_SUFFIXES:
        return [input_path], None

    raise ValueError(f"Unsupported input: {input_path} (expected a .pdf, .epub, folder, or .zip)")


def main():
    parser = argparse.ArgumentParser(
        description="Ingest one or more cookbooks (PDF/EPUB, a folder, or a .zip) into the recipe library."
    )
    parser.add_argument("path", type=Path, help="Path to a book file, a folder of books, or a .zip of books")
    parser.add_argument(
        "--style",
        default=None,
        help="Optional free-text tag applied to every recipe ingested this run (diet/cuisine are auto-classified per recipe)",
    )
    parser.add_argument(
        "--allow-image-fallback",
        action="store_true",
        help="For PDFs with little/no text layer (scanned pages), fall back to the more expensive image-based extraction instead of skipping them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just report page counts and which PDFs would need the image fallback -- makes no API calls",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Path not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    db.init_db()

    try:
        book_files, temp_dir = _collect_book_files(args.path)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if not book_files:
        print("No .pdf or .epub files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(book_files)} book(s) to ingest.\n")

    if args.dry_run:
        total_pages = 0
        for book_path in book_files:
            if book_path.suffix.lower() == ".pdf":
                text, page_count = _extract_pdf_text(book_path)
                avg = len(text) / max(page_count, 1)
                flag = "OK (text)" if avg >= MIN_CHARS_PER_PAGE else "NEEDS --allow-image-fallback (scanned?)"
                print(f"  {book_path.name}: {page_count} pages, ~{avg:.0f} chars/page -- {flag}")
                total_pages += page_count
            else:
                print(f"  {book_path.name}: epub, text-based (cheap)")
        print(f"\nTotal pages across all PDFs: {total_pages}. No API calls made (--dry-run).")
        return

    results = []
    for book_path in book_files:
        print(f"--- {book_path.name} ---")
        try:
            count = ingest_one(book_path, args.style, allow_image_fallback=args.allow_image_fallback)
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append((book_path.name, 0, str(e)))
            continue
        print(f"  {count} recipe(s) saved.\n")
        results.append((book_path.name, count, None))

    print("=== Summary ===")
    total = 0
    for name, count, error in results:
        status = f"FAILED ({error})" if error else f"{count} recipes"
        print(f"  {name}: {status}")
        total += count
    print(f"\nTotal recipes saved this run: {total}")
    print(f"Total recipes in library: {db.count_recipes()}")

    if temp_dir is not None:
        print(f"\n(Extracted zip contents are at {temp_dir} -- safe to delete once you're happy with the results.)")


if __name__ == "__main__":
    main()
