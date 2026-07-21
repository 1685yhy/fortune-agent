"""Pricing API — transparent pricing information."""
from fastapi import APIRouter
from src.pricing import FREE_FEATURES, PAID_FEATURES, NEW_USER_FREE_READINGS, TRUST_STATEMENT

router = APIRouter(prefix="/api", tags=["pricing"])


@router.get("/pricing")
async def get_pricing():
    """Return complete pricing configuration as JSON."""
    return {
        "free_features": FREE_FEATURES,
        "paid_features": {k: v for k, v in PAID_FEATURES.items()},
        "new_user_free_readings": NEW_USER_FREE_READINGS,
        "trust_statement": TRUST_STATEMENT,
    }


@router.get("/pricing/statement")
async def get_trust_statement():
    """Return the trust/anti-scam statement."""
    return {
        "trust_statement": TRUST_STATEMENT,
    }
