"""Tier-gating scaffold.

No real payment provider yet. A signed-in user's tier lives on their account
(users.tier, see users_db.py) so it survives across devices/cookie clears --
the cookie is now only a fallback for the (now rare) case of a tier check
with nobody logged in. A /dev/set-user-tier route grants an account a tier
for local testing. Swapping in real payments later means replacing how
users.tier gets set -- the gating logic (require_tier) doesn't change.
"""

from enum import IntEnum

from fastapi import Depends, HTTPException, Request

from app import auth

TIER_COOKIE = "wc_tier"


class Tier(IntEnum):
    FREE = 0
    PAID1 = 1
    PAID2 = 2
    PAID3 = 3


def get_current_tier(request: Request, user: dict | None) -> Tier:
    if user and user.get("tier"):
        try:
            return Tier[user["tier"]]
        except KeyError:
            pass

    raw = request.cookies.get(TIER_COOKIE, "free").upper()
    try:
        return Tier[raw]
    except KeyError:
        return Tier.FREE


def require_tier(minimum: Tier):
    def dependency(request: Request, user: dict | None = Depends(auth.get_current_user)) -> Tier:
        tier = get_current_tier(request, user)
        if tier < minimum:
            raise HTTPException(
                status_code=402,
                detail=f"This feature requires the {minimum.name} tier or higher (you're on {tier.name}).",
            )
        return tier

    return dependency
