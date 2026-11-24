#!/usr/bin/env python3
"""
merge_unified_dataset.py
========================
Script de consolidation et d'uniformisation 360° du catalogue E-commerce AI Scraping.
Fusionne l'ensemble des données normalisées par l'IA (Vente Neuf/Reconditionné/Occasion
et Réparations Ateliers Marseille + Grossiste B2B Utopya) selon l'architecture
canonique validée (Structure_BD - Modèle Option A).

Sorties générées dans ./united_dataset/ :
1. ./united_dataset/unified_catalog_database.json : Base de données globale unifiée
2. ./united_dataset/catalog_index.json : Index ultra-rapide de recherche multi-critères
3. ./united_dataset/devices/<device_id>.json : Documents canoniques unitaires par SKU/capacité
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SRC_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SRC_DIR.parent
PROJECT_ROOT = PIPELINE_DIR.parent

# Importer le module d'intégrité canonique et de validation officielle
sys.path.insert(0, str(PROJECT_ROOT / "03_ia_normalizer" / "engine"))
sys.path.insert(0, str(PROJECT_ROOT / "02_scrapers"))

from device_validator import (
    validate_and_canonicalize_brand,
    clean_and_canonicalize_model_name,
    extract_specs_from_model,
    is_valid_smartphone_device,
    is_valid_sale_price,
    is_valid_repair_price,
    extract_clean_repair_offer,
    LEGITIMATE_SMARTPHONE_BRANDS,
    EXCLUDED_PARASITIC_BRANDS,
)

OUTPUTS_AI_DIR = PROJECT_ROOT / "03_ia_normalizer" / "outputs"
DATASET_DIR = PROJECT_ROOT / "02_scrapers" / "outputs"
NEW_SCRAPERS_DIR = DATASET_DIR
TARGET_UNITED_DIR = PIPELINE_DIR / "outputs" / "latest"
TARGET_DEVICES_DIR = TARGET_UNITED_DIR / "devices"
HISTORY_DIR = PIPELINE_DIR / "outputs" / "history"

# Créer les répertoires cibles
TARGET_UNITED_DIR.mkdir(parents=True, exist_ok=True)
TARGET_DEVICES_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_brand_name(raw_brand: str | None, raw_model: str | None = None, title: str | None = None) -> str | None:
    """Valide et normalise la marque d'un appareil selon le référentiel mondial officiel."""
    return validate_and_canonicalize_brand(raw_brand, raw_model, title)


# Nettoyage et standardisation canonique des noms de modèles
def clean_model_name(brand: str, raw_model: str | None) -> str:
    if not raw_model:
        return "Inconnu"
    # Dépollution déterministe intégrale via le validateur canonique
    cleaned = clean_and_canonicalize_model_name(brand, str(raw_model))
    if cleaned:
        return cleaned

    # Fallback si modèle non reconnu
    m = str(raw_model).strip()
    m = re.sub(rf"(?i)^\s*{re.escape(brand)}\s+", "", m).strip()
    m = re.sub(r"(?i)\b(smartphone|téléphone|telephone|reconditionné|reconditionne|neuf|occasion)\b", "", m).strip()
    m = re.sub(r"\s+", " ", m).strip(" -_")
    return m if m else "Inconnu"


def make_device_slug(brand: str, model: str, storage: Optional[int] = None) -> str:
    """Génère le device_id canonique (ex: apple_iphone_15_128gb, generique_a17_16gb).
    Utilise CanonicalResolver en priorité, puis fallback lexical sécurisé."""
    try:
        from scraper.ai_normalizer.model_normalizer import CanonicalResolver
        resolver = CanonicalResolver.get_instance()
        resolved = resolver.resolve(raw_model=model, brand=brand)
        if resolved and resolved.canonical_slug and resolved.resolution_method in ("technical_code", "exact_alias"):
            base_slug = resolved.canonical_slug
            s = f"_{storage}gb" if storage else ""
            return f"{base_slug}{s}"
    except Exception:
        pass

    b_ascii = unicodedata.normalize("NFKD", brand).encode("ASCII", "ignore").decode("utf-8").lower()
    b = re.sub(r"[^a-z0-9]+", "", b_ascii)
    m_ascii = unicodedata.normalize("NFKD", model).encode("ASCII", "ignore").decode("utf-8").lower()
    m = re.sub(r"[^a-z0-9]+", "_", m_ascii).strip("_")
    s = f"_{storage}gb" if storage else ""
    return f"{b}_{m}{s}"


# ==============================================================================
# GARDE-FOUS MARCHÉ : REJET ANOMALIES DE VENTE & PRIX INCOHÉRENTS
# ==============================================================================
REJECT_PACKAGING_RE = re.compile(
    r"\b(bo[iî]te|coffret|packaging|box)\s*(vide|seule?|d'origine\s+vide|empty)\b"
    r"|\b(empty\s+box|box\s+only|bo[iî]te\s+sans\s+t[eé]l[eé]phone)\b",
    re.IGNORECASE,
)

REJECT_SPARE_PARTS_RE = re.compile(
    r"\b(ch[aâ]ssis\s+(seul|central|m[eé]tallique)|cadre\s+m[eé]tallique|pour\s+pi[eè]ces|bloqu[eé]\s+icloud|icloud\s+lock|non\s+fonctionnel|factice|dummy)\b",
    re.IGNORECASE,
)

REJECT_ACCESSORY_ALONE_RE = re.compile(
    r"\b(coque\s+seule|housse\s+seule|[eé]tui\s+seul|verre\s+tremp[eé]\s+seul|option\s+au\s+choix)\b",
    re.IGNORECASE,
)

MODEL_PRICE_FLOORS = [
    (re.compile(r"iphone\s*15", re.IGNORECASE), 250.0),
    (re.compile(r"iphone\s*14", re.IGNORECASE), 180.0),
    (re.compile(r"iphone\s*13", re.IGNORECASE), 140.0),
    (re.compile(r"iphone\s*12", re.IGNORECASE), 100.0),
    (re.compile(r"iphone\s*11", re.IGNORECASE), 80.0),
    (re.compile(r"galaxy\s*s2[45]", re.IGNORECASE), 250.0),
    (re.compile(r"galaxy\s*s23", re.IGNORECASE), 180.0),
    (re.compile(r"pixel\s*(8|9|10)", re.IGNORECASE), 180.0),
]


def is_valid_smartphone_sale(
    title: str | None,
    brand: str | None,
    model: str | None,
    price: float | None,
) -> bool:
    """Valide scrupuleusement si une offre de vente est un vrai smartphone avec un prix cohérent."""
    t = (title or "").strip()
    b = (brand or "").strip()
    m = (model or "").strip()
    full_text = f"{b} {m} {t}".lower()

    if REJECT_PACKAGING_RE.search(full_text):
        return False
    if REJECT_SPARE_PARTS_RE.search(full_text):
        return False
    if REJECT_ACCESSORY_ALONE_RE.search(full_text):
        return False

    if price is None or price <= 0:
        return False

    # Plancher universel strict pour tout smartphone fonctionnel
    if price < 40.0:
        return False

    # Planchers spécifiques par modèle
    for pat, floor in MODEL_PRICE_FLOORS:
        if pat.search(full_text):
            if price < floor:
                return False

    return True


# Mapping des pannes vers les 11 catégories canoniques
CANONICAL_REPAIRS = {
    "ecran": {
        "label": "Remplacement Écran / Vitre",
        "requires_color": False,
        "aliases": ["screen", "écran", "ecran", "ecran lcd", "vitre", "afficheur", "module afficheur"],
    },
    "vitre_arriere": {
        "label": "Remplacement Vitre Arrière",
        "requires_color": True,
        "aliases": ["back_cover", "vitre arrière", "vitre arriere", "façade arrière", "facade arriere", "dos", "coque arrière"],
    },
    "batterie": {
        "label": "Changement Batterie",
        "requires_color": False,
        "aliases": ["battery", "batterie", "changement batterie"],
    },
    "connecteur_charge": {
        "label": "Réparation Connecteur de Charge",
        "requires_color": False,
        "aliases": ["charging_port", "connecteur de charge", "charge", "connecteur charge"],
    },
    "camera_arriere": {
        "label": "Remplacement Caméra Arrière",
        "requires_color": False,
        "aliases": ["rear_camera", "appareil photo arrière", "caméra arrière", "camera arriere", "lentille caméra", "lentille caméra arrière"],
    },
    "camera_avant": {
        "label": "Remplacement Caméra Avant",
        "requires_color": False,
        "aliases": ["front_camera", "appareil photo avant", "caméra avant", "camera avant", "selfie"],
    },
    "haut_parleur": {
        "label": "Réparation Haut-parleur / Écouteur",
        "requires_color": False,
        "aliases": ["speaker", "earpiece", "haut-parleur", "haut parleur", "écouteur", "microphone", "micro"],
    },
    "vibreur": {
        "label": "Remplacement Vibreur",
        "requires_color": False,
        "aliases": ["vibrator", "vibreur"],
    },
    "desoxydation": {
        "label": "Désoxydation / Nettoyage Liquide",
        "requires_color": False,
        "aliases": ["desoxydation", "désoxydation"],
    },
    "diagnostic": {
        "label": "Diagnostic Atelier & Devis",
        "requires_color": False,
        "aliases": ["diagnostics", "diagnostic", "réinitialisation logicielle", "transfert de données", "réparation logicielle"],
    },
    "autre": {
        "label": "Autres Prestations",
        "requires_color": False,
        "aliases": ["other", "buttons", "motherboard", "carte mère", "bouton power"],
    },
}


def map_repair_to_canonical(raw_type: str | None) -> str:
    if not raw_type:
        return "autre"
    t_clean = unicodedata.normalize("NFKD", str(raw_type).strip().lower())
    t_clean = "".join(c for c in t_clean if not unicodedata.combining(c))

    for cat_id, meta in CANONICAL_REPAIRS.items():
        for alias in meta["aliases"]:
            a_clean = unicodedata.normalize("NFKD", alias.lower())
            a_clean = "".join(c for c in a_clean if not unicodedata.combining(c))
            if a_clean == t_clean or a_clean in t_clean:
                return cat_id
    return "autre"


def compute_summary_stats(items: List[Dict[str, Any]], price_key: str = "price") -> Dict[str, Any]:
    """Calcule min, avg, max, median, mode et total."""
    prices = []
    for it in items:
        p = it.get(price_key)
        try:
            val = float(p)
            if val > 0:
                prices.append(val)
        except (ValueError, TypeError):
            pass

    if not prices:
        return {
            "min_price": None,
            "avg_price": None,
            "max_price": None,
            "median_price": None,
            "mode_price": None,
            "currency": "EUR",
            "total_offers": len(items),
        }

    # Calcul du mode statistique (valeur la plus fréquente)
    counts = Counter(prices)
    most_common_price, _ = counts.most_common(1)[0]

    return {
        "min_price": round(min(prices), 2),
        "avg_price": round(statistics.mean(prices), 2),
        "max_price": round(max(prices), 2),
        "median_price": round(statistics.median(prices), 2),
        "mode_price": round(most_common_price, 2),
        "currency": "EUR",
        "total_offers": len(items),
    }


def iter_records(file_path: Path):
    """Itère de manière robuste sur les enregistrements d'un fichier .jsonl, .json ou .csv."""
    if not file_path or not file_path.exists():
        return
    suffix = file_path.suffix.lower()
    if suffix == '.jsonl':
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                s = line.strip()
                if s:
                    try:
                        yield json.loads(s)
                    except Exception:
                        pass
    elif suffix == '.json':
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    records = data.get('rows') or data.get('items') or data.get('data') or []
                    for r in records:
                        if isinstance(r, dict):
                            yield r
                elif isinstance(data, list):
                    for r in data:
                        if isinstance(r, dict):
                            yield r
        except Exception as e:
            print(f'   ⚠️ Erreur lecture JSON {file_path.name}: {e}')
    elif suffix == '.csv':
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                for r in reader:
                    yield r
        except Exception as e:
            print(f'   ⚠️ Erreur lecture CSV {file_path.name}: {e}')


def main() -> None:
    print("=" * 80)
    print("🚀 [E-commerce AI Scraping Engine] Lancement de la consolidation unifiée multi-sources")
    print(f"📁 Dossier cible : {TARGET_UNITED_DIR}")
    print("=" * 80)

    # 1. Résolution STRICTEMENT ET EXCLUSIVEMENT basée sur les sorties validées de la normalisation IA (outputs_ai/)
    # Règle d'or absolue : scraping -> nettoyage et normalisation IA -> merge_unified_dataset.py
    # Aucun repli automatique (fallback) vers dataset/ brut n'est autorisé.
    def find_latest_source_file(source_key: str) -> Optional[Path]:
        """Trouve exclusivement le dernier run raffiné par l'IA dans outputs_ai/ (.json, .jsonl ou .csv).
        Si la source n'a pas encore subi de normalisation IA, elle n'est pas ingérée."""
        if OUTPUTS_AI_DIR.exists():
            ai_candidates = [
                f for f in OUTPUTS_AI_DIR.glob(f"{source_key}_ai_refined_*")
                if not f.name.endswith(("_issues.jsonl", "_quarantine.jsonl", "_run.json"))
                and not f.name.startswith(".")
                and f.suffix.lower() in (".json", ".jsonl", ".csv")
            ]
            if ai_candidates:
                return max(ai_candidates, key=lambda f: f.stat().st_mtime)

        return None

    # Sources du pôle Vente (Neuf, Reconditionné, Occasion)
    sales_keys = ["amazon", "apple", "darty", "ebay", "samsung", "leboncoin", "ephoneaccess", "ldlc"]
    sales_sources: List[Path] = []
    for k in sales_keys:
        sf = find_latest_source_file(k)
        if sf and sf.exists():
            sales_sources.append(sf)
        else:
            print(f"   ⏳ Source Vente en attente de données : {k}")

    # Sources du pôle Réparation (Ateliers Marseille, Réseaux Nationaux & Constructeurs)
    repair_configs = [
        ("utopya", "b2b_parts"),
        ("xiaomi", "b2b_parts"),
        ("wefix", "workshop"),
        ("save", "workshop"),
        ("phone_cbd", "workshop"),
        ("pixou_phone", "workshop"),
        ("targets", "workshop"),
        ("docteur_it", "workshop"),
        ("iriparo", "workshop"),
        ("samsung_repair", "workshop"),
        ("apple_repair", "workshop"),
        ("mistral_phone", "workshop"),
        ("stuff_telecom", "workshop"),
        ("smartphonepro", "workshop"),
        ("i_rep", "workshop"),
        ("pixel_store", "workshop"),
        ("phonethik", "workshop"),
        ("oppo", "workshop"),
        ("honor", "workshop"),
        ("google", "workshop"),
        ("phone_express", "workshop"),
        ("corner_mobile", "workshop"),
        ("smart_repair_63", "workshop"),
        ("mister_minit", "workshop"),
        ("prosmobile", "workshop"),
        ("mon_reparateur_pro", "workshop"),
        ("clinique_phone", "workshop"),
        ("go_smart", "workshop"),
        ("repairmen", "workshop"),
        ("feexo", "workshop"),
        ("repair_me", "workshop"),
        ("stuff", "workshop"),
        ("imobstore", "workshop"),
        ("urgence_phone", "workshop"),
    ]
    declared_repair_keys = {rk for rk, _ in repair_configs}
    declared_sources = set(sales_keys) | declared_repair_keys

    repair_sources: List[Tuple[Path, str]] = []
    for rk, rtype in repair_configs:
        rf = find_latest_source_file(rk)
        if rf and rf.exists():
            repair_sources.append((rf, rtype))
        else:
            print(f"   ⏳ Source Réparation en attente de données : {rk}")

    # Découverte automatique et robuste de toute nouvelle source supplémentaire raffinée dans outputs_ai
    def is_repair_file(file_path: Path) -> bool:
        try:
            count = 0
            for r_check in iter_records(file_path):
                dt = str(r_check.get("data_type") or "").lower()
                src = str(r_check.get("source_site") or "").lower()
                if dt in ("repair_price", "part_cost", "part_price") or "repair" in src:
                    return True
                if dt in ("product_sales", "sale", "device_sale"):
                    return False
                count += 1
                if count >= 30:
                    break
        except Exception:
            pass
        return False

    if OUTPUTS_AI_DIR.exists():
        discovered_sources: Set[str] = set()
        for f in sorted(OUTPUTS_AI_DIR.glob("*_ai_refined_*")):
            if f.name.endswith(("_issues.jsonl", "_quarantine.jsonl", "_run.json")) or f.name.startswith("."):
                continue
            if f.suffix.lower() not in (".json", ".jsonl", ".csv"):
                continue
            src_name = f.name.split("_ai_refined_")[0]
            if src_name not in declared_sources and src_name not in discovered_sources:
                discovered_sources.add(src_name)
                latest_f = find_latest_source_file(src_name)
                if not latest_f:
                    continue
                if is_repair_file(latest_f):
                    repair_sources.append((latest_f, "workshop"))
                    print(f"   ✨ Découverte automatique source Réparation : {src_name} ({latest_f.name})")
                else:
                    sales_sources.append(latest_f)
                    print(f"   ✨ Découverte automatique source Vente : {src_name} ({latest_f.name})")

    # Dictionnaire de secours pour Amazon en cas d'hallucination LLM (269 items)
    amazon_orig_map: Dict[str, Dict[str, Any]] = {}
    for az_candidate in [DATASET_DIR / "db_amazon.json", DATASET_DIR / "db_amazon.jsonl", DATASET_DIR / "db_amazon.csv"]:
        if az_candidate.exists():
            for row in iter_records(az_candidate):
                pid = row.get("product_id")
                if pid:
                    amazon_orig_map[pid] = row
            if amazon_orig_map:
                break

    print("\n📦 ÉTAPE 1 : Chargement et partitionnement des offres de vente...")
    # Structure : (brand, model, storage) -> offers list by condition
    sales_catalog = defaultdict(lambda: {"new": [], "refurbished": [], "used": [], "colors": set()})
    # Déduplication stricte ventes : (brand, model, storage, condition) -> set of (source_site, price, color)
    seen_sale_offers: Dict[Tuple, Set[Tuple]] = defaultdict(set)
    total_sales_loaded = 0
    total_sales_rejected_dedup = 0

    for path in sales_sources:
        if not path.exists():
            print(f"   ⚠️ Fichier absent ignoré : {path.name}")
            continue
        print(f"   ↳ Ingestion Vente : {path.name}...")
        for r in iter_records(path):
            if not r or not isinstance(r, dict):
                continue
            try:
                # Garde-fou absolu de production : Zéro fuite de quarantaine vers la production
                if r.get("record_status") and r.get("record_status") != "ACCEPTED":
                    continue
                if r.get("include_in_production") is False:
                    continue

                raw_brand = r.get("brand") or r.get("device_family")
                raw_model = r.get("model") or r.get("item_title")

                item_title = r.get("item_title") or raw_model or ""

                # Sécurité spécifique Amazon si hallucination Fairphone
                if r.get("source_site") == "amazon" and str(raw_brand).strip().lower() == "fairphone":
                    pid = r.get("product_id")
                    if pid and pid in amazon_orig_map:
                        orig_r = amazon_orig_map[pid]
                        raw_brand = orig_r.get("brand") or raw_brand
                        raw_model = orig_r.get("model") or orig_r.get("item_title")
                        item_title = orig_r.get("item_title") or raw_model or ""
                        if orig_r.get("storage_gb"):
                            r["storage_gb"] = orig_r["storage_gb"]
                        if orig_r.get("condition"):
                            r["condition"] = orig_r["condition"]

                brand = clean_brand_name(raw_brand, raw_model, item_title)
                if not brand:
                    continue
                
                # Dépollution complète et sauvetage stockage/couleur
                clean_m, ext_storage, ext_color = extract_specs_from_model(brand, raw_model or item_title or "")
                if not clean_m:
                    continue
                model = clean_m

                is_valid_dev, _ = is_valid_smartphone_device(brand, model, item_title)
                if not is_valid_dev:
                    continue

                # Stockage
                storage = None
                if r.get("storage_gb") is not None:
                    try:
                        storage = int(r["storage_gb"])
                        if storage == 1000:
                            storage = 1024
                    except (ValueError, TypeError):
                        pass
                
                # Secourir le stockage extrait si manquant ou masqué par la RAM
                if ext_storage and (storage is None or storage in (1, 2, 3, 4, 6, 8, 12, 16)):
                    storage = ext_storage

                # Fallback regex extraction si non renseigné
                if storage is None and r.get("item_title"):
                    m_st = re.search(r"\b(\d+)\s*(?:go|gb)\b", r["item_title"], re.IGNORECASE)
                    if m_st:
                        storage = int(m_st.group(1))
                        if storage == 1000:
                            storage = 1024
                    else:
                        m_to = re.search(r"\b(\d+)\s*(?:to|tb)\b", r["item_title"], re.IGNORECASE)
                        if m_to:
                            storage = int(m_to.group(1)) * 1024

                # Couleur
                color = r.get("color")
                if color and str(color).strip() and str(color).lower() not in ("null", "none", "unknown"):
                    color = str(color).strip().title()
                elif ext_color:
                    color = ext_color
                else:
                    color = None

                # Condition
                cond_raw = str(r.get("condition", "used")).lower()
                if "new" in cond_raw or "neuf" in cond_raw:
                    cond_key = "new"
                elif "refurb" in cond_raw or "reconditionn" in cond_raw:
                    cond_key = "refurbished"
                else:
                    cond_key = "used"

                price_val = None
                try:
                    price_val = float(r.get("price", 0))
                except (ValueError, TypeError):
                    pass

                # Application du garde-fou strict des ventes de smartphones (anti-boîtes vides, accessoires, prix absurdes)
                is_valid_p, _ = is_valid_sale_price(price_val, brand, model, item_title)
                if not is_valid_p:
                    continue

                offer_obj = {
                    "source_site": r.get("source_site", "market"),
                    "seller_name": r.get("seller_name") or r.get("shop_name") or "Vendeur Certifié",
                    "color": color,
                    "price": price_val,
                    "currency": r.get("currency", "EUR"),
                    "availability": "En stock" if r.get("is_currently_sold", True) else "Rupture",
                    "shipping": f"Livraison {r.get('shipping_cost', '0.00')} €" if r.get("shipping_cost") is not None else "Livraison disponible",
                    "warranty": r.get("warranty") or ("Garantie légale 2 ans" if cond_key == "new" else "Garantie 1 an"),
                    "url": r.get("source_url") or "",
                }
                if cond_key == "refurbished":
                    offer_obj["grade"] = r.get("grade") or "Très bon état"

                # --- DÉDUPLICATION STRICTE VENTES ---
                # Un doublon = même (source_site, price, color) pour le même SKU + condition
                sale_key = (brand, model, storage, cond_key)
                sale_fingerprint = (
                    offer_obj["source_site"],
                    offer_obj["price"],
                    offer_obj["color"],
                )
                if sale_fingerprint in seen_sale_offers[sale_key]:
                    total_sales_rejected_dedup += 1
                    continue
                seen_sale_offers[sale_key].add(sale_fingerprint)
                # --- FIN DÉDUPLICATION ---

                sales_catalog[(brand, model, storage)][cond_key].append(offer_obj)
                if color:
                    sales_catalog[(brand, model, storage)]["colors"].add(color)
                total_sales_loaded += 1
            except Exception:
                pass

    print(f"   ✅ Total offres de vente chargées : {total_sales_loaded} réparties sur {len(sales_catalog)} variantes.")
    if total_sales_rejected_dedup > 0:
        print(f"   🧹 Doublons vente rejetés à l'ingestion : {total_sales_rejected_dedup} (déduplication stricte source+prix+couleur)")

    print("\n🔧 ÉTAPE 2 : Chargement et association des réparations & pièces B2B...")
    # Structure : (brand, model) -> category -> {"b2b_parts": [], "all_providers": []}
    repairs_catalog = defaultdict(lambda: defaultdict(lambda: {"b2b_parts": [], "all_providers": []}))
    # Déduplication stricte réparations
    seen_b2b_parts: Dict[Tuple, Set[Tuple]] = defaultdict(set)
    seen_repair_providers: Dict[Tuple, Set[Tuple]] = defaultdict(set)
    total_repairs_loaded = 0
    total_repairs_rejected_dedup = 0

    for path, kind in repair_sources:
        if not path.exists():
            print(f"   ⚠️ Fichier absent ignoré : {path.name}")
            continue
        print(f"   ↳ Ingestion Réparation ({kind}) : {path.name}...")
        for r in iter_records(path):
            if not r or not isinstance(r, dict):
                continue
            try:
                # Garde-fou absolu de production : Zéro fuite de quarantaine vers la production
                if r.get("record_status") and r.get("record_status") != "ACCEPTED":
                    continue
                if r.get("include_in_production") is False:
                    continue

                clean_res, clean_status = extract_clean_repair_offer(r, clean_model_name)
                if not clean_res:
                    continue
                brand, model, canon_cat, price_val = clean_res

                if kind == "b2b_parts":
                    # Utopya B2B
                    if price_val is None or price_val <= 0 or price_val > 1000.0:
                        continue
                    # part_grade : qualité de la pièce (Soft OLED, Premium, ReLife, etc.)
                    part_grade = (
                        r.get("quality") or r.get("part_grade") or r.get("grade") or None
                    )
                    b2b_item = {
                        "provider": r.get("shop_name") or "Utopya Grossiste B2B",
                        "part_name": r.get("product_or_part_name") or r.get("item_name") or "Pièce Détachée",
                        "part_grade": part_grade,
                        "price_ht": price_val,
                        "currency": r.get("currency", "EUR"),
                        "in_stock": bool(r.get("in_stock", True)),
                        "url": r.get("source_url") or "",
                    }
                    if r.get("color"):
                        b2b_item["color"] = str(r["color"]).strip().title()

                    # --- DÉDUPLICATION STRICTE B2B ---
                    b2b_key = (brand, model, canon_cat)
                    b2b_fingerprint = (
                        b2b_item["provider"],
                        b2b_item["price_ht"],
                        b2b_item.get("part_grade"),
                        b2b_item.get("color"),
                    )
                    if b2b_fingerprint in seen_b2b_parts[b2b_key]:
                        total_repairs_rejected_dedup += 1
                        continue
                    seen_b2b_parts[b2b_key].add(b2b_fingerprint)
                    # --- FIN DÉDUPLICATION ---

                    repairs_catalog[(brand, model)][canon_cat]["b2b_parts"].append(b2b_item)

                else:
                    # Ateliers Marseille (Save, WeFix, Phone CBD, Targets, Pixou Phone)
                    # Adresse réelle : priorité shop_address > address > None (ne pas utiliser la ville comme adresse)
                    real_address = r.get("shop_address") or r.get("address") or None
                    city_val = r.get("city") or "Marseille"
                    # Si l'adresse est identique à la ville (fallback générique), on la laisse None
                    if real_address and real_address.strip().lower() == city_val.strip().lower():
                        real_address = None

                    # QualiRépar : montant et prix final calculés
                    has_qualirepar = bool(
                        "qualirepar" in str(r.get("warranty", "")).lower()
                        or r.get("qualirepar", False)
                        or r.get("qualirepar_discount", False)
                    )
                    qualirepar_amount = None
                    final_price_qualirepar = None
                    if has_qualirepar:
                        # Montant QualiRépar 2024 : 25€ pour smartphones
                        qualirepar_amount = float(r.get("qualirepar_amount", 25.0))
                        final_price_qualirepar = round(price_val - qualirepar_amount, 2)
                        if final_price_qualirepar < 0:
                            final_price_qualirepar = 0.0

                    provider_item = {
                        "shop_name": r.get("shop_name") or r.get("source_site") or "Atelier Réparation Marseille",
                        "address": real_address,
                        "city": city_val,
                        "postal_code": r.get("postal_code") or "13000",
                        "price": price_val,
                        "currency": r.get("currency", "EUR"),
                        "delay": r.get("delay") or "30 à 60 minutes",
                        "warranty": r.get("warranty") or "Garantie atelier 6 mois",
                        "qualirepar_discount": has_qualirepar,
                        "qualirepar_amount": qualirepar_amount,
                        "final_price_with_qualirepar": final_price_qualirepar,
                        "url": r.get("source_url") or "",
                    }
                    if canon_cat == "vitre_arriere":
                        # color_handling : mode de gestion de la couleur pour la vitre arrière
                        color_handling = r.get("color_handling") or "forfait_unique"
                        provider_item["color_handling"] = color_handling
                        provider_item["color_verification_required"] = True
                        provider_item["color_notes"] = (
                            r.get("color_notes")
                            or "Disponibilité de la couleur à confirmer avec l'atelier avant passage."
                        )

                    # --- DÉDUPLICATION STRICTE ATELIERS ---
                    # Un doublon atelier = même (shop_name, price) pour le même modèle + catégorie
                    provider_key = (brand, model, canon_cat)
                    provider_fingerprint = (
                        provider_item["shop_name"],
                        provider_item["price"],
                    )
                    if provider_fingerprint in seen_repair_providers[provider_key]:
                        total_repairs_rejected_dedup += 1
                        continue
                    seen_repair_providers[provider_key].add(provider_fingerprint)
                    # --- FIN DÉDUPLICATION ---

                    repairs_catalog[(brand, model)][canon_cat]["all_providers"].append(provider_item)

                total_repairs_loaded += 1
            except Exception:
                pass

    print(f"   ✅ Total réparations & pièces chargées : {total_repairs_loaded} réparties sur {len(repairs_catalog)} modèles.")
    if total_repairs_rejected_dedup > 0:
        print(f"   🧹 Doublons réparations rejetés à l'ingestion : {total_repairs_rejected_dedup} (déduplication stricte shop+prix)")

    print("\n🏛️ ÉTAPE 3 : Consolidation Canonique Option A & Calculs Statistiques...")

    # Nettoyage préventif des anciens fichiers unitaires pour éviter les orphelins
    for old_file in TARGET_DEVICES_DIR.glob("*.json"):
        try:
            old_file.unlink()
        except Exception:
            pass

    # Identifier tous les couples (brand, model) uniques
    all_models_index = set()
    for (b, m, s) in sales_catalog.keys():
        all_models_index.add((b, m))
    for (b, m) in repairs_catalog.keys():
        all_models_index.add((b, m))

    master_unified_database: Dict[str, Any] = {}
    catalog_index: Dict[str, Any] = {
        "generated_at": now_iso_utc(),
        "total_canonical_skus": 0,
        "brands": defaultdict(lambda: defaultdict(list)),
    }

    # Pour chaque modèle, construire les documents pour chaque capacité connue
    total_skus_created = 0

    for (brand, model) in sorted(all_models_index):
        # Trouver les capacités existantes pour ce modèle dans les ventes
        known_storages = set()
        for (b, m, s) in sales_catalog.keys():
            if b == brand and m == model and s is not None:
                known_storages.add(s)

        # Si aucune capacité n'est connue (ex: console ou réparation seule), créer une entrée sans stockage
        target_storages = sorted(known_storages) if known_storages else [None]

        # Les réparations du modèle s'appliquent à toutes les déclinaisons de stockage du modèle
        model_repairs_raw = repairs_catalog.get((brand, model), {})

        for storage in target_storages:
            device_id = make_device_slug(brand, model, storage)
            sales_data = sales_catalog.get((brand, model, storage), {"new": [], "refurbished": [], "used": [], "colors": set()})

            # Si des ventes existaient sans stockage renseigné et qu'on a des capacités connues,
            # on les intègre à la capacité de base (min) pour ne perdre aucune offre
            if known_storages and storage == min(known_storages):
                unspecified_sales = sales_catalog.get((brand, model, None))
                if unspecified_sales:
                    for cond in ("new", "refurbished", "used"):
                        sales_data[cond].extend(unspecified_sales.get(cond, []))
                    sales_data["colors"].update(unspecified_sales.get("colors", set()))

            # Couleurs disponibles : union des couleurs de vente et des couleurs de pièces B2B
            available_colors: Set[str] = set(sales_data["colors"])
            for cat_data in model_repairs_raw.values():
                for part in cat_data.get("b2b_parts", []):
                    if part.get("color"):
                        available_colors.add(part["color"])

            sorted_colors = sorted(list(available_colors))

            # Structuration des réparations
            repairs_section: Dict[str, Any] = {}
            for cat_id, meta in CANONICAL_REPAIRS.items():
                cat_data = model_repairs_raw.get(cat_id, {"b2b_parts": [], "all_providers": []})
                providers = cat_data["all_providers"]
                parts = cat_data["b2b_parts"]

                summary_rep = compute_summary_stats(providers, price_key="price")
                # Adapter clé pour les prestataires
                summary_rep["total_providers"] = len(providers)
                summary_rep.pop("total_offers", None)

                rep_entry: Dict[str, Any] = {
                    "repair_label": meta["label"],
                    "requires_color": meta["requires_color"],
                    "summary": summary_rep,
                    "b2b_parts": parts,
                    "all_providers": providers,
                }
                if meta["requires_color"]:
                    rep_entry["supported_colors"] = sorted_colors

                repairs_section[cat_id] = rep_entry

            # Structuration des ventes
            market_sales_section = {
                "new": {
                    "summary": compute_summary_stats(sales_data["new"]),
                    "all_offers": sales_data["new"],
                },
                "refurbished": {
                    "summary": compute_summary_stats(sales_data["refurbished"]),
                    "all_offers": sales_data["refurbished"],
                },
                "used": {
                    "summary": compute_summary_stats(sales_data["used"]),
                    "all_offers": sales_data["used"],
                },
            }

            # Détermination du statut de résolution (canonical vs provisional)
            is_canonical = False
            try:
                from scraper.ai_normalizer.model_normalizer import CanonicalResolver
                resolver = CanonicalResolver.get_instance()
                resolved = resolver.resolve(raw_model=model, brand=brand)
                is_canonical = bool(resolved and resolved.canonical_resolution)
            except Exception:
                pass

            canonical_doc = {
                "device_id": device_id,
                "brand": brand,
                "model": model,
                "device_type": "smartphone",
                "storage": storage,
                "ram_gb": None,
                "available_colors": sorted_colors,
                "resolution": "canonical" if is_canonical else "provisional",
                "is_provisional": not is_canonical,
                "last_updated": now_iso_utc(),
                "market_sales": market_sales_section,
                "repairs": repairs_section,
            }

            # Enregistrer dans la base unifiée
            master_unified_database[device_id] = canonical_doc

            # Écrire le document JSON individuel
            doc_file = TARGET_DEVICES_DIR / f"{device_id}.json"
            with open(doc_file, "w", encoding="utf-8") as f:
                json.dump(canonical_doc, f, indent=2, ensure_ascii=False)

            # Enrichir l'index de recherche
            catalog_index["brands"][brand][model].append({
                "storage": storage,
                "device_id": device_id,
                "file_path": f"devices/{device_id}.json",
                "min_new_price": canonical_doc["market_sales"]["new"]["summary"]["min_price"],
                "min_refurbished_price": canonical_doc["market_sales"]["refurbished"]["summary"]["min_price"],
                "min_screen_repair_price": canonical_doc["repairs"]["ecran"]["summary"]["min_price"],
                "total_sales_offers": (
                    canonical_doc["market_sales"]["new"]["summary"]["total_offers"]
                    + canonical_doc["market_sales"]["refurbished"]["summary"]["total_offers"]
                    + canonical_doc["market_sales"]["used"]["summary"]["total_offers"]
                ),
                "total_repair_providers": sum(
                    rep["summary"]["total_providers"] for rep in canonical_doc["repairs"].values()
                ),
            })

            total_skus_created += 1

    catalog_index["total_canonical_skus"] = total_skus_created

    # 4. Écriture des fichiers maîtres
    print("\n💾 ÉTAPE 4 : Enregistrement des fichiers consolidés finaux...")

    import shutil

    # Sauvegarde dans l'historique si une version précédente existe (max 10 archives conservées)
    master_db_path = TARGET_UNITED_DIR / "unified_catalog_database.json"
    if master_db_path.exists():
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_json = HISTORY_DIR / f"unified_catalog_database_{timestamp_str}.json"
        archive_csv = HISTORY_DIR / f"master_dataset_{timestamp_str}.csv"
        try:
            shutil.copy2(master_db_path, archive_json)
            old_csv = TARGET_UNITED_DIR / "master_dataset.csv"
            if old_csv.exists():
                shutil.copy2(old_csv, archive_csv)
            history_files = sorted(HISTORY_DIR.glob("unified_catalog_database_*.json"))
            if len(history_files) > 10:
                for f_to_rm in history_files[:-10]:
                    f_to_rm.unlink(missing_ok=True)
                    csv_to_rm = HISTORY_DIR / f_to_rm.name.replace("unified_catalog_database_", "master_dataset_").replace(".json", ".csv")
                    csv_to_rm.unlink(missing_ok=True)
        except Exception as e:
            print(f"⚠️ Erreur archivage historique : {e}")

    with open(master_db_path, "w", encoding="utf-8") as f:
        json.dump(master_unified_database, f, indent=2, ensure_ascii=False)

    index_path = TARGET_UNITED_DIR / "catalog_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(catalog_index, f, indent=2, ensure_ascii=False)

    # 5. Génération du Dataset Complet Consolidé au format CSV (master_dataset.csv)
    csv_master_path = TARGET_UNITED_DIR / "master_dataset.csv"
    csv_rows = []
    for device_id, doc in master_unified_database.items():
        summary_new = doc.get("market_sales", {}).get("new", {}).get("summary", {})
        summary_refurb = doc.get("market_sales", {}).get("refurbished", {}).get("summary", {})
        summary_used = doc.get("market_sales", {}).get("used", {}).get("summary", {})
        summary_ecran = doc.get("repairs", {}).get("ecran", {}).get("summary", {})
        summary_batt = doc.get("repairs", {}).get("batterie", {}).get("summary", {})
        csv_rows.append({
            "device_id": device_id,
            "brand": doc.get("brand", ""),
            "model": doc.get("model", ""),
            "storage": doc.get("storage", ""),
            "min_new_price": summary_new.get("min_price"),
            "avg_new_price": summary_new.get("avg_price"),
            "total_new_offers": summary_new.get("total_offers", 0),
            "min_refurbished_price": summary_refurb.get("min_price"),
            "avg_refurbished_price": summary_refurb.get("avg_price"),
            "total_refurbished_offers": summary_refurb.get("total_offers", 0),
            "min_used_price": summary_used.get("min_price"),
            "total_used_offers": summary_used.get("total_offers", 0),
            "min_screen_repair_price": summary_ecran.get("min_price"),
            "avg_screen_repair_price": summary_ecran.get("avg_price"),
            "min_battery_repair_price": summary_batt.get("min_price"),
            "total_repair_providers": sum(
                rep.get("summary", {}).get("total_providers", 0) for rep in doc.get("repairs", {}).values() if isinstance(rep, dict)
            ),
            "last_updated": doc.get("last_updated", ""),
        })

    if csv_rows:
        with open(csv_master_path, "w", encoding="utf-8", newline="") as cf:
            writer = csv.DictWriter(cf, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)

    print("=" * 80)
    print("✅ MERGE UNIFIÉ RÉALISÉ AVEC SUCCÈS !")
    print(f"📊 Total SKUs Canoniques Option A générés : {total_skus_created}")
    print(f"📁 Fichier Maître JSON : {master_db_path} ({master_db_path.stat().st_size / 1024 / 1024:.2f} Mo)")
    if csv_master_path.exists():
        print(f"📁 Fichier Maître CSV  : {csv_master_path} ({csv_master_path.stat().st_size / 1024:.1f} Ko, {len(csv_rows)} lignes)")
    print(f"📁 Fichier Index de Recherche : {index_path} ({index_path.stat().st_size / 1024:.1f} Ko)")
    print(f"📂 Documents Unitaires dans : {TARGET_DEVICES_DIR} ({len(list(TARGET_DEVICES_DIR.glob('*.json')))} fichiers)")
    print("=" * 80)


if __name__ == "__main__":
    main()
