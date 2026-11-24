"""
Vérificateur de marques mobile intelligent avec vérification externe et cache persistant.
Exécuté lors du traitement IA (et non au merge) pour enrichir la donnée sans perte.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import aiohttp

CACHE_FILE = Path(__file__).resolve().parents[2] / "outputs_ai" / ".cache_brand_verification.json"

# Référentiel des constructeurs officiels mondiaux de smartphones
KNOWN_SMARTPHONE_BRANDS: Dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "google": "Google",
    "huawei": "Huawei",
    "honor": "Honor",
    "oppo": "Oppo",
    "sony": "Sony",
    "oneplus": "OnePlus",
    "realme": "Realme",
    "motorola": "Motorola",
    "moto": "Motorola",
    "vivo": "Vivo",
    "asus": "Asus",
    "nothing": "Nothing",
    "poco": "Poco",
    "redmi": "Redmi",
    "fairphone": "Fairphone",
    "tcl": "TCL",
    "zte": "ZTE",
    "meizu": "Meizu",
    "blackview": "Blackview",
    "umidigi": "Umidigi",
    "oukitel": "Oukitel",
    "doogee": "Doogee",
    "ulefone": "Ulefone",
    "cubot": "Cubot",
    "infinix": "Infinix",
    "tecno": "Tecno",
    "wiko": "Wiko",
    "crosscall": "Crosscall",
    "nokia": "Nokia",
    "cat": "CAT",
    "htc": "HTC",
    "lg": "LG",
    "lenovo": "Lenovo",
    "sharp": "Sharp",
    "panasonic": "Panasonic",
    "alcatel": "Alcatel",
    "agm": "AGM",
    "itel": "Itel",
    "caterpillar": "CAT",
}

# Marques formellement non-téléphonie mobile (appareils photos, consoles de salon, etc.)
KNOWN_NON_PHONE_BRANDS: Dict[str, str] = {
    "nintendo": "Nintendo",
    "playstation": "PlayStation",
    "xbox": "Xbox",
    "sega": "Sega",
    "nikon": "Nikon",
    "canon": "Canon",
    "olympus": "Olympus",
    "fujifilm": "Fujifilm",
    "fuji": "Fujifilm",
    "casio": "Casio",
    "pentax": "Pentax",
    "gopro": "GoPro",
    "dji": "DJI",
    "epson": "Epson",
    "portable": "Portable (PC)",
    "fixe": "Fixe (PC)",
    "valve": "Valve",
    "retro-gaming": "Retro-Gaming",
}


class BrandVerificationResult:
    def __init__(
        self,
        raw_brand: str,
        canonical_brand: str,
        status: str,  # 'KNOWN', 'VERIFIED_EXTERNAL', 'UNVERIFIED', 'NOT_SMARTPHONE'
        is_smartphone_brand: bool,
        verification_source: str,
        confidence: float,
    ):
        self.raw_brand = raw_brand
        self.canonical_brand = canonical_brand
        self.status = status
        self.is_smartphone_brand = is_smartphone_brand
        self.verification_source = verification_source
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "brand": self.canonical_brand,
            "raw_brand": self.raw_brand,
            "is_smartphone_brand": self.is_smartphone_brand,
            "status": self.status,
            "confidence": self.confidence,
            "source_type": self.verification_source,
        }


class BrandVerifier:
    _instance: Optional[BrandVerifier] = None

    def __init__(self, cache_path: Path = CACHE_FILE):
        self.cache_path = cache_path
        self.cache: Dict[str, Dict[str, Any]] = self._load_cache()

    @classmethod
    def get_instance(cls) -> BrandVerifier:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    async def verify_brand(
        self,
        raw_brand: str,
        raw_model: str = "",
        item_title: str = "",
        session: Optional[aiohttp.ClientSession] = None,
    ) -> BrandVerificationResult:
        """
        Vérifie si une marque est un fabricant authentique de smartphones.
        Utilise : Catalogue Connu -> Cache Persistant -> Recherche Web Asynchrone.
        """
        clean_b = str(raw_brand or "").strip()
        key = clean_b.lower()

        # 1. Vérification dans le catalogue officiel mondial immédiat
        if key in KNOWN_SMARTPHONE_BRANDS:
            return BrandVerificationResult(
                raw_brand=clean_b,
                canonical_brand=KNOWN_SMARTPHONE_BRANDS[key],
                status="KNOWN",
                is_smartphone_brand=True,
                verification_source="official_catalog",
                confidence=1.0,
            )

        # 2. Vérification dans les marques formellement exclues (appareils photos, etc.)
        if key in KNOWN_NON_PHONE_BRANDS:
            return BrandVerificationResult(
                raw_brand=clean_b,
                canonical_brand=KNOWN_NON_PHONE_BRANDS[key],
                status="NOT_SMARTPHONE",
                is_smartphone_brand=False,
                verification_source="non_phone_catalog",
                confidence=1.0,
            )

        # 3. Vérification dans le cache persistant
        if key in self.cache:
            entry = self.cache[key]
            return BrandVerificationResult(
                raw_brand=clean_b,
                canonical_brand=entry.get("canonical_brand", clean_b.title()),
                status=entry.get("status", "VERIFIED_EXTERNAL"),
                is_smartphone_brand=entry.get("is_smartphone_brand", True),
                verification_source=entry.get("verification_source", "cached_web"),
                confidence=entry.get("confidence", 0.90),
            )

        # 4. Recherche Web / Connaissances externes
        # Interrogation asynchrone de Wikipedia API pour savoir si la marque produit des smartphones
        is_sp = True
        status_val = "UNVERIFIED"
        source_val = "unverified_fallback"
        conf_val = 0.70
        canonical_name = clean_b.title()

        try:
            close_session_at_end = False
            if session is None:
                session = aiohttp.ClientSession()
                close_session_at_end = True

            encoded_query = urllib.parse.quote(f"{clean_b} smartphone")
            wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded_query}&format=json"
            wiki_headers = {"User-Agent": "E-commerce AI ScrapingNormalizer/2.0 (contact@ecommerce_ai_scraping.fr)"}

            async with session.get(wiki_url, headers=wiki_headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    search_results = data.get("query", {}).get("search", [])
                    if search_results:
                        snippet = " ".join([r.get("snippet", "") + " " + r.get("title", "") for r in search_results[:3]]).lower()
                        # Présence de termes confirmant la fabrication de téléphones mobiles
                        mobile_keywords = ["smartphone", "mobile phone", "android", "cell phone", "handset", "telecommunications"]
                        camera_console_keywords = ["digital camera", "video game console", "dslr", "lens", "printer"]

                        has_mobile = any(kw in snippet for kw in mobile_keywords)
                        has_cam_console = any(kw in snippet for kw in camera_console_keywords)

                        if has_mobile and not (has_cam_console and not ("smartphone" in snippet)):
                            is_sp = True
                            status_val = "VERIFIED_EXTERNAL"
                            source_val = "wikipedia"
                            conf_val = 0.98
                        elif has_cam_console and not has_mobile:
                            is_sp = False
                            status_val = "NOT_SMARTPHONE"
                            source_val = "wikipedia"
                            conf_val = 0.98
                        else:
                            # Preuve externe insuffisante -> UNVERIFIED mais conservé
                            is_sp = True
                            status_val = "UNVERIFIED"
                            source_val = "insufficient_external_proof"
                            conf_val = 0.50

            if close_session_at_end:
                await session.close()

        except Exception:
            # En cas de timeout ou coupure réseau, principe de rétention : on garde avec statut UNVERIFIED
            is_sp = True
            status_val = "UNVERIFIED"
            source_val = "network_timeout_retention"
            conf_val = 0.50

        # Sauvegarder dans le cache
        self.cache[key] = {
            "canonical_brand": canonical_name,
            "status": status_val,
            "is_smartphone_brand": is_sp,
            "verification_source": source_val,
            "confidence": conf_val,
        }
        self._save_cache()

        return BrandVerificationResult(
            raw_brand=clean_b,
            canonical_brand=canonical_name,
            status=status_val,
            is_smartphone_brand=is_sp,
            verification_source=source_val,
            confidence=conf_val,
        )
