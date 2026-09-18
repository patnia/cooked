"""Fixed, closed vocabulary for generic-input matching.

Two separate axes:
- type_tags: dish-type/protein/drink-subtype, keyword-matched against the dish
  name (same as before). Backfilled offline by ingest/tag_recipes.py and
  stored in recipes.type_tags.
- diet / cuisine: single-value-per-recipe classifications made by the LLM at
  ingestion time (recipes.diet, recipes.cuisine) -- not keyword-matched here,
  just exposed as generic-input vocabulary so "vegan" or "indian" typed alone
  can look recipes up directly by those columns.

Shared by the offline backfill script (ingest/tag_recipes.py) and the live
/api/extract generic-input matching, so both use the same definitions.
"""

import re

TAG_KEYWORDS: dict[str, list[str]] = {
    "pasta": ["pasta", "spaghetti", "penne", "linguine", "fettuccine", "macaroni", "lasagna",
              "ravioli", "tortellini", "bucatini", "ziti", "orzo", "noodle"],
    "curry": ["curry", "masala", "korma", "vindaloo", "kurma", "makhani"],
    "rice": ["rice", "pulao", "pullao", "pullav", "biryani", "khichdi", "risotto", "paella"],
    "bread": ["bread", "chapati", "phulka", "paratha", "naan", "roti", "loaf", "bun", "biscuit", "scone"],
    "dal": ["dal", "daal", "lentil", "sambhar", "sambar", "rajma", "chole", "chana"],
    "soup": ["soup", "broth", "bisque", "chowder"],
    "salad": ["salad", "slaw"],
    "dessert": ["ice cream", "pudding", "halva", "kheer", "burfi", "gulab jamun", "rasgoola",
                "tart", "cheesecake", "mousse", "trifle", "custard", "sorbet"],
    "cake": ["cake", "torte", "sponge", "cupcake", "muffin"],
    "snack": ["pakora", "fritter", "bonda", "vada", "samosa", "chaat", "bhel", "cutlet",
              "nachos", "tortilla chips"],
    "fritter": ["pakora", "fritter", "bonda", "vada", "tempura"],
    "drink": ["cocktail", "mocktail", "smoothie", "juice", "shake", "lassi", "tea", "coffee",
              "punch", "lemonade", "martini", "margarita", "mojito", "sangria"],
    "cocktail": ["cocktail", "martini", "margarita", "mojito", "sangria", "daiquiri", "spritz"],
    "mocktail": ["mocktail", "virgin mojito", "shirley temple"],
    "smoothie": ["smoothie"],
    "juice": ["juice"],
    "tea": ["tea", "chai"],
    "coffee": ["coffee"],
    "chicken": ["chicken"],
    "seafood": ["fish", "shrimp", "prawn", "salmon", "tuna", "crab", "lobster", "cod", "clam"],
    "pork": ["pork", "bacon", "ham", "prosciutto", "pancetta", "chorizo"],
    "beef": ["beef", "steak", "brisket", "ribeye"],
    "lamb": ["lamb", "mutton"],
}

TAG_VOCAB = set(TAG_KEYWORDS.keys())

# recipes.diet values -> generic-input phrases that should match them
DIET_PHRASES: dict[str, str] = {
    "vegan": "vegan",
    "vegetarian": "vegetarian",
    "veg": "vegetarian",
    "jain": "jain",
    "non vegetarian": "non_vegetarian",
    "nonvegetarian": "non_vegetarian",
    "non veg": "non_vegetarian",
    "nonveg": "non_vegetarian",
}

# recipes.cuisine values -> generic-input phrases that should match them
CUISINE_PHRASES: dict[str, str] = {
    "indian": "indian",
    "western": "western",
}

# single lookup table: normalized phrase -> (axis, value)
_GENERIC_LOOKUP: dict[str, tuple[str, str]] = {
    **{tag: ("type_tag", tag) for tag in TAG_KEYWORDS},
    **{phrase: ("diet", value) for phrase, value in DIET_PHRASES.items()},
    **{phrase: ("cuisine", value) for phrase, value in CUISINE_PHRASES.items()},
}

_FOR_N_RE = re.compile(r"\s+for\s+\d+.*$", re.I)
_HYPHEN_RE = re.compile(r"[-_]+")

# Whole-word matching, not substring -- a plain "k in name" check false-positives
# on short keywords buried inside unrelated words (e.g. "ham" inside "Bahama").
# Compound dish names (cupcake, cheesecake, ...) are handled by listing them as
# their own keywords above, not by relying on substring matches.
_TAG_PATTERNS: dict[str, list[re.Pattern]] = {
    tag: [re.compile(r"\b" + re.escape(kw) + r"\b", re.I) for kw in keywords]
    for tag, keywords in TAG_KEYWORDS.items()
}


def classify_type_tags(dish_name: str) -> list[str]:
    return [tag for tag, patterns in _TAG_PATTERNS.items() if any(p.search(dish_name) for p in patterns)]


def extract_generic_query(text: str) -> tuple[str, str] | None:
    """If the input is a bare category/diet/cuisine word (optionally with
    'for N ...' trailing), return (axis, value) where axis is one of
    "type_tag", "diet", "cuisine". Otherwise None -- leaves specific
    dish-name requests like 'chicken tikka masala for 4' untouched.
    """
    stripped = _FOR_N_RE.sub("", text).strip().lower()
    if not stripped:
        return None
    normalized = _HYPHEN_RE.sub(" ", stripped)
    if normalized in _GENERIC_LOOKUP:
        return _GENERIC_LOOKUP[normalized]
    if normalized.endswith("s") and normalized[:-1] in _GENERIC_LOOKUP:
        return _GENERIC_LOOKUP[normalized[:-1]]
    return None
