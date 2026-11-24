"""
Tokenizer contextuel et découpeur de segments pour E-commerce AI V2.
Sépare intelligemment le nom commercial principal des parenthèses et attributs,
sans jamais détruire la donnée brute originale.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


class ParsedModelTokens:
    def __init__(
        self,
        raw_text: str,
        main_name: str,
        parenthetical_segments: List[str],
        identified_tech_codes: List[str],
        identified_attributes: Dict[str, Any],
    ):
        self.raw_text = raw_text
        self.main_name = main_name
        self.parenthetical_segments = parenthetical_segments
        self.identified_tech_codes = identified_tech_codes
        self.identified_attributes = identified_attributes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "main_name": self.main_name,
            "parenthetical_segments": self.parenthetical_segments,
            "identified_tech_codes": self.identified_tech_codes,
            "identified_attributes": self.identified_attributes,
        }


# Regex pour capturer les segments entre parenthèses ou crochets
PARENTHESIS_PATTERN = re.compile(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]")

# Patterns d'attributs évidents à l'intérieur des parenthèses
STORAGE_PATTERN = re.compile(r"^\s*(\d+)\s*(?:go|gb|to|tb)\s*$", re.IGNORECASE)
NETWORK_PATTERN = re.compile(r"^\s*(?:5g|4g|lte)\s*$", re.IGNORECASE)
PURE_NUMBER_PATTERN = re.compile(r"^\s*(\d{1,2})\s*$")  # Ex: (15) dans iPhone (15)


def parse_model_string(raw_model: str, brand: str = "") -> ParsedModelTokens:
    """
    Découpe contextuellement une chaîne de modèle en préservant le texte original.
    Exemples :
      'iPhone 15 Plus (A2847 / A3093 / A3096 / A3094)'
        -> main_name: 'iPhone 15 Plus', tech_codes: ['A2847', 'A3093', 'A3096', 'A3094']
      'Galaxy S20+ (G985F/G986B)'
        -> main_name: 'Galaxy S20+', tech_codes: ['G985F', 'G986B']
      'iPhone (15)'
        -> main_name: 'iPhone 15', tech_codes: [] (15 n'est PAS un code matériel)
      'Phone (2a)'
        -> main_name: 'Phone (2a)', tech_codes: []
    """
    if not raw_model:
        return ParsedModelTokens("", "", [], [], {})

    text = str(raw_model).strip()
    parentheses_matches = PARENTHESIS_PATTERN.findall(text)

    # Cas particulier : modèles dont le nom officiel inclut des parenthèses
    # ex: 'Phone (2a)', 'Phone (1)', 'Phone (2)' chez Nothing
    if re.search(r"\bphone\s*\([12][ab]?\)", text, re.I):
        return ParsedModelTokens(
            raw_text=text,
            main_name=text,
            parenthetical_segments=[],
            identified_tech_codes=[],
            identified_attributes={},
        )

    tech_codes: List[str] = []
    attributes: Dict[str, Any] = {}
    cleaned_main = text

    for segment in parentheses_matches:
        seg_str = segment.strip()
        # 1. Vérifier si c'est un numéro de génération/modèle comme (15) dans iPhone (15)
        m_num = PURE_NUMBER_PATTERN.match(seg_str)
        if m_num:
            # Réintégrer le chiffre dans le nom principal : iPhone (15) -> iPhone 15
            cleaned_main = re.sub(
                rf"[\(\[\{{]\s*{re.escape(seg_str)}\s*[\)\]\}}]",
                f" {seg_str} ",
                cleaned_main,
            )
            continue

        # 2. Vérifier si c'est un attribut de stockage (256 Go)
        m_st = STORAGE_PATTERN.match(seg_str)
        if m_st:
            attributes["storage_raw"] = seg_str
            cleaned_main = re.sub(
                rf"[\(\[\{{]\s*{re.escape(seg_str)}\s*[\)\]\}}]", "", cleaned_main
            )
            continue

        # 3. Vérifier si c'est du réseau (5G)
        if NETWORK_PATTERN.match(seg_str):
            attributes["network"] = seg_str.upper()
            cleaned_main = re.sub(
                rf"[\(\[\{{]\s*{re.escape(seg_str)}\s*[\)\]\}}]",
                f" {seg_str.upper()} ",
                cleaned_main,
            )
            continue

        # 4. Examiner si le segment est composé de codes matériels séparés par des / ou -
        # Ex: A2847 / A3093 ou G985F/G986B
        tokens = [t.strip() for t in re.split(r"[/,;|]+", seg_str) if t.strip()]
        is_hardware_code_segment = False
        found_codes = []

        for tok in tokens:
            # Code Apple A\d{4}
            if re.match(r"^A\d{4}$", tok):
                found_codes.append(tok)
                is_hardware_code_segment = True
            # Code Samsung SM-...
            elif re.match(r"^SM-[A-Z0-9]+$", tok, re.I):
                found_codes.append(tok.upper())
                is_hardware_code_segment = True
            # Code constructeur atelier type G985F, N986B, A546B, CL6, KI5, AD8, X6837, X6531B
            elif re.match(r"^[A-Z]{1,3}\d{1,4}[A-Z0-9]{0,2}$", tok, re.I) and not re.match(r"^\d+$", tok):
                found_codes.append(tok.upper())
                is_hardware_code_segment = True

        if is_hardware_code_segment and found_codes:
            tech_codes.extend(found_codes)
            # Retirer le bloc parenthèse du nom commercial principal
            cleaned_main = re.sub(
                rf"[\(\[\{{]\s*{re.escape(seg_str)}\s*[\)\]\}}]", "", cleaned_main
            )
        else:
            # Segment non-matériel : vérifier s'il s'agit d'un descripteur à nettoyer ou à garder
            # Si c'est juste un mot comme (Compact), (Reflex), (Hybride), on l'enlève du nom smartphone
            if seg_str.lower() in ("compact", "reflex", "hybride", "bridge"):
                cleaned_main = re.sub(
                    rf"[\(\[\{{]\s*{re.escape(seg_str)}\s*[\)\]\}}]", "", cleaned_main
                )

    # Nettoyage cosmétique du main_name (espaces multiples, ponctuation résiduelle)
    cleaned_main = re.sub(r"\s+", " ", cleaned_main).strip(" -_/")

    return ParsedModelTokens(
        raw_text=text,
        main_name=cleaned_main,
        parenthetical_segments=parentheses_matches,
        identified_tech_codes=sorted(list(set(tech_codes))),
        identified_attributes=attributes,
    )
