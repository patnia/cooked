"""Save manually-extracted recipes straight into the recipe library -- zero API calls.

Used for the manual ingestion workflow: after Claude reads a book's text file
and extracts recipes itself (as part of the coding session), the recipes are
written to a JSON file matching the schema below and saved with this script.

JSON schema (a list of recipe objects):
[
  {
    "dish_name": "Simple Dal",
    "serves": 4,
    "diet": "vegetarian",       // vegetarian | non_vegetarian | jain | vegan
    "cuisine": "indian",        // indian | western | other
    "ingredients": [
      {"name": "toor dal", "quantity": 1, "unit": "cup", "category": "grains"}
    ]
  }
]

Usage:
    python -m ingest.save_recipes recipes.json --source-book "Some Book.pdf"
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db

REQUIRED_INGREDIENT_KEYS = {"name", "quantity", "unit", "category"}
VALID_CATEGORIES = {"produce", "spices_pantry", "dairy", "grains", "other"}
VALID_DIETS = {"vegetarian", "non_vegetarian", "jain", "vegan", None}
VALID_CUISINES = {"indian", "western", "other", None}


def validate(recipe: dict, index: int) -> list[str]:
    errors = []
    if not recipe.get("dish_name"):
        errors.append(f"recipe {index}: missing dish_name")
    if not isinstance(recipe.get("serves"), int):
        errors.append(f"recipe {index} ({recipe.get('dish_name')}): serves must be an integer")
    if recipe.get("diet") not in VALID_DIETS:
        errors.append(f"recipe {index} ({recipe.get('dish_name')}): invalid diet {recipe.get('diet')!r}")
    if recipe.get("cuisine") not in VALID_CUISINES:
        errors.append(f"recipe {index} ({recipe.get('dish_name')}): invalid cuisine {recipe.get('cuisine')!r}")
    ingredients = recipe.get("ingredients")
    if not ingredients:
        errors.append(f"recipe {index} ({recipe.get('dish_name')}): no ingredients")
    else:
        for j, ing in enumerate(ingredients):
            missing = REQUIRED_INGREDIENT_KEYS - ing.keys()
            if missing:
                errors.append(f"recipe {index} ({recipe.get('dish_name')}) ingredient {j}: missing {missing}")
            if ing.get("category") not in VALID_CATEGORIES:
                errors.append(f"recipe {index} ({recipe.get('dish_name')}) ingredient {j}: invalid category {ing.get('category')!r}")
    return errors


def main():
    parser = argparse.ArgumentParser(description="Save manually-extracted recipes into the library (no API calls).")
    parser.add_argument("json_file", type=Path, help="JSON file: a list of recipe objects")
    parser.add_argument("--source-book", required=True, help="Filename of the source book, for provenance")
    parser.add_argument("--style", default=None, help="Optional free-text tag applied to all recipes in this file")
    args = parser.parse_args()

    recipes = json.loads(args.json_file.read_text(encoding="utf-8"))
    if not isinstance(recipes, list):
        print("JSON file must contain a list of recipe objects.", file=sys.stderr)
        sys.exit(1)

    all_errors = []
    for i, recipe in enumerate(recipes):
        all_errors.extend(validate(recipe, i))

    if all_errors:
        print(f"{len(all_errors)} validation error(s), nothing saved:", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    db.init_db()
    for recipe in recipes:
        db.insert_recipe(
            dish_name=recipe["dish_name"],
            serves=recipe["serves"],
            ingredients=recipe["ingredients"],
            diet=recipe.get("diet"),
            cuisine=recipe.get("cuisine"),
            style_tag=args.style,
            source_book=args.source_book,
        )
        print(f"  saved: {recipe['dish_name']} ({recipe.get('diet')}, {recipe.get('cuisine')}, {len(recipe['ingredients'])} ingredients)")

    print(f"\n{len(recipes)} recipe(s) saved from {args.source_book}.")
    print(f"Total recipes in library: {db.count_recipes()}")


if __name__ == "__main__":
    main()
