"""
Détecteur d'attributs matériels (Stockage, RAM, Couleur, État) pour E-commerce AI V2.
Extrait les spécifications sans jamais amputer le modèle commercial.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

COLOR_MAP = {
    "noir": "Noir", "noire": "Noir", "black": "Noir", "midnight": "Minuit", "minuit": "Minuit",
    "blanc": "Blanc", "blanche": "Blanc", "white": "Blanc", "starlight": "Lumière stellaire",
    "bleu": "Bleu", "bleue": "Bleu", "blue": "Bleu",
    "rouge": "Rouge", "red": "Rouge", "product red": "Rouge",
    "vert": "Vert", "verte": "Vert", "green": "Vert",
    "gris": "Gris", "grise": "Gris", "gray": "Gris", "grey": "Gris", "space gray": "Gris Sidéral",
    "or": "Or", "gold": "Or",
    "argent": "Argent", "silver": "Argent",
    "violet": "Violet", "violette": "Violet", "purple": "Violet",
    "rose": "Rose", "pink": "Rose",
    "jaune": "Jaune", "yellow": "Jaune",
    "titane naturel": "Titane Naturel", "natural titanium": "Titane Naturel",
    "titane noir": "Noir Titane", "black titanium": "Noir Titane",
    "titane blanc": "Blanc Titane", "white titanium": "Blanc Titane",
    "titane bleu": "Bleu Titane", "blue titanium": "Bleu Titane",
    "titane sable": "Titane Sable",
}

STORAGE_RE = re.compile(r"\b(\d+)\s*(Go|GB|To|TB)\b", re.IGNORECASE)
RAM_RE = re.compile(r"\b(\d+)\s*(?:go|gb|mo|mb)\s*(?:de\s+)?ram\b", re.IGNORECASE)
RAM_SHORT_RE = re.compile(r"\b(\d+)\s*ram\b", re.IGNORECASE)
COMBO_RE = re.compile(r"\b(\d+)\s*(?:go|gb)?\s*/\s*(\d+)\s*(go|gb|to|tb)\b", re.IGNORECASE)


def extract_attributes(text: str) -> Dict[str, Any]:
    """Extrait les attributs sans modifier le texte brut."""
    attrs: Dict[str, Any] = {
        "storage_gb": None,
        "ram_gb": None,
        "color": None,
    }
    if not text:
        return attrs

    # 1. Combo RAM/Stockage type '8/256 Go'
    combo = COMBO_RE.search(text)
    if combo:
        r_val = int(combo.group(1))
        s_val = int(combo.group(2))
        unit = combo.group(3).lower()
        if unit in ("to", "tb"):
            s_val *= 1024
        if s_val == 1000:
            s_val = 1024
        attrs["ram_gb"] = r_val
        attrs["storage_gb"] = s_val
    else:
        # RAM explicite
        m_ram = RAM_RE.search(text) or RAM_SHORT_RE.search(text)
        if m_ram:
            try:
                attrs["ram_gb"] = int(m_ram.group(1))
            except Exception:
                pass

        # Stockage explicite
        # Ne pas prendre la RAM comme stockage
        text_no_ram = RAM_RE.sub("", text)
        text_no_ram = RAM_SHORT_RE.sub("", text_no_ram)
        m_st = STORAGE_RE.findall(text_no_ram)
        if m_st:
            for val_str, unit in m_st:
                val = int(val_str)
                if unit.lower() in ("to", "tb"):
                    val *= 1024
                if val == 1000:
                    val = 1024
                if val in (16, 32, 64, 128, 256, 512, 1024, 2048):
                    attrs["storage_gb"] = val
                    break

    # Couleur
    t_lower = text.lower()
    for raw_c, canon_c in sorted(COLOR_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(raw_c)}\b", t_lower):
            attrs["color"] = canon_c
            break

    return attrs
