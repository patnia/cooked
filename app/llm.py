"""Two LLM calls: Haiku for structured extraction, Sonnet for step-writing.

Both use forced tool-use so responses parse directly into predictable JSON,
rather than relying on the model to produce well-formed free text.
"""

import os

from anthropic import Anthropic
from dotenv import load_dotenv

from app.schemas import Preferences

load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

EXTRACTION_MODEL = "claude-haiku-4-5-20251001"
STEPS_MODEL = "claude-sonnet-5"

_EXTRACT_TOOL = {
    "name": "record_recipe",
    "description": "Record the structured recipe extracted from the input.",
    "input_schema": {
        "type": "object",
        "properties": {
            "dish_name": {"type": "string"},
            "serves": {"type": "integer"},
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
        "required": ["dish_name", "serves", "ingredients"],
    },
}

_STEPS_TOOL = {
    "name": "write_steps",
    "description": "Record the freshly written cooking steps.",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "timer_seconds": {
                            "type": ["integer", "null"],
                            "description": (
                                "If this step involves an unattended wait with a stated duration "
                                "(simmering, baking, resting, marinating, etc), the duration in "
                                "seconds so the app can set a timer. Use the midpoint of a stated "
                                "range (e.g. '12-15 minutes' -> 810). Null for steps with no "
                                "clock-driven wait (chopping, stirring briefly, plating, ...)."
                            ),
                        },
                    },
                    "required": ["text", "timer_seconds"],
                },
            },
        },
        "required": ["steps"],
    },
}


def _tool_call(message, tool_name: str) -> dict:
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Response was cut off before the {tool_name} call finished (hit max_tokens) -- retry with a shorter input or a higher token limit"
        )
    for block in message.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise RuntimeError(f"Model did not call the {tool_name} tool")


def extract_recipe(text: str) -> dict:
    message = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=2048,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_recipe"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract a structured recipe from this request. If the number "
                    "of servings isn't specified, default to a sensible number for "
                    "the dish. Break every ingredient the recipe actually needs "
                    "(including base spices/oil/salt if implied by the dish) into "
                    "one row each, and group each into one of: produce, "
                    "spices_pantry, dairy, grains, other.\n\n"
                    f"Request: {text}"
                ),
            }
        ],
    )
    return _tool_call(message, "record_recipe")


def generate_steps(dish_name: str, serves: int, ingredients: list[dict], preferences: Preferences) -> list[dict]:
    ingredient_lines = "\n".join(
        f"- {i['name']}: {i['quantity']} {i['unit']}" for i in ingredients
    )
    skill_copy = {
        "never_made_it": "has never made this dish before -- explain any technique, don't assume familiarity",
        "done_it_before": "has made this before -- keep the explanations brief, skip over-explaining basics",
        "comfortable": "is comfortable cooking this -- be terse about technique, just the sequence",
    }[preferences.skill_level]

    language_instruction = (
        "Write the steps in Hindi." if preferences.language == "hindi" else "Write the steps in English."
    )

    message = client.messages.create(
        model=STEPS_MODEL,
        max_tokens=3072,
        tools=[_STEPS_TOOL],
        tool_choice={"type": "tool", "name": "write_steps"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Write fresh, imperative cooking steps for {dish_name} "
                    f"(serves {serves}), using only these ingredients:\n{ingredient_lines}\n\n"
                    f"The cook {skill_copy}. They're using a {preferences.appliance.replace('_', ' ')}. "
                    f"{language_instruction}\n\n"
                    "Be precise, not vague. Every step that involves heat, time, or measurement "
                    "must state the exact number -- never write 'cook until done', 'a bit of', "
                    "'medium heat', or 'a few minutes' on their own without a concrete figure attached. "
                    "Specifically:\n"
                    "- Heat: give both the stove dial setting (low/medium/medium-high/high) or oven "
                    "temperature in °C, AND, where it matters for the result, the approximate °C of the "
                    "pan/oil/oven itself (e.g. 'medium-high heat, oil shimmering around 180°C', "
                    "'preheat the oven to 220°C').\n"
                    "- Time: give a specific duration or tight range in minutes/seconds for every "
                    "cooking action (e.g. 'simmer for 12-15 minutes', 'rest for 5 minutes'), not just "
                    "a doneness description alone.\n"
                    "- Doneness cues: pair the time with a concrete visual/textural/temperature cue "
                    "(e.g. 'until the onions are golden brown and fragrant, about 6-8 minutes', "
                    "'until the internal temperature reaches 74°C').\n"
                    "- Quantities: when a step uses an ingredient, reference the exact amount from the "
                    "ingredient list above (e.g. '1.5 tsp cumin seeds'), not a vague amount -- unless "
                    "it's genuinely a to-taste seasoning step at the end.\n\n"
                    "This precision applies at every skill level -- the skill level only changes how much "
                    "technique/explanation surrounds the numbers, never whether the numbers are there.\n\n"
                    "For each step, also set timer_seconds when the step is an unattended wait with a "
                    "stated duration (simmering, baking, resting, marinating) so the app can start a "
                    "real countdown timer for it -- leave it null for steps that don't involve waiting "
                    "on the clock (chopping, stirring, plating).\n\n"
                    "Write these steps fresh in your own words -- do not reproduce or closely "
                    "paraphrase any specific source text, just describe how to cook the dish "
                    "given the ingredient list."
                ),
            }
        ],
    )
    result = _tool_call(message, "write_steps")
    return result["steps"]
