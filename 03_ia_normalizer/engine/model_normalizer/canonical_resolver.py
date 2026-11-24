"""
Résolveur canonique pour E-commerce AI V2.
Associe un appareil scrapé à son identifiant officiel en utilisant la hiérarchie :
Code Technique (0.99) > Modèle Exact (0.97) > Alias (0.92) > Nettoyage Lexical (0.85).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CANONICAL_DB_FILE = Path(__file__).parent / "canonical_devices.json"


def normalize_slug_key(text: str) -> str:
    """Génère un slug propre ascii sans accents ni caractères spéciaux."""
    if not text:
        return ""
    ascii_txt = unicodedata.normalize("NFKD", str(text)).encode("ASCII", "ignore").decode("utf-8").lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_txt).strip("_")


class ResolvedDevice:
    def __init__(
        self,
        raw_model: str,
        canonical_brand: str,
        canonical_model: str,
        canonical_slug: str,
        technical_codes: List[str],
        confidence: float,
        resolution_method: str,
        status: str = "validated",
        model_status: str = "RESOLVED",
        resolution_strength: str = "HIGH",
        keep_record: bool = True,
        canonical_resolution: Optional[bool] = None,
        slug_type: Optional[str] = None,
    ):
        self.raw_model = raw_model
        self.canonical_brand = canonical_brand
        self.canonical_model = canonical_model
        self.canonical_slug = canonical_slug
        self.technical_codes = technical_codes
        self.confidence = confidence
        self.resolution_method = resolution_method
        self.status = status
        self.model_status = model_status
        self.resolution_strength = resolution_strength
        self.keep_record = keep_record
        self.canonical_resolution = (model_status == "RESOLVED") if canonical_resolution is None else canonical_resolution
        self.slug_type = ("canonical" if self.canonical_resolution else "provisional") if slug_type is None else slug_type
        self.resolved_canonical_slug = canonical_slug if self.canonical_resolution else None
        self.provisional_slug = canonical_slug if not self.canonical_resolution else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_model": self.raw_model,
            "brand": self.canonical_brand,
            "model": self.canonical_model,
            "canonical_slug": self.canonical_slug,
            "canonical_resolution": self.canonical_resolution,
            "slug_type": self.slug_type,
            "resolved_canonical_slug": self.resolved_canonical_slug,
            "provisional_slug": self.provisional_slug,
            "technical_codes": self.technical_codes,
            "confidence": self.confidence,
            "resolution_method": self.resolution_method,
            "resolution_strength": self.resolution_strength,
            "model_status": self.model_status,
            "status": self.status,
            "keep_record": self.keep_record,
        }


class CanonicalResolver:
    _instance: Optional[CanonicalResolver] = None

    def __init__(self, db_path: Path = CANONICAL_DB_FILE):
        self.db_path = db_path
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.tech_code_index: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        self.alias_index: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
        self._load_database()

    @classmethod
    def get_instance(cls) -> CanonicalResolver:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_database(self) -> None:
        if not self.db_path.exists():
            return
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                self.devices = json.load(f)
        except Exception:
            self.devices = {}

        for slug, dev in self.devices.items():
            brand_norm = normalize_slug_key(dev.get("brand", ""))
            # Indexer par codes techniques
            for code in dev.get("technical_codes", []):
                self.tech_code_index[code.upper()] = (slug, dev)

            # Indexer par modèle officiel et alias
            official_m = dev.get("model", "")
            self.alias_index[(brand_norm, normalize_slug_key(official_m))] = (slug, dev)
            for alias in dev.get("aliases", []):
                self.alias_index[(brand_norm, normalize_slug_key(alias))] = (slug, dev)

    def resolve(
        self,
        raw_model: str,
        brand: str = "",
        detected_tech_codes: Optional[List[str]] = None,
        candidate_model: Optional[str] = None,
    ) -> ResolvedDevice:
        """
        Résout un modèle vers son homologue canonique officiel.
        """
        tech_codes = list(detected_tech_codes or [])
        brand_str = str(brand or "").strip()
        brand_norm = normalize_slug_key(brand_str)

        # 1. Résolution Prioritaire : Code Technique Matériel (0.99)
        for code in tech_codes:
            code_upper = code.upper()
            if code_upper in self.tech_code_index:
                slug, dev = self.tech_code_index[code_upper]
                return ResolvedDevice(
                    raw_model=raw_model,
                    canonical_brand=dev["brand"],
                    canonical_model=dev["model"],
                    canonical_slug=slug,
                    technical_codes=tech_codes,
                    confidence=0.99,
                    resolution_method="technical_code",
                    status="validated",
                    model_status="RESOLVED",
                    resolution_strength="VERY_HIGH",
                    keep_record=True,
                )

        # 2. Résolution par Correspondance Exacte / Alias (0.97 - 0.92)
        test_candidates = [
            candidate_model or "",
            raw_model or "",
        ]
        for cand in test_candidates:
            if not cand:
                continue
            cand_norm = normalize_slug_key(cand)
            # Enlever la marque si en tête
            if brand_norm and cand_norm.startswith(f"{brand_norm}_"):
                cand_norm = cand_norm[len(brand_norm) + 1 :]

            if (brand_norm, cand_norm) in self.alias_index:
                slug, dev = self.alias_index[(brand_norm, cand_norm)]
                return ResolvedDevice(
                    raw_model=raw_model,
                    canonical_brand=dev["brand"],
                    canonical_model=dev["model"],
                    canonical_slug=slug,
                    technical_codes=tech_codes,
                    confidence=0.97,
                    resolution_method="exact_alias",
                    status="validated",
                    model_status="RESOLVED",
                    resolution_strength="HIGH",
                    keep_record=True,
                )

        # 3. Fallback : Modèle commercial propre déduit (0.85) - DATA RETENTION : JAMAIS DE SUPPRESSION
        clean_model = candidate_model or raw_model or "Inconnu"
        # Enlever la marque du nom si redondante
        if brand_str and clean_model.lower().startswith(brand_str.lower()):
            clean_model = re.sub(rf"(?i)^\s*{re.escape(brand_str)}\s+", "", clean_model).strip()

        # Construire le slug
        b_slug = normalize_slug_key(brand_str) or "generique"
        m_slug = normalize_slug_key(clean_model) or "modele"
        generated_slug = f"{b_slug}_{m_slug}"

        return ResolvedDevice(
            raw_model=raw_model,
            canonical_brand=brand_str or "Autre",
            canonical_model=clean_model,
            canonical_slug=generated_slug,
            technical_codes=tech_codes,
            confidence=0.85,
            resolution_method="lexical_clean",
            status="unverified" if not brand_str else "validated",
            model_status="UNRESOLVED",
            resolution_strength="MEDIUM",
            keep_record=True,
        )
