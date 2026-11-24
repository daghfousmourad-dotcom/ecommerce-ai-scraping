"""
Package model_normalizer pour E-commerce AI V2.
Fournit le pipeline contextuel en 3 niveaux :
1. Parsing / extraction des tokens et segments
2. Détection contextuelle des identifiants techniques
3. Résolution vers un modèle canonique et slug officiel
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .tokenizer import parse_model_string, ParsedModelTokens
from .technical_code_detector import extract_technical_codes
from .attribute_detector import extract_attributes
from .canonical_resolver import CanonicalResolver, ResolvedDevice
from .brand_verifier import BrandVerifier, BrandVerificationResult
from .confidence import compute_normalization_confidence


def normalize_device_entry(
    raw_model: str,
    raw_brand: str = "",
    item_title: str = "",
) -> Dict[str, Any]:
    """
    Pipeline complet de normalisation contextuelle d'un appareil :
    Préserve la donnée brute, extrait les identifiants techniques et résout le modèle canonique.
    """
    orig_model = str(raw_model or "").strip()
    orig_brand = str(raw_brand or "").strip()

    # 1. Parsing lexical et analyse des parenthèses
    parsed: ParsedModelTokens = parse_model_string(orig_model, brand=orig_brand)

    # 2. Détection des codes techniques
    detected_codes, cleaned_from_codes = extract_technical_codes(parsed.main_name, brand=orig_brand)
    all_tech_codes = sorted(list(set(parsed.identified_tech_codes + detected_codes)))

    # 3. Extraction des attributs (stockage, couleur, RAM)
    specs = extract_attributes(f"{orig_model} {item_title}")
    # Fusionner avec les attributs trouvés dans les parenthèses
    if parsed.identified_attributes.get("storage_raw") and not specs.get("storage_gb"):
        st_match = parsed.identified_attributes["storage_raw"]
        try:
            specs["storage_gb"] = int("".join(filter(str.isdigit, st_match)))
        except Exception:
            pass

    # 4. Résolution vers le référentiel canonique
    resolver = CanonicalResolver.get_instance()
    resolved: ResolvedDevice = resolver.resolve(
        raw_model=orig_model,
        brand=orig_brand,
        detected_tech_codes=all_tech_codes,
        candidate_model=cleaned_from_codes or parsed.main_name,
    )

    conf = compute_normalization_confidence(
        resolution_method=resolved.resolution_method,
        has_tech_code=len(all_tech_codes) > 0,
    )

    return {
        "raw_model": orig_model,
        "raw_brand": orig_brand,
        "model_candidate": cleaned_from_codes or parsed.main_name,
        "model": resolved.canonical_model,
        "brand": resolved.canonical_brand,
        "canonical_slug": resolved.canonical_slug,
        "canonical_resolution": resolved.canonical_resolution,
        "slug_type": resolved.slug_type,
        "resolved_canonical_slug": resolved.resolved_canonical_slug,
        "provisional_slug": resolved.provisional_slug,
        "technical_codes": all_tech_codes,
        "storage_gb": specs.get("storage_gb"),
        "ram_gb": specs.get("ram_gb"),
        "color": specs.get("color"),
        "normalization_confidence": conf,
        "resolution_method": resolved.resolution_method,
        "resolution_strength": resolved.resolution_strength,
        "model_status": resolved.model_status,
        "status": resolved.status,
        "keep_record": resolved.keep_record,
    }


__all__ = [
    "parse_model_string",
    "extract_technical_codes",
    "extract_attributes",
    "CanonicalResolver",
    "ResolvedDevice",
    "BrandVerifier",
    "BrandVerificationResult",
    "normalize_device_entry",
    "compute_normalization_confidence",
]
