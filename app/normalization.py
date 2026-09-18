"""Quantity normalization: raw LLM-extracted quantities -> real Indian retail pack sizes.

Seed data only (per build spec) - extend PACK_SIZES_GRAMS as real usage surfaces gaps.
"""

import math

from app.schemas import Ingredient, NormalizedIngredient

# Unit -> grams (or ml, treated as equivalent for this purpose)
UNIT_TO_GRAMS = {
    "g": 1, "gram": 1, "grams": 1,
    "kg": 1000, "kilogram": 1000, "kilograms": 1000,
    "ml": 1, "milliliter": 1, "millilitre": 1, "milliliters": 1,
    "l": 1000, "liter": 1000, "litre": 1000, "liters": 1000,
    "tsp": 5, "teaspoon": 5, "teaspoons": 5,
    "tbsp": 15, "tablespoon": 15, "tablespoons": 15,
    "cup": 240, "cups": 240,
    "pinch": 0.5, "pinches": 0.5,
}

# Ingredient (normalized, lowercase) -> available pack sizes in grams/ml, ascending.
PACK_SIZES_GRAMS: dict[str, list[int]] = {
    "turmeric powder": [100],
    "turmeric": [100],
    "haldi": [100],
    "red chilli powder": [100],
    "chilli powder": [100],
    "chili powder": [100],
    "garam masala": [50],
    "cumin seeds": [100],
    "cumin": [100],
    "jeera": [100],
    "coriander powder": [100],
    "ghee": [200, 500],
    "cooking oil": [1000],
    "oil": [1000],
    "basmati rice": [1000],
    "rice": [1000],
    "toor dal": [500, 1000],
    "moong dal": [500, 1000],
    "wheat flour": [1000, 5000],
    "atta": [1000, 5000],
    "ginger garlic paste": [200],
    "ginger-garlic paste": [200],
    "yogurt": [400],
    "curd": [400],
    "paneer": [200],
}


def _find_pack_sizes(name: str) -> list[int] | None:
    key = name.strip().lower()
    if key in PACK_SIZES_GRAMS:
        return PACK_SIZES_GRAMS[key]
    # Only match when the ingredient name contains the full table phrase
    # (e.g. "besan flour" contains "besan") -- never the reverse, or a short
    # generic word like "ginger" would get swallowed by a longer, more
    # specific entry like "ginger garlic paste".
    for table_key, sizes in PACK_SIZES_GRAMS.items():
        if table_key in key:
            return sizes
    return None


def normalize_ingredient(ingredient: Ingredient) -> NormalizedIngredient:
    pack_sizes = _find_pack_sizes(ingredient.name)
    grams_per_unit = UNIT_TO_GRAMS.get(ingredient.unit.strip().lower())
    needed_display = f"{ingredient.quantity:g} {ingredient.unit}"

    if pack_sizes and grams_per_unit:
        total_grams = ingredient.quantity * grams_per_unit
        fitting = [p for p in pack_sizes if p >= total_grams]
        if fitting:
            chosen = min(fitting)
            buy_display = f"1 x {chosen}g packet"
        else:
            largest = max(pack_sizes)
            count = math.ceil(total_grams / largest)
            buy_display = f"{count} x {largest}g packet" + ("s" if count > 1 else "")
    else:
        qty = ingredient.quantity
        qty_str = f"{qty:g}"
        buy_display = f"{qty_str} {ingredient.unit} (loose / as needed)"

    return NormalizedIngredient(
        name=ingredient.name,
        quantity=ingredient.quantity,
        unit=ingredient.unit,
        category=ingredient.category,
        needed_display=needed_display,
        buy_display=buy_display,
    )


def normalize_all(ingredients: list[Ingredient]) -> list[NormalizedIngredient]:
    return [normalize_ingredient(i) for i in ingredients]


def scale_ingredients(ingredients: list[dict], from_serves: int, to_serves: int) -> list[dict]:
    """Scale raw ingredient quantities (pre-normalization dicts, as stored in
    the library) from one serving count to another. Pack-size rounding in
    normalize_ingredient() runs afterward, against the scaled amount, so a
    bigger batch correctly bumps a pantry item up to more packets on its own.
    """
    if from_serves <= 0 or to_serves == from_serves:
        return ingredients
    ratio = to_serves / from_serves
    return [{**ing, "quantity": round(ing["quantity"] * ratio, 2)} for ing in ingredients]
