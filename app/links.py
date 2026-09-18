from urllib.parse import quote


def blinkit_url(item: str) -> str:
    return f"https://blinkit.com/s/?q={quote(item)}"


def zepto_url(item: str) -> str:
    return f"https://www.zeptonow.com/search?query={quote(item)}"


def amazon_url(item: str) -> str:
    return f"https://www.amazon.in/s?k={quote(item)}"


def youtube_recipe_url(dish_name: str, hindi: bool = False) -> str:
    query = f"{dish_name} recipe easy"
    if hindi:
        query += " hindi"
    return f"https://www.youtube.com/results?search_query={quote(query)}"
