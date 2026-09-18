from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import auth, db, links, llm, users_db
from app.normalization import normalize_all, scale_ingredients
from app.tags import extract_generic_query
from app.schemas import (
    ExtractedRecipe,
    ExtractRequest,
    GenerateRequest,
    GenerateResponse,
    Ingredient,
    LoginRequest,
    OnboardingPreferences,
    QuizRequest,
    ShoppingItem,
    SignupRequest,
    UserOut,
)
from app.tiers import TIER_COOKIE, Tier, require_tier

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="What's Cooking")


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    users_db.init_users_db()


# --- PWA shell (root scope so the service worker can control the whole origin) ---


@app.get("/")
def index():
    # Cache-bust app.js/style.css by file mtime -- a plain FileResponse here let
    # the browser's ordinary HTTP cache keep serving a stale <script src="...">
    # after an edit, even past a hard reload, because the reference URL itself
    # never changed. Regenerating it whenever the file's mtime changes forces a
    # real fetch instead of a cache hit, for us now and for real users later.
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js_version = int((STATIC_DIR / "app.js").stat().st_mtime)
    css_version = int((STATIC_DIR / "style.css").stat().st_mtime)
    html = html.replace('/static/app.js"', f'/static/app.js?v={js_version}"')
    html = html.replace('/static/style.css"', f'/static/style.css?v={css_version}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- API ---


def _extracted_from_hit(hit: dict, target_serves: int | None = None) -> ExtractedRecipe:
    ingredients = hit["ingredients"]
    serves = hit["serves"]
    if target_serves and target_serves != serves:
        ingredients = scale_ingredients(ingredients, from_serves=serves, to_serves=target_serves)
        serves = target_serves
    raw_ingredients = [Ingredient(**i) for i in ingredients]
    normalized = normalize_all(raw_ingredients)
    return ExtractedRecipe(dish_name=hit["dish_name"], serves=serves, ingredients=normalized)


@app.post("/api/extract", response_model=ExtractedRecipe)
def extract(
    req: ExtractRequest,
    tier: Tier = Depends(require_tier(Tier.PAID1)),
    user: dict | None = Depends(auth.get_current_user),
):
    generic = extract_generic_query(req.text)
    library_hit = None
    if generic:
        axis, value = generic
        if axis == "type_tag":
            # A bare category search ("curry") is exactly the case where it's
            # fair to lean on a signed-in user's stored preference to fill in
            # what they didn't specify -- a specific dish name always bypasses
            # this untouched. Never a hard filter: falls back to any tag match.
            effective_tag = value
            diet_bias = cuisine_bias = None
            if user:
                if value == "drink" and user["drink_preference"] in ("cocktail", "mocktail"):
                    effective_tag = user["drink_preference"]
                else:
                    if user["dietary_restriction"] != "none":
                        diet_bias = user["dietary_restriction"]
                    if user["preferred_cuisine"] != "none":
                        cuisine_bias = user["preferred_cuisine"]
            library_hit = db.lookup_recipe_by_tag(effective_tag, diet=diet_bias, cuisine=cuisine_bias)
            if not library_hit and effective_tag != value:
                library_hit = db.lookup_recipe_by_tag(value)
        elif axis == "diet":
            library_hit = db.lookup_recipe_by_diet(value)
        elif axis == "cuisine":
            library_hit = db.lookup_recipe_by_cuisine(value)
    if not library_hit:
        library_hit = db.lookup_recipe(req.text)
    if library_hit:
        return _extracted_from_hit(library_hit)

    extracted = llm.extract_recipe(req.text)
    raw_ingredients = [Ingredient(**i) for i in extracted["ingredients"]]
    normalized = normalize_all(raw_ingredients)
    return ExtractedRecipe(dish_name=extracted["dish_name"], serves=extracted["serves"], ingredients=normalized)


@app.post("/api/quiz", response_model=ExtractedRecipe)
def quiz(req: QuizRequest, tier: Tier = Depends(require_tier(Tier.PAID1))):
    if req.category == "random":
        hit = db.lookup_recipe_filtered()
    else:
        diet = req.diet if req.diet != "none" else None
        cuisine = req.cuisine if req.cuisine != "none" else None
        hit = db.lookup_recipe_filtered(tags=req.tags, diet=diet, cuisine=cuisine)

    if not hit:
        raise HTTPException(500, "No recipes in the library yet.")
    return _extracted_from_hit(hit, target_serves=req.serves)


@app.post("/api/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, tier: Tier = Depends(require_tier(Tier.PAID1))):
    items = [
        ShoppingItem(
            name=ing.name,
            needed_display=ing.needed_display,
            buy_display=ing.buy_display,
            blinkit_url=links.blinkit_url(ing.name),
            zepto_url=links.zepto_url(ing.name),
            amazon_url=links.amazon_url(ing.name),
        )
        for ing in req.to_buy
    ]

    steps = None
    youtube_url = None

    if req.preferences.steps_mode in ("written", "both"):
        steps = llm.generate_steps(
            req.dish_name,
            req.serves,
            [i.model_dump() for i in req.ingredients],
            req.preferences,
        )

    if req.preferences.steps_mode in ("video", "both"):
        youtube_url = links.youtube_recipe_url(
            req.dish_name, hindi=req.preferences.language == "hindi"
        )

    return GenerateResponse(
        dish_name=req.dish_name, items=items, steps=steps, youtube_url=youtube_url
    )


# --- accounts ---


def _user_out(user_row: dict) -> UserOut:
    return UserOut(
        email=user_row["email"],
        preferences=OnboardingPreferences(
            name=user_row["name"],
            age=user_row["age"],
            content_preference=user_row["content_preference"],
            preferred_cuisine=user_row["preferred_cuisine"],
            dietary_restriction=user_row["dietary_restriction"],
            drink_preference=user_row["drink_preference"],
            skill_level=user_row["skill_level"],
            appliance=user_row["appliance"].split(",") if user_row["appliance"] else [],
            language=user_row["language"],
        ),
    )


@app.post("/api/signup", response_model=UserOut)
def signup(req: SignupRequest, response: Response):
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Enter a valid email address.")
    if users_db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists.")

    password_hash = auth.hash_password(req.password)
    user_id = users_db.create_user(email, password_hash, req.preferences.model_dump())
    token = auth.start_session(user_id)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
    return _user_out(users_db.get_user_by_id(user_id))


@app.post("/api/login", response_model=UserOut)
def login(req: LoginRequest, response: Response):
    email = req.email.strip().lower()
    user = users_db.get_user_by_email(email)
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password.")

    token = auth.start_session(user["id"])
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
    return _user_out(user)


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        users_db.delete_session(token)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me", response_model=Optional[UserOut])
def me(user: dict | None = Depends(auth.get_current_user)):
    return _user_out(user) if user else None


# --- dev-only tier switch (stand-in for real auth/payments, see app/tiers.py) ---


@app.get("/dev/set-tier")
def set_tier(tier: str):
    tier_key = tier.upper()
    if tier_key not in Tier.__members__:
        raise HTTPException(400, f"Unknown tier '{tier}'. Choose from: {', '.join(Tier.__members__)}")
    response = RedirectResponse(url="/")
    response.set_cookie(TIER_COOKIE, tier_key, max_age=60 * 60 * 24 * 365)
    return response


@app.get("/dev/set-user-tier")
def set_user_tier(email: str, tier: str):
    """Grant a specific account a tier directly, independent of whichever
    browser/device is asking -- the account-level equivalent of /dev/set-tier.
    """
    tier_key = tier.upper()
    if tier_key not in Tier.__members__:
        raise HTTPException(400, f"Unknown tier '{tier}'. Choose from: {', '.join(Tier.__members__)}")
    user = users_db.get_user_by_email(email.strip().lower())
    if not user:
        raise HTTPException(404, f"No account found for {email}.")
    users_db.set_user_tier(user["id"], tier_key)
    return {"ok": True, "email": user["email"], "tier": tier_key}


@app.get("/dev/reset")
def dev_reset(request: Request):
    """Convenience for testing the fresh-visitor flow: logs out and drops
    back to FREE tier in one link, instead of clicking logout then re-visiting
    /dev/set-tier?tier=free by hand.
    """
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        users_db.delete_session(token)
    response = RedirectResponse(url="/")
    response.delete_cookie(auth.SESSION_COOKIE)
    response.delete_cookie(TIER_COOKIE)
    return response
