"""Tier-gating scaffold.

No real accounts or payment provider yet. Current tier is read from a cookie,
defaulting to FREE. A /dev/set-tier route flips it for local testing. Swapping
in real auth/payments later means replacing how the cookie gets set -- the
gating logic (require_tier) doesn't change.
"""

from enum import IntEnum

from fastapi import HTTPException, Request

TIER_COOKIE = "wc_tier"


class Tier(IntEnum):
    FREE = 0
    PAID1 = 1
    PAID2 = 2
    PAID3 = 3


def get_current_tier(request: Request) -> Tier:
    raw = request.cookies.get(TIER_COOKIE, "free").upper()
    try:
        return Tier[raw]
    except KeyError:
        return Tier.FREE


def require_tier(minimum: Tier):
    def dependency(request: Request) -> Tier:
        tier = get_current_tier(request)
        if tier < minimum:
            raise HTTPException(
                status_code=402,
                detail=f"This feature requires the {minimum.name} tier or higher (you're on {tier.name}).",
            )
        return tier

    return dependency
