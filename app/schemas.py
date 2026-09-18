from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Category = Literal["produce", "spices_pantry", "dairy", "grains", "other"]


class Ingredient(BaseModel):
    name: str
    quantity: float
    unit: str
    category: Category


class NormalizedIngredient(Ingredient):
    needed_display: str  # e.g. "1 tbsp", "300 g" -- what the recipe actually calls for
    buy_display: str  # e.g. "1 x 100g packet", "500g (loose)" -- what to purchase


class ExtractedRecipe(BaseModel):
    dish_name: str
    serves: int
    ingredients: list[NormalizedIngredient]


class ExtractRequest(BaseModel):
    text: str


class QuizRequest(BaseModel):
    category: Literal["food", "drink", "random"]
    cuisine: Literal["indian", "western", "none"] = "none"
    diet: Literal["vegetarian", "vegan", "jain", "non_vegetarian", "none"] = "none"
    tags: list[str] = []  # resolved tag(s) from the mood/protein/drink-type answers
    serves: int = Field(ge=1, le=50)


class Preferences(BaseModel):
    steps_mode: Literal["written", "video", "both"]
    skill_level: Literal["never_made_it", "done_it_before", "comfortable"]
    appliance: Literal["stovetop", "pressure_cooker", "oven", "air_fryer"]
    language: Literal["english", "hindi"]


class GenerateRequest(BaseModel):
    dish_name: str
    serves: int
    ingredients: list[NormalizedIngredient]  # full recipe list -- steps are written against this
    to_buy: list[NormalizedIngredient]  # checked subset -- only these appear on the shopping list
    preferences: Preferences


class ShoppingItem(BaseModel):
    name: str
    needed_display: str
    buy_display: str
    blinkit_url: str
    zepto_url: str
    amazon_url: str


class Step(BaseModel):
    text: str
    timer_seconds: Optional[int] = None  # unattended-wait duration, if any


class GenerateResponse(BaseModel):
    dish_name: str
    items: list[ShoppingItem]
    steps: Optional[list[Step]] = None
    youtube_url: Optional[str] = None


MIN_DRINKING_AGE = 18


class OnboardingPreferences(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=1, le=120)
    content_preference: Literal["food", "drink", "both"]
    preferred_cuisine: Literal["indian", "western", "none"] = "none"
    dietary_restriction: Literal["vegetarian", "vegan", "jain", "non_vegetarian", "none"] = "none"
    drink_preference: Literal["cocktail", "mocktail", "both", "none"] = "none"
    skill_level: Literal["never_made_it", "done_it_before", "comfortable"]
    appliance: list[Literal["stovetop", "pressure_cooker", "oven", "air_fryer"]] = Field(min_length=1)
    language: Literal["english", "hindi"]

    @model_validator(mode="after")
    def _no_cocktails_under_age(self) -> "OnboardingPreferences":
        if self.age < MIN_DRINKING_AGE and self.drink_preference in ("cocktail", "both"):
            raise ValueError(f"Cocktails require age {MIN_DRINKING_AGE}+.")
        return self


class SignupRequest(BaseModel):
    email: str
    password: str
    preferences: OnboardingPreferences


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    email: str
    preferences: OnboardingPreferences
