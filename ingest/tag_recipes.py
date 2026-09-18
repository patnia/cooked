"""One-off backfill: assign dish-type tags to every recipe in the library.

Zero API calls -- pure keyword matching against dish names, same style as the
diet/cuisine classifiers used during ingestion. Powers generic-input matching
in /api/extract (e.g. typing "pasta" alone picks a random recipe tagged
"pasta" instead of matching nothing or an arbitrary row).

Usage:
    python -m ingest.tag_recipes
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.tags import classify_type_tags


def main():
    db.init_db()
    recipes = db.iter_all_recipes()
    tagged = 0
    for r in recipes:
        tags = classify_type_tags(r["dish_name"])
        # always write, even when empty, so a rerun clears stale tags from a
        # previous (e.g. buggy) classification -- not just add new ones
        db.set_type_tags(r["id"], tags)
        if tags:
            tagged += 1

    print(f"Tagged {tagged} of {len(recipes)} recipes.")

    # quick sample so the run is spot-checkable
    for sample_tag in ("pasta", "curry", "rice", "dessert", "drink", "cocktail",
                        "mocktail", "pork", "beef", "lamb"):
        hit = db.lookup_recipe_by_tag(sample_tag)
        print(f"  {sample_tag}: {hit['dish_name'] if hit else '(no match)'}")

    for diet in ("vegan", "vegetarian", "jain", "non_vegetarian"):
        hit = db.lookup_recipe_by_diet(diet)
        print(f"  diet={diet}: {hit['dish_name'] if hit else '(no match)'}")

    for cuisine in ("indian", "western"):
        hit = db.lookup_recipe_by_cuisine(cuisine)
        print(f"  cuisine={cuisine}: {hit['dish_name'] if hit else '(no match)'}")


if __name__ == "__main__":
    main()
