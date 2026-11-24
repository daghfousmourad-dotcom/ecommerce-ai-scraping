#!/usr/bin/env python3
"""
scraper_electro_depot.py
========================
Scraper haute performance et résilient pour Electro Dépôt (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°24 (Priorité 1) de la cartographie E-commerce AI Scraping, présent à Marseille La Valentine (13011).

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Smartphones Neufs : https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/smartphone.html
  - Smartphones Reconditionnés : https://www.electrodepot.fr/smartphone-mobilite/smartphone-reconditionne.html
  - Mobiles & Seniors (Optionnel) : https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/telephone-mobile.html

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_electro_depot.json
  2. db_electro_depot.jsonl
  3. db_electro_depot.csv
  4. db_electro_depot_run.json
  5. db_electro_depot_issues.jsonl
"""

from __future__ import annotations

import sys
from pathlib import Path
_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / 'core'
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import concurrent.futures
import csv
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

# Configuration des chemins d'importation
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT / "unit_scrapers"))
sys.path.insert(0, str(PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "ai_normalizer"))

try:
    from common_export import export_standard_artifacts, PRODUCT_SALE_KEYS
except ImportError:
    export_standard_artifacts = None
    PRODUCT_SALE_KEYS = [
        "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
        "device_type", "device_family", "brand", "model", "model_normalized",
        "item_title", "storage_gb", "storage_raw", "ram_gb", "color", "condition",
        "product_id", "is_currently_sold", "price", "currency", "price_raw",
        "seller_name", "shipping_cost"
    ]

try:
    from device_validator import (
        validate_and_canonicalize_brand,
        is_valid_sale_price,
        canonicalize_model_name,
    )
except ImportError:
    validate_and_canonicalize_brand = None
    is_valid_sale_price = None
    canonicalize_model_name = None

# Configuration des Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scraper_electro_depot")

SOURCE_SITE = "electrodepot.fr"
BASE_URL = "https://www.electrodepot.fr"
STORE_MARSEILLE = "Electro Dépôt Marseille La Valentine (13011)"
SHIPPING_MARSEILLE = "0.0 € (Retrait 1h en magasin Marseille La Valentine / Livraison express)"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
]

CATEGORY_CONFIGS = [
    {
        "category_name": "new",
        "url_base": "https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/smartphone.html",
        "condition": "new",
        "label": "Smartphones Neufs"
    },
    {
        "category_name": "refurbished",
        "url_base": "https://www.electrodepot.fr/smartphone-mobilite/smartphone-reconditionne.html",
        "condition": "refurbished",
        "label": "Smartphones Reconditionnés"
    },
    {
        "category_name": "senior",
        "url_base": "https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/telephone-mobile.html",
        "condition": "new",
        "label": "Mobiles & Seniors"
    }
]

COLOR_KEYWORDS = [
    "noir", "black", "blanc", "white", "bleu", "blue", "rouge", "red", "vert", "green",
    "or", "gold", "argent", "silver", "gris", "grey", "gray", "rose", "pink", "violet",
    "purple", "jaune", "yellow", "titane", "titanium", "sable", "creme", "lavande", "corail"
]


def fetch_html(url: str, max_retries: int = 3, timeout: int = 15) -> Optional[str]:
    """Effectue une requête HTTP GET robuste avec rotation User-Agent et retries."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1"
    }

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return response.read().decode("utf-8", errors="replace")
                logger.warning(f"⚠️ Statut HTTP inattendu {response.status} pour {url}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.debug(f"ℹ️ Page 404 (fin de pagination probable) : {url}")
                return None
            logger.warning(f"⚠️ HTTPError {e.code} sur {url} (tentative {attempt}/{max_retries})")
        except Exception as e:
            logger.warning(f"⚠️ Erreur de connexion {e} sur {url} (tentative {attempt}/{max_retries})")

        time.sleep(1.0 * attempt + random.uniform(0.2, 0.6))

    return None


def extract_listing_cards(html: str) -> List[Dict[str, Any]]:
    """Extrait la liste des produits depuis la page catalogue Electro Dépôt."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".productlist-item")
    cards = []

    for it in items:
        # URL du produit
        href = it.get("href")
        if not href:
            continue
        if href.startswith("/"):
            href = f"{BASE_URL}{href}"
        elif not href.startswith("http"):
            continue

        # Marque affichée sur la carte
        brand_el = it.select_one(".productlist-item--brand")
        card_brand = brand_el.get_text(strip=True) if brand_el else ""

        # Nom du produit
        name_el = it.select_one(".productlist-item--name, h2")
        card_name = name_el.get_text(strip=True) if name_el else ""

        # Prix
        price_el = it.select_one(".number_price")
        card_price_raw = price_el.get_text(" ", strip=True) if price_el else ""

        # Caractéristiques rapides (Ecran, Stockage, Photo)
        card_desc = {}
        for li in it.select(".productlist-item--description li"):
            txt = li.get_text(strip=True)
            if ":" in txt:
                k, v = txt.split(":", 1)
                card_desc[k.strip().lower()] = v.strip()

        cards.append({
            "url": href,
            "card_brand": card_brand,
            "card_name": card_name,
            "card_price_raw": card_price_raw,
            "card_desc": card_desc
        })

    return cards


def enrich_product_details(card: Dict[str, Any], fallback_condition: str) -> Dict[str, Any]:
    """Récupère les détails enrichis depuis la fiche produit Electro Dépôt."""
    url = card["url"]
    html = fetch_html(url, max_retries=2, timeout=12)

    product_data = {
        "url": url,
        "title": card["card_name"],
        "brand": card["card_brand"],
        "model": "",
        "storage_gb": None,
        "storage_raw": "",
        "ram_gb": None,
        "color": None,
        "condition": fallback_condition,
        "price": None,
        "price_raw": card["card_price_raw"],
        "sku": url.split("/")[-1].replace(".html", "")
    }

    if not html:
        # Fallback sur les données de la carte de listing
        return _fallback_from_card(card, product_data, fallback_condition)

    soup = BeautifulSoup(html, "html.parser")

    # 1. Extraction du prix exact
    meta_price = soup.find("meta", property="product:price:amount")
    if meta_price and meta_price.get("content"):
        try:
            product_data["price"] = float(meta_price["content"].replace(",", "."))
            product_data["price_raw"] = f"{meta_price['content']} EUR"
        except ValueError:
            pass

    if product_data["price"] is None and card["card_price_raw"]:
        # Fallback prix carte
        clean_p = re.sub(r"[^\d,\.]", "", card["card_price_raw"]).replace(",", ".")
        try:
            product_data["price"] = float(clean_p)
        except ValueError:
            pass

    # 2. Titre complet
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        product_data["title"] = og_title["content"].strip()
    elif soup.find("h1"):
        product_data["title"] = soup.find("h1").get_text(strip=True)

    # 3. Table des caractéristiques techniques exhaustives
    specs: Dict[str, str] = {}
    for tr in soup.select("table tr, .data.table tr"):
        th = tr.find(["th", "td"])
        tds = tr.find_all("td")
        if th and len(tds) >= 1:
            specs[th.get_text(strip=True)] = tds[-1].get_text(strip=True)

    # Marque
    brand_spec = specs.get("Marque")
    if brand_spec:
        product_data["brand"] = brand_spec
    elif not product_data["brand"]:
        # Tenter d'extraire la marque du titre
        for known_b in ["Apple", "Samsung", "Xiaomi", "Honor", "Motorola", "Realme", "Oppo", "Vivo", "Google", "Nokia", "Doro"]:
            if known_b.lower() in product_data["title"].lower():
                product_data["brand"] = known_b
                break

    # Modèle
    model_spec = specs.get("Modèle")
    if model_spec:
        product_data["model"] = model_spec

    # Stockage
    storage_spec = specs.get("Stockage interne") or card["card_desc"].get("stockage", "")
    if storage_spec:
        product_data["storage_raw"] = storage_spec
        m_st = re.search(r"(\d+)\s*(Go|GB|To|TB)", storage_spec, re.IGNORECASE)
        if m_st:
            val = int(m_st.group(1))
            unit = m_st.group(2).lower()
            product_data["storage_gb"] = val * 1024 if "t" in unit else val

    # RAM
    ram_spec = specs.get("Mémoire vive RAM")
    if ram_spec:
        m_ram = re.search(r"(\d+)\s*(Go|GB)", ram_spec, re.IGNORECASE)
        if m_ram:
            product_data["ram_gb"] = int(m_ram.group(1))

    # Couleur
    color_spec = specs.get("Coloris")
    if color_spec:
        product_data["color"] = color_spec.strip().capitalize()
    else:
        # Recherche par mots clés dans le titre
        for col in COLOR_KEYWORDS:
            if re.search(r"\b" + re.escape(col) + r"\b", product_data["title"], re.IGNORECASE):
                product_data["color"] = col.capitalize()
                break

    # Condition (Neuf vs Reconditionné)
    etat_spec = specs.get("État", "")
    if "reconditionn" in etat_spec.lower() or "reconditionne" in url.lower() or fallback_condition == "refurbished":
        product_data["condition"] = "refurbished"
    else:
        product_data["condition"] = "new"

    # SKU / Code Article
    sku_spec = specs.get("Code article")
    if sku_spec:
        product_data["sku"] = sku_spec

    return product_data


def _fallback_from_card(card: Dict[str, Any], product_data: Dict[str, Any], fallback_condition: str) -> Dict[str, Any]:
    """Construit les attributs à partir des informations de la carte de listing."""
    title = card["card_name"]
    product_data["title"] = title
    product_data["brand"] = card["card_brand"]

    # Stockage depuis desc
    st = card["card_desc"].get("stockage", "")
    if st:
        product_data["storage_raw"] = st
        m_st = re.search(r"(\d+)\s*(Go|GB|To|TB)", st, re.IGNORECASE)
        if m_st:
            v = int(m_st.group(1))
            product_data["storage_gb"] = v * 1024 if "t" in m_st.group(2).lower() else v
    else:
        m_st = re.search(r"(\d+)\s*(Go|GB|To|TB)", title, re.IGNORECASE)
        if m_st:
            v = int(m_st.group(1))
            product_data["storage_raw"] = m_st.group(0)
            product_data["storage_gb"] = v * 1024 if "t" in m_st.group(2).lower() else v

    # Prix
    if card["card_price_raw"]:
        clean_p = re.sub(r"[^\d,\.]", "", card["card_price_raw"]).replace(",", ".")
        try:
            product_data["price"] = float(clean_p)
        except ValueError:
            pass

    # Couleur depuis titre
    for col in COLOR_KEYWORDS:
        if re.search(r"\b" + re.escape(col) + r"\b", title, re.IGNORECASE):
            product_data["color"] = col.capitalize()
            break

    product_data["condition"] = fallback_condition
    return product_data


def clean_and_normalize_row(
    prod: Dict[str, Any],
    scrape_started_at: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Nettoie, normalise et valide un produit selon le schéma canonique E-commerce AI Scraping."""
    raw_title = prod["title"]
    prod_url = prod["url"]
    price_val = prod["price"]

    # 1. Validation de l'existence du prix
    if price_val is None or price_val <= 0:
        return None, {
            "error_type": "missing_price",
            "message": f"Prix manquant ou nul pour {raw_title} ({prod_url})",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    # 2. Validation & Normalisation de la marque
    raw_brand = prod.get("brand") or ""
    raw_model = prod.get("model") or ""
    if validate_and_canonicalize_brand:
        brand = validate_and_canonicalize_brand(raw_brand, raw_model, raw_title)
    else:
        brand = raw_brand.capitalize() if raw_brand else ""

    if not brand:
        # Tentative d'extraction de la marque depuis le début du titre
        parts = raw_title.replace("Smartphone ", "").replace("Mobile ", "").split()
        if parts:
            b_candidate = parts[0]
            if validate_and_canonicalize_brand:
                brand = validate_and_canonicalize_brand(b_candidate, raw_model, raw_title)
            if not brand:
                brand = b_candidate.capitalize()
    if not brand and raw_brand:
        brand = raw_brand.capitalize()

    if not brand:
        return None, {
            "error_type": "unknown_brand",
            "message": f"Marque indéterminée pour le titre : {raw_title}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    # 3. Extraction et nettoyage du modèle
    model = prod["model"]
    if not model:
        # Nettoyage du titre pour dériver le modèle
        m_clean = raw_title
        m_clean = re.sub(r"^(?:Smartphone|Mobile|Téléphone)\s+", "", m_clean, flags=re.IGNORECASE)
        if m_clean.lower().startswith(brand.lower()):
            m_clean = m_clean[len(brand):].strip()
        # Supprimer le stockage et la couleur
        if prod["storage_raw"]:
            m_clean = re.sub(re.escape(prod["storage_raw"]), "", m_clean, flags=re.IGNORECASE)
        if prod["color"]:
            m_clean = re.sub(r"\b" + re.escape(prod["color"]) + r"\b", "", m_clean, flags=re.IGNORECASE)
        m_clean = re.sub(r"\b(?:Reconditionn[ée]|grade\s+[^\s]+|5G|4G)\b", "", m_clean, flags=re.IGNORECASE)
        model = re.sub(r"\s+", " ", m_clean).strip(" -")

    # Harmonisation des dénominations de modèles
    if brand == "Samsung" and not model.lower().startswith("galaxy") and "galaxy" in raw_title.lower():
        model = f"Galaxy {model}"
    elif brand == "Apple" and not model.lower().startswith("iphone"):
        model = f"iPhone {model}"
    elif brand == "Apple" and model.lower().startswith("iphone"):
        model = re.sub(r"^iphone\s*", "iPhone ", model, flags=re.IGNORECASE)

    # Modèle normalisé canonique
    if canonicalize_model_name:
        model_normalized = canonicalize_model_name(brand, model) or model
    else:
        model_normalized = model

    # 4. Validation du plancher de prix
    if is_valid_sale_price:
        valid_price, reason = is_valid_sale_price(price_val, brand, model, raw_title)
        if not valid_price:
            return None, {
                "error_type": "invalid_price_floor",
                "message": f"Prix non conforme ({reason}) pour {raw_title} (prix: {price_val}€)",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    row = {
        "source_site": SOURCE_SITE,
        "source_url": prod_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scrape_started_at": scrape_started_at,
        "data_type": "product_sale",
        "device_type": "smartphone",
        "device_family": "smartphone",
        "brand": brand,
        "model": model,
        "model_normalized": model_normalized,
        "item_title": raw_title,
        "storage_gb": prod["storage_gb"],
        "storage_raw": prod["storage_raw"],
        "ram_gb": prod["ram_gb"],
        "color": prod["color"],
        "condition": prod["condition"],
        "product_id": f"electrodepot_{prod['sku']}",
        "is_currently_sold": True,
        "price": price_val,
        "currency": "EUR",
        "price_raw": prod["price_raw"],
        "seller_name": STORE_MARSEILLE,
        "shipping_cost": SHIPPING_MARSEILLE,
    }
    return row, None


def scrape_category(
    cat_conf: Dict[str, Any],
    limit_pages: Optional[int],
    workers: int,
    scrape_started_at: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape toutes les pages d'une catégorie et enrichit les fiches produits."""
    label = cat_conf["label"]
    base_url = cat_conf["url_base"]
    fallback_cond = cat_conf["condition"]

    logger.info(f"🚀 Début de l'extraction catalogue : {label} ({base_url})")

    cards_to_enrich: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    issues: List[Dict[str, Any]] = []

    page = 1
    while True:
        if limit_pages and page > limit_pages:
            logger.info(f"⏹️ Limite de {limit_pages} page(s) atteinte pour {label}")
            break

        cat_url = f"{base_url}?page={page}" if page > 1 else base_url
        logger.info(f"📄 Scraping {label} — Page {page} : {cat_url}")

        html = fetch_html(cat_url)
        if not html:
            logger.info(f"ℹ️ Page {page} introuvable ou inaccessible, fin de pagination pour {label}")
            break

        cards = extract_listing_cards(html)
        if not cards:
            logger.info(f"ℹ️ Aucun produit trouvé sur la page {page}, fin de pagination pour {label}")
            break

        new_cards = [c for c in cards if c["url"] not in seen_urls]
        logger.info(f"   ↳ {len(cards)} cartes trouvées ({len(new_cards)} nouvelles)")

        if not new_cards:
            logger.info(f"🔄 Redirection ou boucle détectée à la page {page}, arrêt de pagination pour {label}")
            break

        for c in new_cards:
            seen_urls.add(c["url"])
            cards_to_enrich.append(c)

        page += 1
        time.sleep(random.uniform(0.3, 0.7))

    logger.info(f"🔍 {len(cards_to_enrich)} produits uniques identifiés pour {label}. Démarrage de l'enrichissement...")

    rows: List[Dict[str, Any]] = []

    # Enrichissement multi-threadé avec pool contrôlé
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_card = {
            executor.submit(enrich_product_details, card, fallback_cond): card
            for card in cards_to_enrich
        }

        completed = 0
        for future in concurrent.futures.as_completed(future_to_card):
            completed += 1
            if completed % 25 == 0 or completed == len(cards_to_enrich):
                logger.info(f"   ↳ Enrichissement en cours : {completed}/{len(cards_to_enrich)} ({label})")

            try:
                prod_data = future.result()
                row, issue = clean_and_normalize_row(prod_data, scrape_started_at)
                if row:
                    rows.append(row)
                elif issue:
                    issues.append(issue)
            except Exception as exc:
                card = future_to_card[future]
                logger.error(f"❌ Exception lors de l'enrichissement de {card['url']}: {exc}")
                issues.append({
                    "error_type": "enrichment_exception",
                    "message": str(exc),
                    "url": card["url"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

    logger.info(f"✅ {label} : {len(rows)} offres qualifiées extraites ({len(issues)} rejets).")
    return rows, issues


def run_electro_depot_scraper(
    limit_pages: Optional[int] = None,
    category_filter: str = "all",
    workers: int = 6,
    output_dir: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Exécute le scraping complet d'Electro Dépôt et exporte les artefacts standards."""
    scrape_started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 65)
    logger.info("⚡ Lancement du Scraper Electro Dépôt Marseille (Neuf & Recond.)")
    logger.info(f"   - Horodatage : {scrape_started_at}")
    logger.info(f"   - Limite pages : {limit_pages or 'Illimité (Catalogue complet)'}")
    logger.info(f"   - Filtre Catégorie : {category_filter}")
    logger.info(f"   - Workers : {workers}")
    logger.info("=" * 65)

    out_dir = Path(output_dir) if output_dir else CURRENT_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    configs_to_run = CATEGORY_CONFIGS
    if category_filter != "all":
        configs_to_run = [c for c in CATEGORY_CONFIGS if c["category_name"] == category_filter]

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []

    for conf in configs_to_run:
        rows, issues = scrape_category(conf, limit_pages, workers, scrape_started_at)
        all_rows.extend(rows)
        all_issues.extend(issues)

    scrape_finished_at = datetime.now(timezone.utc).isoformat()

    # Déduplication par (product_id, price, condition)
    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for r in all_rows:
        key = (r["product_id"], r["price"], r["condition"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    logger.info("=" * 65)
    logger.info(f"🎉 Scraping Electro Dépôt Terminé avec Succès !")
    logger.info(f"   - Total Offres Collectées : {len(all_rows)}")
    logger.info(f"   - Offres Uniques Qualifiées : {len(unique_rows)}")
    logger.info(f"   - Total Anomalies Traitées : {len(all_issues)}")

    # Export des 5 artefacts E-commerce AI V2
    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="electro_depot",
            domain="product_sales",
            rows=unique_rows,
            output_dir=out_dir,
            issues=all_issues,
            scrape_started_at=scrape_started_at,
            scrape_finished_at=scrape_finished_at
        )
        logger.info(f"📁 5 Artefacts standardisés générés dans : {out_dir}")
        for k, p in files.items():
            logger.info(f"   ✓ {k}: {p}")
    else:
        csv_file = out_dir / "db_electro_depot.csv"
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        logger.info(f"   ✓ CSV exporté : {csv_file}")

    # Synchronisation des fichiers dans les répertoires miroirs
    # Synchronisation des fichiers dans les répertoires miroirs de staging (PAS dataset/ selon consigne)
    mirror_dirs = [
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for mdir in mirror_dirs:
        try:
            mdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_electro_depot*"):
                shutil.copy2(f, mdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés vers : {mdir}")
        except Exception as e:
            logger.warning(f"Erreur copie vers {mdir}: {e}")

    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper Electro Dépôt Marseille — Smartphones Neufs & Reconditionnés (E-commerce AI V2)")
    parser.add_argument("--limit-pages", type=int, default=None, help="Limite de pages par catégorie")
    parser.add_argument("--category", choices=["all", "new", "refurbished", "senior"], default="all", help="Filtrer par catégorie")
    parser.add_argument("--workers", type=int, default=6, help="Nombre de threads d'enrichissement simultanés")
    parser.add_argument("--output-dir", type=str, default=None, help="Dossier d'exportation des artefacts")
    args = parser.parse_args()

    run_electro_depot_scraper(
        limit_pages=args.limit_pages,
        category_filter=args.category,
        workers=args.workers,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
