#!/usr/bin/env python3
"""
device_validator.py
===================
Module d'intégrité, de taxonomie officielle et de validation canonique
pour les marques, modèles et tarifs de smartphones du projet E-commerce AI Scraping.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple, Dict, Any

# 1. LISTE BLANCHE STRICTE DES MARQUES OFFICIELLES DE SMARTPHONES DANS LE MONDE
LEGITIMATE_SMARTPHONE_BRANDS: Dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "redmi": "Xiaomi",
    "poco": "Xiaomi",
    "google": "Google",
    "huawei": "Huawei",
    "honor": "Honor",
    "oppo": "Oppo",
    "oneplus": "OnePlus",
    "sony": "Sony",
    "motorola": "Motorola",
    "nokia": "Nokia",
    "realme": "Realme",
    "vivo": "Vivo",
    "asus": "Asus",
    "nothing": "Nothing",
    "cmf": "Nothing",
    "fairphone": "Fairphone",
    "blackview": "Blackview",
    "crosscall": "Crosscall",
    "doogee": "Doogee",
    "ulefone": "Ulefone",
    "oukitel": "Oukitel",
    "umidigi": "Umidigi",
    "tcl": "TCL",
    "wiko": "Wiko",
    "zte": "ZTE",
    "alcatel": "Alcatel",
    "htc": "HTC",
    "lg": "LG",
    "meizu": "Meizu",
    "infinix": "Infinix",
    "lenovo": "Lenovo",
    "sharp": "Sharp",
    "doro": "Doro",
    "logicom": "Logicom",
    "konrow": "Konrow",
    "panasonic": "Panasonic",
    "kyocera": "Kyocera",
    "cat": "CAT",
    "caterpillar": "CAT",
    "unihertz": "Unihertz",
    "cubot": "Cubot",
    "fossibot": "Fossibot",
    "agm": "AGM",
    "hammer": "Hammer",
    "oscal": "Oscal",
    "blackberry": "Blackberry",
    "fujitsu": "Fujitsu",
    "uniwa": "Uniwa",
    "soyes": "Soyes",
}

# 2. MARQUES ET TERMES STRICTEMENT EXCLUS (PARASITES / NON-CONSTRUCTEURS)
EXCLUDED_PARASITIC_BRANDS = {
    "simpletek", "android", "générique", "generique", "smartphone", "inconnu",
    "autre", "autres", "aixun", "mechanic", "nintendo", "playstation", "xbox",
    "microsoft", "acer", "king9000", "m17 pro max", "s24 mini", "s24 ultra",
    "s26ultra", "100%", "sans marque", "- sans marque/générique -", "noname",
    "unbranded", "equipements", "informatique", "outillage", "consommables",
    "smart", "portable", "fixe", "valve", "retro-gaming"
}

# 3. MOTS CLÉS DE REJET FORMEL DES APPAREILS HORS PÉRIMÈTRE SMARTPHONE
NON_PHONE_KEYWORDS_RE = [
    # A. Outils / Équipement Atelier B2B
    (re.compile(r"\b(microscope|soudure|station\s+de\s+soudure|t400|t420|fer\s+[aà]\s+souder|station\s+air\s+chaud)\b", re.I), "Outillage Atelier B2B"),
    # B. Ordinateurs portables Apple & PC
    (re.compile(r"\b(macbook|retina|unibody|imac|mac\s+mini|mac\s+pro|portable|fixe|tour\s+pc|pc\s+fixe|pc\s+portable|pc\s+gamer)\b", re.I), "Ordinateur Portable / PC"),
    (re.compile(r"^air\s+\d{2}.*\(a\d{4}\)", re.I), "MacBook Air"),
    # C. Consoles de jeux
    (re.compile(r"\b(ps2|ps3|ps4|ps5|playstation|xbox|switch|joy-con|wii|game\s+cube|steam\s+deck|nintendo\s+nes|nes\s+mini)\b", re.I), "Console de Jeu"),
    # D. Instruments de musique / Guitares
    (re.compile(r"\b(000-18|guitare|martin\s+000)\b", re.I), "Guitare / Instrument"),
    # E. Modèles spéculatifs ou fictifs
    (re.compile(r"\b(iphone\s+duo)\b", re.I), "Modèle Spéculatif Fictif"),
    # F. Tablettes tactiles pures
    (re.compile(r"\b(ipad|galaxy\s+tab|redmi\s+pad|xiaomi\s+pad|mediapad|xperia\s+z[24]\s+tablet|iconia\s+tab|memo\s+pad)\b", re.I), "Tablette Tactile"),
    # F2. Codes internes scraper iPad (ip10, ip11, ip12, ip13, ip14 sans iphone devant)
    (re.compile(r"(?<![a-z])ip\d{2,}(?:\s*\.\d+)?(?:\s+\d+(?:st|nd|rd|th)?\s+gen)?(?:\s+\(a\d{4}\))?", re.I), "Code Interne iPad"),
    # G. Montres connectées
    (re.compile(r"\b(apple\s*watch|applewatch|watch\s+series|galaxy\s+watch|series\s+\d+\s*\(\d+mm\)|ultra\s+\d+\s*\(\d+mm\))\b", re.I), "Montre Connectée"),
    # G2. Apple Watch Ultra / Apple Watch SE sans contexte iPhone
    (re.compile(r"\b(watch\s+ultra|ultra\s+\d+(?:\s*\(\d+\s*mm\))?|watch\s+se)\b", re.I), "Apple Watch"),
    # H. Trottinettes / Mobilité
    (re.compile(r"\b(scooter|trottinette|mi\s+electric\s+scooter)\b", re.I), "Trottinette / Mobilité"),
    # I. Avis clients / Témoignages web scrapis
    (re.compile(r"(★★★★★|★|\bcamille\s+l\.|\bavis\s+client\b|\brecommande\s+[aà]\s+tous\b|\bfissur[eé]\s+le\s+matin\b)", re.I), "Avis Client / Témoignage"),
    # J. Bannières publicitaires / Textes marketing génériques
    (re.compile(r"\b(selon\s+mod[eè]le|devis\s+gratuit|pos[eé]\s+en\s+\d+h?|dalle\s+oled\s+qualit[eé]\s+oem|autonomie\s+retrouv[eé]e|garantie\s+\d+\s*mois|v[eé]rifier\s+mon\s+mod[eè]le)\b", re.I), "Bannière Marketing"),
    # K. En-têtes de navigation et liens de catégories
    (re.compile(r"\bdevice\s*/\s*r[eé]parations?\b", re.I), "En-tête Navigation Atelier"),
    # L. Emballages / Boîtes vides
    (re.compile(r"\b(bo[iî]te|coffret|packaging|box)\s*(vide|seule?|empty)\b|\b(empty\s+box|box\s+only)\b", re.I), "Emballage Vide"),
    # M. Pièces détachées métalliques / châssis
    (re.compile(r"\b(ch[aâ]ssis\s+(seul|central|m[eé]tallique)|cadre\s+m[eé]tallique|pour\s+pi[eè]ces|bloqu[eé]\s+icloud|icloud\s+lock|factice|dummy)\b", re.I), "Pièce Détachée Seule"),
    # N. iPad Air numéroté (Air 2, Air 3... sans contexte iPhone)
    (re.compile(r"\bair\s+[2-9]\b", re.I), "iPad Air Numéroté"),
    # O. Multi-modèles invalides (iPhone 12/13, Galaxy A53/A54, etc.)
    (re.compile(r"\b(?:iphone|galaxy|pixel|xperia)\s+\w+\s*[/\-]\s*\w+\s*(?:series|série)?\b", re.I), "Multi-Modèle Invalide"),
    # P. Apple Air sans précision iPhone (iPad Air varianté)
    (re.compile(r"^(?:apple\s+)?air\s+\d+\s*(?:gb|go|tb|to)\b", re.I), "iPad Air Capacité Seule"),
    # Q. Appareils photos numériques / reflex / bridges / compacts / optiques
    (re.compile(r"\b(appareil\s+photo|reflex|hybride|bridge|compact|bloc\s+optique|nettoyage\s+capteur|alpha\s+nex|coolpix|powershot|cyber-?shot|lumix|eos\s+r)\b", re.I), "Appareil Photo / Reflex"),
]

# 4. RÈGLES DE RÉAFFECTATION DES MARQUES INVERSÉES OU MAL ASSIGNÉES
BRAND_REASSIGNMENT_RULES = [
    (re.compile(r"\bxperia\b", re.I), "Sony"),
    (re.compile(r"\bhonor\b", re.I), "Honor"),
    (re.compile(r"\b(iphone|ipad|macbook|airpods|apple\s+watch)\b", re.I), "Apple"),
    (re.compile(r"\bgalaxy\b", re.I), "Samsung"),
    (re.compile(r"\b(pixel)\b", re.I), "Google"),
    (re.compile(r"\b(redmi|poco)\b", re.I), "Xiaomi"),
    (re.compile(r"\b(zenfone|rog\s+phone)\b", re.I), "Asus"),
    (re.compile(r"\b(blade)\b", re.I), "ZTE"),
]

# 5. PLANCHERS DE PRIX DE VENTE (SMARTPHONES NEUFS & RECONDITIONNÉS)
UNIVERSAL_SALE_PRICE_FLOOR = 40.0
UNIVERSAL_SALE_PRICE_CEILING = 3500.0  # Sauf Huawei Mate XT

MODEL_SALE_PRICE_FLOORS = [
    (re.compile(r"iphone\s*16", re.I), 350.0),
    (re.compile(r"iphone\s*15", re.I), 250.0),
    (re.compile(r"iphone\s*14", re.I), 180.0),
    (re.compile(r"iphone\s*13", re.I), 140.0),
    (re.compile(r"iphone\s*12", re.I), 100.0),
    (re.compile(r"iphone\s*11", re.I), 80.0),
    (re.compile(r"galaxy\s*s2[45]", re.I), 250.0),
    (re.compile(r"galaxy\s*s2[23]", re.I), 160.0),
    (re.compile(r"galaxy\s*z\s*(flip|fold)\s*[34567]", re.I), 200.0),
    (re.compile(r"pixel\s*(8|9|10)", re.I), 180.0),
]

# Mensualités typiques crédit/forfait Darty à rejeter si le prix total est écrasé
REJECTED_MONTHLY_INSTALLMENTS = {9.0, 19.0, 24.17, 30.0, 37.0}

# 5b. TABLE DE CANONICALIZATION DES VARIANTES D'IPHONE SE
# Mappe toutes les formes de nommage vers 3 formes canoniques officielles
APPLE_SE_CANONICAL_MAP = [
    # SE 1ère génération (2016)
    (re.compile(r"(?i)\biphone\s+se\s*(?:1(?:\s*[eè]?re?\s*g[eé]n(?:ération)?)?|2016|2015|32\s*gb|32\s*go)?\b$"), "iPhone SE (2016)"),
    # SE 2ème génération (2020)
    (re.compile(r"(?i)\biphone\s+se\s*(?:2(?:[eè]?\s*(?:me?|[eè]me?|nd|2nd)?(?:\s*g[eé]n(?:ération)?)?)?|2020)\b"), "iPhone SE (2020)"),
    # SE 3ème génération (2022)
    (re.compile(r"(?i)\biphone\s+se\s*(?:3(?:\s*[eè]?me?\s*g[eé]n(?:ération)?)?|2022|se3|3rd)\b"), "iPhone SE (2022)"),
]

# 5c. MOTIFS DE REJECTION DES NOMS DE MODÈLES INVALIDES (post-LLM)
# Multi-modèles, codes internes, bundles avec accessoires
INVALID_MODEL_RE = re.compile(
    r"(?:"
    r"\d+\s*[/\-]\s*\d+"  # Multi-modèles séparés par / ou - (ex: 12/13, 12-14)
    r"|"
    r"\+\s*(?:chargeur|magsafe|etui|coque|airpods|cable|adaptateur)"  # Bundles
    r"|"
    r"\d+\s*(?:series|série)\b"  # Séries génériques
    r"|"
    r"\b(?:i17|ip\d{4}|ippro\d+)"  # Codes internes Apple
    r")",
    re.IGNORECASE,
)

REPAIR_PRICE_MIN_FLOOR = 10.0      # En-dessous de 10€ = frais de port ou visserie (sauf diagnostic)
REPAIR_PRICE_MAX_CEILING = 1500.0  # Au-dessus de 1500€ = erreur de parsing (ex: 52020€ ou 3591€)


def validate_and_canonicalize_brand(raw_brand: str | None, raw_model: str | None, title: str | None) -> Optional[str]:
    """
    Valide et normalise la marque d'un appareil selon le référentiel officiel.
    Corrige les inversions de marques et rejette les marques parasites.
    """
    b_str = (raw_brand or "").strip().lower()
    m_str = (raw_model or "").strip()
    t_str = (title or "").strip()
    full_text = f"{b_str} {m_str} {t_str}".lower()

    # 1. Vérifier d'abord si le modèle impose une marque officielle spécifique (Inversion prioritaire)
    for pat, canonical_brand in BRAND_REASSIGNMENT_RULES:
        if pat.search(f"{m_str} {t_str}"):
            # Cas spécial : Huawei Honor -> Honor
            if canonical_brand == "Honor" and b_str in ("huawei", "honor"):
                return "Honor"
            # Cas spécial : Sony Xperia sous Apple
            if canonical_brand == "Sony" and ("xperia" in m_str.lower() or "xperia" in t_str.lower()):
                return "Sony"
            # Cas spécial : Google Pixel sous Smartphone
            if canonical_brand == "Google" and "pixel" in m_str.lower():
                return "Google"
            # Si le modèle est clairement identifiable
            if canonical_brand in ("Apple", "Samsung", "Xiaomi", "Asus", "ZTE"):
                if pat.search(m_str):
                    return canonical_brand

    # 2. Rejet des marques parasites et des fabricants formellement non-téléphonie (appareils photos, consoles...)
    if b_str in EXCLUDED_PARASITIC_BRANDS:
        return None

    # 3. Vérification dans la liste des constructeurs mondiaux
    if b_str in LEGITIMATE_SMARTPHONE_BRANDS:
        return LEGITIMATE_SMARTPHONE_BRANDS[b_str]

    # Tentative de correspondance partielle
    for legit_key, legit_name in LEGITIMATE_SMARTPHONE_BRANDS.items():
        if legit_key == b_str or f" {legit_key} " in f" {b_str} " or b_str.startswith(legit_key):
            return legit_name

    # Rétention intelligente : si ce n'est pas une marque formellement interdite,
    # on conserve la marque au lieu de la détruire (UNVERIFIED)
    try:
        from .model_normalizer.brand_verifier import KNOWN_NON_PHONE_BRANDS
    except (ImportError, ValueError):
        from model_normalizer.brand_verifier import KNOWN_NON_PHONE_BRANDS
    if b_str in KNOWN_NON_PHONE_BRANDS:
        return None

    return b_str.title() if len(b_str) >= 2 else None


def is_valid_smartphone_device(brand: str, model: str, title: str = "") -> Tuple[bool, str]:
    """
    Vérifie qu'un appareil est un smartphone authentique et non un ordinateur, microscope, console, etc.
    Applique le principe de rétention : seul le cas certain de non-smartphone est rejeté.
    """
    combined = f"{brand} {model} {title}".lower()

    # Vérification des mots-clés de rejet formel (tablettes pures, PC, consoles)
    for pat, reason in NON_PHONE_KEYWORDS_RE:
        if pat.search(combined):
            return False, f"Rejet non-smartphone : {reason}"

    # Vérification de la marque parasite
    if brand.lower() in EXCLUDED_PARASITIC_BRANDS:
        return False, f"Marque parasite : {brand}"

    try:
        from .model_normalizer.brand_verifier import KNOWN_NON_PHONE_BRANDS
    except (ImportError, ValueError):
        from model_normalizer.brand_verifier import KNOWN_NON_PHONE_BRANDS
    if brand.lower() in KNOWN_NON_PHONE_BRANDS:
        return False, f"Marque non-smartphone : {brand}"

    # Modèle trop court ou vide
    if len(model.strip()) < 1:
        return False, "Nom de modèle vide"

    return True, "Valide"


def canonicalize_model_name(brand: str, model: str) -> Optional[str]:
    """
    Canonicalize et valide le nom d'un modèle après normalisation LLM.
    - Applique la table APPLE_SE_CANONICAL_MAP pour les variantes d'iPhone SE.
    - Rejette les multi-modèles, fiches techniques orphelines, codes internes.
    - Nettoie les artefacts résiduels (Dual SIM, USB, dimensions, etc.)
    Retourne le nom canonical propre, ou None si le modèle est invalide.
    """
    return clean_and_canonicalize_model_name(brand, model)


def is_valid_sale_price(price: Optional[float], brand: str, model: str, title: str = "") -> Tuple[bool, str]:
    """
    Vérifie qu'un prix de vente (New / Refurbished / Used) est réaliste et conforme.
    """
    if price is None:
        return False, "Prix absent (None)"

    try:
        p = float(price)
    except (ValueError, TypeError):
        return False, "Prix non convertible en float"

    combined = f"{brand} {model} {title}".lower()

    # 1. Plancher universel smartphone
    if p < UNIVERSAL_SALE_PRICE_FLOOR:
        return False, f"Prix {p}€ inférieur au plancher universel de {UNIVERSAL_SALE_PRICE_FLOOR}€"

    # 2. Mensualités Darty
    if p in REJECTED_MONTHLY_INSTALLMENTS and any(w in combined for w in ["mois", "forfait", "reprise", "abonnement", "credit", "financement"]):
        return False, f"Prix {p}€ correspondant à une mensualité ou offre forfait Darty"

    # 3. Planchers par gamme
    for pat, floor in MODEL_SALE_PRICE_FLOORS:
        if pat.search(combined) and p < floor:
            return False, f"Prix {p}€ anormalement bas pour ce modèle (seuil mini : {floor}€)"

    # 4. Plafond maximal
    if p > UNIVERSAL_SALE_PRICE_CEILING and "mate xt" not in combined:
        return False, f"Prix {p}€ supérieur au plafond maximal de {UNIVERSAL_SALE_PRICE_CEILING}€"

    return True, "Prix de vente conforme"


def is_valid_repair_price(price: Optional[float], category: str = "") -> Tuple[bool, str]:
    """
    Vérifie qu'un prix de réparation d'atelier est réaliste.
    """
    if price is None:
        return False, "Prix de réparation absent"

    try:
        p = float(price)
    except (ValueError, TypeError):
        return False, "Prix non convertible"

    # Plafond anti-aberrations (52 020€ ou 3 591€)
    if p > REPAIR_PRICE_MAX_CEILING:
        return False, f"Tarif de réparation {p}€ anormalement élevé (> {REPAIR_PRICE_MAX_CEILING}€)"

    # Plancher anti-frais de port / visserie
    if category != "diagnostic" and p < REPAIR_PRICE_MIN_FLOOR:
        return False, f"Tarif de réparation {p}€ trop bas (< {REPAIR_PRICE_MIN_FLOOR}€)"

    return True, "Tarif de réparation conforme"


def extract_clean_repair_offer(
    r: Dict[str, Any],
    clean_model_func,
) -> Tuple[Optional[Tuple[str, str, str, float]], str]:
    """
    Extrait et nettoie scrupuleusement une offre de réparation ou pièce détachée.
    Sépare le smartphone canonique (marque, modèle épuré) de la prestation de réparation
    (catégorie canonique, tarif) afin d'éviter la prolifération de pseudo-appareils
    (ex: apple_iphone_12_batterie_79 -> Apple / iPhone 12 / batterie / 79€).
    Rejette formellement les avis clients, bannières, faux en-têtes et non-smartphones.
    """
    raw_brand = r.get("brand") or r.get("device_family") or ""
    raw_model = r.get("model") or ""
    item_name = r.get("item_name") or r.get("product_or_part_name") or ""
    rep_raw = r.get("repair_type_raw") or r.get("repair_type") or ""
    price_raw = str(r.get("price_raw") or "")
    source_site = r.get("source_site") or ""

    combined_all = f"{raw_brand} {raw_model} {item_name} {rep_raw} {price_raw}".lower()

    # 1. Filtre anti-avis / témoignages / bannières marketing / faux en-têtes / non-smartphones
    for pat, reason in NON_PHONE_KEYWORDS_RE:
        if pat.search(combined_all):
            return None, f"Rejet non-smartphone : {reason}"

    # 2. Détermination de la marque
    brand = validate_and_canonicalize_brand(raw_brand, raw_model, item_name)
    if not brand:
        return None, "Marque invalide ou non reconnue"

    # 3. Extraction et normalisation contextuelle du nom de modèle
    # Utilise le pipeline en 3 niveaux : parsing des parenthèses, codes techniques et résolution canonique
    try:
        from .model_normalizer import normalize_device_entry
    except (ImportError, ValueError):
        from model_normalizer import normalize_device_entry

    target_str = raw_model or item_name or ""
    # Séparation sur délimiteur si la suite contient une prestation (ex: "Xperia M2 : vitre tactile / LCD")
    if re.search(r"[:\-\|/]\s*(?:vitre|écran|ecran|batterie|lcd|tactile|reparation|réparation)", target_str, re.I):
        target_str = re.split(r"[:\-\|/]\s*(?:vitre|écran|ecran|batterie|lcd|tactile|reparation|réparation)", target_str, flags=re.I)[0].strip()

    # Nettoyage prix avec devise résiduel
    target_str = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:€|eur(?:os)?)(?!\w)", "", target_str, flags=re.I).strip()
    target_str = re.sub(rf"(?i)^\s*{re.escape(brand)}\s+", "", target_str).strip()

    # Normalisation avancée
    norm_res = normalize_device_entry(target_str, raw_brand=brand, item_title=item_name)
    model = norm_res["model"]

    is_valid, reason = is_valid_smartphone_device(brand, model, item_name)
    if not is_valid:
        return None, f"Appareil invalide : {reason}"

    # 4. Détermination de la catégorie de réparation canonique
    canon_cat = "autre"
    rep_text = f"{rep_raw} {item_name}".lower()
    if any(tok in rep_text for tok in ["batterie", "battery"]):
        canon_cat = "batterie"
    elif any(tok in rep_text for tok in ["écran", "ecran", "lcd", "afficheur", "dalle", "vitre avant"]):
        canon_cat = "ecran"
    elif any(tok in rep_text for tok in ["vitre arrière", "vitre arriere", "back_cover", "dos"]):
        canon_cat = "vitre_arriere"
    elif any(tok in rep_text for tok in ["connecteur", "charge", "charging"]):
        canon_cat = "connecteur_charge"
    elif any(tok in rep_text for tok in ["caméra arrière", "camera arriere", "photo arrière", "appareil photo"]):
        canon_cat = "camera_arriere"
    elif any(tok in rep_text for tok in ["caméra avant", "camera avant", "selfie"]):
        canon_cat = "camera_avant"
    elif any(tok in rep_text for tok in ["haut-parleur", "écouteur", "speaker", "micro"]):
        canon_cat = "haut_parleur"
    elif any(tok in rep_text for tok in ["vibreur"]):
        canon_cat = "vibreur"
    elif any(tok in rep_text for tok in ["désoxydation", "desoxydation"]):
        canon_cat = "desoxydation"
    elif any(tok in rep_text for tok in ["diagnostic"]):
        canon_cat = "diagnostic"

    # 5. Extraction et validation du prix
    price_val = None
    try:
        price_val = float(r.get("price", 0))
    except (ValueError, TypeError):
        pass

    if source_site == "mymobileshop.fr" and canon_cat == "autre":
        if price_val and price_val <= 99.0:
            canon_cat = "batterie"
        else:
            canon_cat = "ecran"

    # Rétention intelligente du tarif : diagnostic gratuit (0€) ou acompte sont conservés
    if price_val is None or price_val < 0:
        return None, "Tarif invalide (négatif ou manquant)"

    return (brand, model, canon_cat, price_val), "OK"


# ==============================================================================
# 5d. MOTEUR DE DÉPOLLUTION DÉTERMINISTE DES NOMS DE MODÈLES & EXTRACTION SPECS
# ==============================================================================
CANONICAL_COLOR_MAP: Dict[str, str] = {
    # Nuances constructeurs & couleurs composées
    "rouge framboise": "Rouge Framboise",
    "noir volcanique": "Noir Volcanique",
    "noir carbon": "Noir Carbon",
    "bleu foncé": "Bleu Foncé",
    "lily white": "Blanc",
    "cosmic gold": "Or",
    "or désert": "Or Désert",
    "or desert": "Or Désert",
    "desert titanium": "Or Désert",
    "all black": "Noir",
    "nuage": "Nuage",
    "lavande": "Lavande",
    "lumière stellaire": "Lumière Stellaire",
    "starlight": "Lumière Stellaire",
    "midnight": "Minuit",
    "minuit": "Minuit",
    "phantom black": "Noir",
    "phantom silver": "Argent",
    "phantom white": "Blanc",
    "phantom violet": "Violet",
    "phantom gray": "Gris",
    "phantom grey": "Gris",
    "phantom": "Noir",
    "space gray": "Gris Sidéral",
    "space grey": "Gris Sidéral",
    "space black": "Noir Sidéral",
    "deep purple": "Violet Intense",
    "alpine green": "Vert Alpin",
    "sierra blue": "Bleu Sierra",
    "midnight green": "Vert Nuit",
    "rose gold": "Or Rose",
    "pacific blue": "Bleu Pacifique",
    "sky blue": "Bleu Ciel",
    "dark night": "Noir",
    "pearl white": "Blanc Nacré",
    "onyx black": "Noir",
    "marble white": "Blanc",
    "titanium black": "Noir Titane",
    "titanium gray": "Gris Titane",
    "titanium silver": "Argent Titane",
    "titanium violet": "Violet Titane",
    "titanium blue": "Bleu Titane",
    "titanium natural": "Titane Naturel",
    "titane naturel": "Titane Naturel",
    "titane noir": "Noir Titane",
    "titane blanc": "Blanc Titane",
    "titane bleu": "Bleu Titane",
    "titane sable": "Titane Sable",
    "natural titanium": "Titane Naturel",
    "white titanium": "Blanc Titane",
    "black titanium": "Noir Titane",
    # Couleurs fondamentales
    "noir": "Noir", "noire": "Noir", "black": "Noir",
    "blanc": "Blanc", "blanche": "Blanc", "white": "Blanc",
    "bleu": "Bleu", "bleue": "Bleu", "blue": "Bleu",
    "rouge": "Rouge", "red": "Rouge",
    "vert": "Vert", "verte": "Vert", "green": "Vert",
    "gris": "Gris", "grise": "Gris", "gray": "Gris", "grey": "Gris",
    "or": "Or", "gold": "Or",
    "argent": "Argent", "silver": "Argent",
    "violet": "Violet", "violette": "Violet", "purple": "Violet",
    "rose": "Rose", "pink": "Rose",
    "jaune": "Jaune", "yellow": "Jaune",
    "orange": "Orange",
    "graphite": "Graphite",
    "titane": "Titane", "titanium": "Titane",
    "carbon": "Carbon",
}

_COLOR_KEYS_SORTED = sorted(CANONICAL_COLOR_MAP.keys(), key=len, reverse=True)
CANONICAL_COLOR_RE = re.compile(rf'\b({"|".join(re.escape(k) for k in _COLOR_KEYS_SORTED)})\b', re.IGNORECASE)
CANONICAL_STORAGE_RE = re.compile(r'\b(\d+)\s*(Go|GB|To|TB)\b', re.IGNORECASE)

MODEL_STRIP_PATTERNS = [
    # Reconditionneurs / Préfixes parasites
    (re.compile(r'\b(?:Tier1\s+T1A|T1A|Tier1)\b(?:\s*[-–—/])?\s*', re.I), ""),
    # Références techniques internes Samsung
    (re.compile(r'\bSM-[A-Z]\d+[A-Z0-9]*\b', re.I), ""),
    (re.compile(r'\bGT-[A-Z]\d+[A-Z0-9]*\b', re.I), ""),
    # Processeurs / SoCs
    (re.compile(r'\bgoogle\s+tensor(?:\s+g\d+)?\b', re.I), ""),
    (re.compile(r'\btensor(?:\s+g\d+)?\b', re.I), ""),
    (re.compile(r'\bsnapdragon(?:\s+\d+[a-z0-9]*)?\b', re.I), ""),
    (re.compile(r'\bmediatek(?:\s+[a-z0-9]+)?\b', re.I), ""),
    (re.compile(r'\bexynos(?:\s+[a-z0-9]+)?\b', re.I), ""),
    # Diagonales / Dimensions d écran
    (re.compile(r"\(\s*\d{1,2}(?:[.,]\d+)?\s*(?:cm|[\x22\x27\u201d\u2019]|pouces?)?\s*\)", re.I), ""),
    (re.compile(r'\b\d{1,2}(?:[.,]\d+)?\s*(?:cm|pouces?)\b', re.I), ""),
    (re.compile(r"\b\d{1,2}[.,]\d+\s*(?:[\x22\x27\u201d\u2019]{1,2})", re.I), ""),
    # SIM & Connectivité
    (re.compile(r'\b(?:double|dual|triple|single)\s*[-/]?\s*(?:nano\s*)?sim\b', re.I), ""),
    (re.compile(r'\bdual[-_]?sim\b', re.I), ""),
    (re.compile(r'\bdouble[-_]?sim\b', re.I), ""),
    (re.compile(r'\bsim\s+unique\b', re.I), ""),
    # OS
    (re.compile(r'\b(ios|ipados)\s+\d+(?:\.\d+)?\b', re.I), ""),
    (re.compile(r'\bandroid\s+\d+(?:\.\d+)?\b', re.I), ""),
    # Ports & Connectique
    (re.compile(r'\busb\s*(?:type-?c|-c)?\b', re.I), ""),
    (re.compile(r'\bmicro-?usb\b', re.I), ""),
    # Batterie
    (re.compile(r'\b\d{3,5}\s*mah\b', re.I), ""),
    # Édition spéciale
    (re.compile(r'\(?\s*product\s*\)?\s*red\b', re.I), ""),
    # Specs diverses
    (re.compile(r'\b(amoled|oled|120\s*hz|leica|robuste|hybride)\b', re.I), ""),
    (re.compile(r'\b\d+[.,]\d+k\b', re.I), ""),
    (re.compile(r'\beu\b', re.I), ""),
]

GENERIC_INVALID_MODELS = {
    "", "iphone", "galaxy", "galaxy s", "galaxy z fold", "galaxy a", "redmi", "redmi note",
    "poco", "edge", "g", "moto g", "razr", "a", "c", "gt", "reno", "armor", "ue", "ue 5g",
    "all", "5g", "4g", "portable incassable", "view", "magic", "magic series", "magic ui",
    "noir", "bleu", "blanc", "rouge"
}

STANDARD_ROM_SIZES = {16, 32, 64, 128, 256, 512, 1024, 2048}


def extract_specs_from_model(brand: str, model: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    Extrait simultanément le nom de modèle propre épuré, le stockage et la couleur
    éventuellement dissimulés dans une chaîne de modèle contaminée.
    Retourne: (clean_model_or_None, extracted_storage, extracted_color)
    """
    if not model:
        return None, None, None

    m = str(model).strip()
    extracted_color = None
    extracted_storage = None

    # 1. Extraction couleur
    if re.search(r'\(?\s*product\s*\)?\s*red\b', m, re.I):
        extracted_color = "Rouge"
    else:
        c_match = CANONICAL_COLOR_RE.search(m)
        if c_match:
            raw_c = c_match.group(0).lower()
            if not ("black shark" in m.lower() and raw_c == "black") and not re.search(r'\bred\s*mi\b', m, re.I):
                extracted_color = CANONICAL_COLOR_MAP.get(raw_c, raw_c.title())

    # 2. Extraction stockage
    m_combo = re.findall(r'(\d+)\s*(?:go|gb)?\s*/\s*(\d+)\s*(go|gb|to|tb)', m, re.I)
    if m_combo:
        for r, s, unit in m_combo:
            v = int(s)
            if unit.lower() in ("to", "tb"): v *= 1024
            if v == 1000: v = 1024
            if v in STANDARD_ROM_SIZES:
                extracted_storage = v
                break
    if extracted_storage is None:
        m_no_ram = re.sub(r'\b\d+\s*(?:go|gb|mo|mb)\s*(?:de\s+)?ram\b', '', m, flags=re.I)
        m_no_ram = re.sub(r'\b\d+\s*ram\b', '', m_no_ram, flags=re.I)
        for val, unit in CANONICAL_STORAGE_RE.findall(m_no_ram):
            v = int(val)
            if unit.lower() in ("to", "tb"): v *= 1024
            if v == 1000: v = 1024
            if v in STANDARD_ROM_SIZES or v in (4, 8):
                extracted_storage = v
                break

    # 3. Nettoyage du modèle
    clean_m = clean_and_canonicalize_model_name(brand, m)
    return clean_m, extracted_storage, extracted_color


def clean_and_canonicalize_model_name(brand: str, model: str) -> Optional[str]:
    """
    Dépollue et canonicalise intégralement le nom d\'un modèle :
    - Supprime dimensions, OS, SIM, USB, RAM, coloris, stockages
    - Applique la table canonique Apple SE
    - Retire '5G' / '4G' pour Apple (sauf SE 5G)
    - Rejette les fiches techniques vides / sans modèle réel (retourne None)
    """
    m = (model or "").strip()
    if not m:
        return None

    # Rejet immédiat multi-modèles / bundles
    if INVALID_MODEL_RE.search(m):
        return None

    # Normalisation coquilles
    m = re.sub(r'\bRED\s*MI\b', 'Redmi', m, flags=re.I)

    # Canonicalization iPhone SE
    if brand.lower() == "apple" and "se" in m.lower():
        for pat, canonical_name in APPLE_SE_CANONICAL_MAP:
            if pat.search(m):
                return canonical_name
        if re.search(r'\bse\s*5g\b', m, re.I):
            return "iPhone SE 5G"

    is_black_shark = bool(re.search(r'\bblack\s+shark\b', m, re.I))
    is_view_go = bool(re.search(r'\bview\s+2\s+go\b', m, re.I))

    # Suppression patterns techniques
    for pat, repl in MODEL_STRIP_PATTERNS:
        m = pat.sub(repl, m)

    # Suppression RAM
    m = re.sub(r'\b\d+\s*(?:go|gb|mo|mb)\s*(?:de\s+)?ram\b', '', m, flags=re.I)
    m = re.sub(r'\b\d+\s*ram\b', '', m, flags=re.I)
    m = re.sub(r'\b\d+\s*(?:go|gb)\s*/\s*\d+\s*(?:go|gb)\b', '', m, flags=re.I)

    # Suppression stockage
    if not is_view_go:
        m = CANONICAL_STORAGE_RE.sub("", m)

    # Suppression couleur
    if not is_black_shark:
        m = CANONICAL_COLOR_RE.sub("", m)

    # Suppression marque en préfixe
    m = re.sub(rf"(?i)^\s*{re.escape(brand)}\s+", "", m).strip()

    # Règle Apple 5G / 4G
    if brand.lower() == "apple":
        if not re.search(r'\bse\b', m, re.I):
            m = re.sub(r'\b[45]g\b', '', m, flags=re.I).strip()

    # Normalisation ponctuation
    m = re.sub(r'[\s/,\-_()\"\'.;:]+', ' ', m).strip()
    m = re.sub(r'^\s*[-–—,;()/.]\s*', '', m)
    m = re.sub(r'\s*[-–—,;()/.]\s*$', '', m)
    m = re.sub(r'\s+', ' ', m).strip()

    # Validation du nom propre
    norm = m.lower().strip()
    if norm in GENERIC_INVALID_MODELS or norm == brand.lower().strip():
        return None
    if not re.search(r'[a-zA-Z0-9]', m):
        return None
    if re.match(r'^\d+$', m) and brand.lower() not in ("oneplus", "xiaomi", "nokia", "realme", "honor", "huawei"):
        return None
    if "magic ui" in norm:
        return None

    # Préfixe iPhone pour Apple si manquant
    if brand.lower() == "apple" and not m.lower().startswith("iphone") and not m.lower().startswith("se"):
        m = f"iPhone {m}"

    return m
