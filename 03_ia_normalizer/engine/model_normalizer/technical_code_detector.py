"""
Détecteur contextuel d'identifiants techniques et codes matériels (Apple, Samsung, Xiaomi, etc.).
Conserve scrupuleusement les codes techniques sans jamais les confondre avec les noms de modèles commerciaux (ex: A546B vs Galaxy A54).
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Codes Apple : A suivi de 4 chiffres exactement (ex: A2847, A3093, A1662)
APPLE_CODE_RE = re.compile(r"\b(A\d{4})\b")

# Codes Samsung officiels complets : SM- suivi de lettre et chiffres (ex: SM-S928B, SM-A546B)
SAMSUNG_SM_CODE_RE = re.compile(r"\b(SM-[A-Z]\d+[A-Z0-9]*)\b", re.IGNORECASE)

# Codes Samsung d'atelier / versions internationales :
# G985F, G986B, N986B, A546B, S908B, F946B, etc.
# Règle stricte : 1 lettre de famille (G, N, A, S, F, X, T, M, J) + 3 ou 4 chiffres + 1 ou 2 lettres de révision/région
SAMSUNG_HARDWARE_RE = re.compile(r"\b([GNASFXTMJE]\d{3,4}[A-Z0-9]{1,2})\b")

# Noms commerciaux protégés à NE JAMAIS extraire comme des codes techniques
PROTECTED_COMMERCIAL_TOKENS = {
    "A10", "A12", "A13", "A14", "A15", "A16", "A20", "A21", "A22", "A23", "A24", "A25",
    "A30", "A31", "A32", "A33", "A34", "A35", "A50", "A51", "A52", "A53", "A54", "A55",
    "A70", "A71", "A72", "A73", "A80", "A90",
    "S10", "S20", "S21", "S22", "S23", "S24", "S25",
    "M12", "M13", "M14", "M15", "M23", "M33", "M34", "M54",
    "F13", "F14", "F54",
    "Z FOLD", "Z FLIP",
}


def extract_technical_codes(text: str, brand: str = "") -> Tuple[List[str], str]:
    """
    Extrait les codes matériels d'un texte et retourne :
    (liste_des_codes_trouves, texte_sans_les_codes_si_en_suffixe)
    """
    if not text:
        return [], ""

    codes: List[str] = []
    b_lower = (brand or "").lower().strip()

    # 1. Détection Apple
    if b_lower in ("apple", "") or "iphone" in text.lower():
        apple_matches = APPLE_CODE_RE.findall(text)
        for m in apple_matches:
            # Sécurité : vérifier que ce n'est pas précédé d'un mot commercial
            codes.append(m.upper())

    # 2. Détection Samsung SM-...
    sm_matches = SAMSUNG_SM_CODE_RE.findall(text)
    for m in sm_matches:
        codes.append(m.upper())

    # 3. Détection Samsung codes d'atelier (ex: G985F, A546B)
    if b_lower in ("samsung", "") or "galaxy" in text.lower():
        hw_matches = SAMSUNG_HARDWARE_RE.findall(text)
        for m in hw_matches:
            upper_m = m.upper()
            # Sécurité contextuelle : ne pas extraire si c'est un nom commercial protégé
            if upper_m not in PROTECTED_COMMERCIAL_TOKENS:
                codes.append(upper_m)

    # Dédoublonner
    unique_codes = sorted(list(set(codes)))

    # Nettoyer les codes trouvés du texte seulement s'ils apparaissent comme suffixes ou parenthèses
    cleaned_text = text
    for c in unique_codes:
        # Enlever ' (A2847)' ou ' A2847 '
        cleaned_text = re.sub(rf"[\(\[\{{]?\s*\b{re.escape(c)}\b\s*[\)\]\}}/]*", " ", cleaned_text)

    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip(" -_/")

    return unique_codes, cleaned_text
