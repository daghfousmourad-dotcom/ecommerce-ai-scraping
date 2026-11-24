"""
Calculateur et échelle de scores de confiance pour la normalisation d'E-commerce AI V2.
"""

from __future__ import annotations

from typing import Dict

CONFIDENCE_LEVELS: Dict[str, float] = {
    "ean_exact": 1.00,
    "technical_code": 0.99,
    "exact_alias": 0.97,
    "alias_recognized": 0.92,
    "lexical_clean": 0.85,
    "fuzzy_match": 0.80,
    "ai_inferred": 0.65,
    "unverified_fallback": 0.50,
}


def compute_normalization_confidence(
    resolution_method: str,
    has_tech_code: bool = False,
    brand_status: str = "KNOWN",
) -> float:
    """Calcule le score de confiance combiné (0.0 à 1.0)."""
    base = CONFIDENCE_LEVELS.get(resolution_method, 0.70)
    if has_tech_code:
        base = max(base, 0.98)
    if brand_status == "KNOWN":
        base = min(1.0, base + 0.02)
    elif brand_status == "NOT_SMARTPHONE":
        base = 0.0
    return round(base, 2)
