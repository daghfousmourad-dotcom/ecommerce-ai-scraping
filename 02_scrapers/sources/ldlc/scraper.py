#!/usr/bin/env python3
"""
scraper_ldlc.py
===============
Scraper haute performance pour LDLC (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°23 (Priorité 1) du catalogue E-commerce AI Scraping, présent à Marseille Grand V & Plan de Campagne.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Smartphones Neufs : https://www.ldlc.com/telephonie/telephonie-portable/mobile-smartphone/c4416/
  - Smartphones Reconditionnés : https://www.ldlc.com/telephonie/telephonie-portable/smartphone-reconditionne/c7931/

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_ldlc.json
  2. db_ldlc.jsonl
  3. db_ldlc.csv
  4. db_ldlc_run.json
  5. db_ldlc_issues.jsonl
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
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT / "unit_scrapers"))
sys.path.insert(0, str(PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "ai_normalizer"))

try:
    from common_export import export_standard_artifacts
except ImportError:
    export_standard_artifacts = None

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

SOURCE_SITE = "ldlc.com"
BASE_URL = "https://www.ldlc.com"

CATEGORY_CONFIGS = [
    {
        "category_name": "new",
        "url_base": "https://www.ldlc.com/telephonie/telephonie-portable/mobile-smartphone/c4416/",
        "condition": "new",
        "label": "Smartphones Neufs"
    },
    {
        "category_name": "refurbished",
        "url_base": "https://www.ldlc.com/telephonie/telephonie-portable/smartphone-reconditionne/c7931/",
        "condition": "refurbished",
        "label": "Smartphones Reconditionnés"
    }
]

PRODUCT_SALE_KEYS = [
    "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
    "device_type", "device_family", "brand", "model", "model_normalized",
    "item_title", "storage_gb", "storage_raw", "ram_gb", "color", "condition",
    "product_id", "is_currently_sold", "price", "currency", "price_raw",
    "seller_name", "shipping_cost"
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scraper_ldlc")


def fetch_url(url: str, retries: int = 3, timeout: int = 25) -> Optional[str]:
    """Effectue une requête HTTP GET robuste avec gestion des reprises."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="ignore")
                logger.warning(f"HTTP {resp.status} sur {url} (tentative {attempt}/{retries})")
        except urllib.error.HTTPError as e:
            logger.warning(f"Erreur HTTP {e.code} sur {url} (tentative {attempt}/{retries})")
            if e.code in (404, 410):
                return None
        except Exception as e:
            logger.warning(f"Erreur réseau sur {url} : {e} (tentative {attempt}/{retries})")
        
        time.sleep(attempt * 1.5)
    return None


def extract_injected_prices(html: str) -> Dict[str, Tuple[float, str]]:
    """
    Extrait la table de correspondance offer_id -> (prix_float, prix_brut_str)
    depuis les scripts dynamiques injectés par LDLC.
    """
    prices: Dict[str, Tuple[float, str]] = {}
    
    # Motif exact identifié sur LDLC :
    # data-offer-id="AR202209080109"]'); pdtElements.forEach( ... .outerHTML = '<div class="price"><div class="price">899€<sup>00</sup></div></div>';
    matches = re.findall(
        r'data-offer-id=\"([A-Za-z0-9_]+)\"\]\'\);\s*pdtElements\.forEach\(.*?\.outerHTML\s*=\s*\'(.*?(?:€|<div class=\"price\").*?)\';',
        html,
        re.DOTALL
    )
    for offer_id, price_html in matches:
        clean = re.sub(r'<[^>]+>', ' ', price_html).replace('&nbsp;', '').replace(' ', '').strip()
        m = re.search(r'(\d+)[€\.](\d*)', clean)
        if m:
            int_part = m.group(1)
            dec_part = m.group(2) if m.group(2) else "00"
            try:
                price_val = round(float(f"{int_part}.{dec_part}"), 2)
                raw_val = f"{int_part}.{dec_part} €"
                prices[offer_id] = (price_val, raw_val)
            except ValueError:
                pass
    return prices


def extract_product_attributes(
    item_soup: BeautifulSoup,
    prices_map: Dict[str, Tuple[float, str]],
    condition_str: str,
    scrape_started_at: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Parse et normalise un produit unitaire depuis sa balise li.pdt-item."""
    offer_id = item_soup.get("data-offer-id") or item_soup.get("data-id") or ""
    
    title_el = item_soup.select_one(".title-3 a")
    if not title_el:
        return None, None
    
    raw_title = title_el.get_text(strip=True)
    rel_url = title_el.get("href", "")
    prod_url = urllib.parse.urljoin(BASE_URL, rel_url)
    
    # ID produit (ex: PB00516137)
    prod_id_match = re.search(r'(PB\d+)', rel_url)
    product_id = prod_id_match.group(1) if prod_id_match else offer_id
    
    desc_el = item_soup.select_one(".pdt-desc .desc")
    desc_text = desc_el.get_text(strip=True) if desc_el else ""
    full_text = f"{raw_title} {desc_text}"

    # Vérification du prix
    if offer_id not in prices_map:
        return None, {
            "error_type": "missing_price",
            "message": f"Prix absent pour l'offre {offer_id} ({raw_title})",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    price_val, price_raw = prices_map[offer_id]
    
    # 1. Extraction et validation de la marque
    first_token = raw_title.split()[0] if raw_title else ""
    brand_candidate = first_token
    if raw_title.lower().startswith("sony ericsson"):
        brand_candidate = "Sony"
    elif raw_title.lower().startswith("redmi") or raw_title.lower().startswith("poco"):
        brand_candidate = "Xiaomi"
    
    if validate_and_canonicalize_brand:
        brand = validate_and_canonicalize_brand(brand_candidate, raw_title, desc_text)
    else:
        brand = brand_candidate.capitalize()

    if not brand:
        return None, {
            "error_type": "invalid_brand",
            "message": f"Marque non reconnue pour {raw_title}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    # 2. Extraction du stockage (ex: 128 Go, 512 Go, 1 To)
    storage_gb: Optional[int] = None
    storage_raw: Optional[str] = None
    storage_match = re.search(r'\b(\d+)\s*(Go|To|GB|TB)\b', full_text, re.IGNORECASE)
    if storage_match:
        val = int(storage_match.group(1))
        unit = storage_match.group(2).upper()
        if unit in ("TO", "TB"):
            storage_gb = val * 1024
            storage_raw = f"{val} To"
        else:
            storage_gb = val
            storage_raw = f"{val} Go"

    # 3. Extraction de la mémoire vive RAM
    ram_gb: Optional[int] = None
    ram_match = re.search(r'\bRAM\s*(\d+)\s*(?:Go|GB)\b', desc_text, re.IGNORECASE)
    if ram_match:
        ram_gb = int(ram_match.group(1))

    # 4. Extraction et nettoyage de la couleur
    color: Optional[str] = None
    color_cleanup = raw_title
    color_cleanup = re.sub(r'\s*-\s*Reconditionné.*$', '', color_cleanup, flags=re.IGNORECASE)
    color_cleanup = re.sub(r'[\(\[]?[A-Z0-9]{4,}/[A-Z0-9]{1,}[\)\]]?', '', color_cleanup)  # Codes réf Apple ex (MHDD3F/A)
    color_cleanup = re.sub(r'\b[A-Z0-9]{5,}\b', '', color_cleanup)
    
    if storage_raw and storage_raw in color_cleanup:
        parts = color_cleanup.split(storage_raw)
        if len(parts) > 1 and parts[1].strip():
            raw_c = parts[1].strip().strip("- ")
            # Nettoyer les parenthèses résiduelles
            raw_c = re.sub(r'[\(\)]', '', raw_c).strip()
            if raw_c:
                color = raw_c
    elif "(PRODUCT)RED" in raw_title:
        color = "(PRODUCT)RED"

    # 5. Extraction du modèle épuré et du modèle normalisé
    clean_model = raw_title
    if clean_model.lower().startswith(brand.lower()):
        clean_model = clean_model[len(brand):].strip()
    
    clean_model = re.sub(r'\s*-\s*Reconditionné.*$', '', clean_model, flags=re.IGNORECASE)
    clean_model = re.sub(r'[\(\[]?[A-Z0-9]{4,}/[A-Z0-9]{1,}[\)\]]?', '', clean_model)
    clean_model = re.sub(r'\(PRODUCT\)RED', '', clean_model, flags=re.IGNORECASE)
    if storage_raw:
        clean_model = re.sub(re.escape(storage_raw), '', clean_model, flags=re.IGNORECASE)
    if color:
        # Enlever la couleur du modèle en respectant les limites de mots
        clean_model = re.sub(r'\b' + re.escape(color) + r'\b', '', clean_model, flags=re.IGNORECASE)
    clean_model = re.sub(r'\s+', ' ', clean_model).strip(" -")
    
    if canonicalize_model_name:
        model_normalized = canonicalize_model_name(brand, clean_model) or clean_model
    else:
        model_normalized = clean_model

    # 6. Validation du prix selon les règles d'intégrité E-commerce AI Scraping
    if is_valid_sale_price:
        valid_price, reason = is_valid_sale_price(price_val, brand, clean_model, raw_title)
        if not valid_price:
            return None, {
                "error_type": "invalid_price_floor",
                "message": f"Prix non conforme ({reason}) pour {raw_title}",
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
        "model": clean_model,
        "model_normalized": model_normalized,
        "item_title": raw_title,
        "storage_gb": storage_gb,
        "storage_raw": storage_raw,
        "ram_gb": ram_gb,
        "color": color,
        "condition": condition_str,
        "product_id": product_id,
        "is_currently_sold": True,
        "price": price_val,
        "currency": "EUR",
        "price_raw": price_raw,
        "seller_name": "LDLC (Marseille & National)",
        "shipping_cost": "0.0 € (Retrait 1h en magasin LDLC Marseille / Livraison offerte)",
    }
    return row, None


def scrape_category(
    cat_conf: Dict[str, Any],
    limit_pages: Optional[int],
    scrape_started_at: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Crawl toutes les pages d'une catégorie LDLC."""
    category_name = cat_conf["category_name"]
    base_cat_url = cat_conf["url_base"]
    condition_str = cat_conf["condition"]
    label = cat_conf["label"]

    logger.info(f"🚀 Démarrage du scraping pour : {label} ({base_cat_url})")

    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    page_num = 1
    current_url = base_cat_url

    while current_url:
        if limit_pages and page_num > limit_pages:
            logger.info(f"⏹️ Limite de {limit_pages} pages atteinte pour {label}")
            break

        logger.info(f"📄 Scraping {label} — Page {page_num} : {current_url}")
        html = fetch_url(current_url)
        if not html:
            logger.error(f"❌ Impossible de charger la page {current_url}")
            issues.append({
                "error_type": "network_fetch_failed",
                "message": f"Échec de chargement HTTP de la page {page_num} ({current_url})",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            break

        # 1. Extraction des prix injectés
        prices_map = extract_injected_prices(html)
        logger.info(f"   ↳ {len(prices_map)} prix extraits depuis les scripts de la page {page_num}")

        # 2. Parsing des produits
        soup = BeautifulSoup(html, "html.parser")
        pdt_items = soup.select("li.pdt-item")
        if not pdt_items:
            logger.info(f"   ↳ Aucun produit li.pdt-item trouvé sur la page {page_num}, fin de pagination.")
            break

        page_valid_count = 0
        for it in pdt_items:
            row, issue = extract_product_attributes(it, prices_map, condition_str, scrape_started_at)
            if row:
                rows.append(row)
                page_valid_count += 1
            elif issue:
                issues.append(issue)

        logger.info(f"   ↳ Page {page_num} traitée : {page_valid_count}/{len(pdt_items)} offres qualifiées (Cumul : {len(rows)})")

        # 3. Découverte de la page suivante via la pagination LDLC
        next_link = soup.select_one(".pagination li.next a")
        if next_link and next_link.get("href"):
            next_href = next_link.get("href")
            current_url = urllib.parse.urljoin(BASE_URL, next_href)
            page_num += 1
            pause = random.uniform(1.2, 2.5)
            time.sleep(pause)
        else:
            logger.info(f"🏁 Dernière page atteinte pour {label} (Total pages parcourues : {page_num})")
            break

    return rows, issues


def run_ldlc_scraper(
    limit_pages: Optional[int] = None,
    category_filter: Optional[str] = None,
    output_dir: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Exécute le scraping complet de LDLC et produit les 5 artefacts standardisés."""
    scrape_started_at = datetime.now(timezone.utc).isoformat()
    out_dir = Path(output_dir) if output_dir else CURRENT_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []

    for cat_conf in CATEGORY_CONFIGS:
        if category_filter and category_filter != "all" and cat_conf["category_name"] != category_filter:
            continue
        rows, issues = scrape_category(cat_conf, limit_pages, scrape_started_at)
        all_rows.extend(rows)
        all_issues.extend(issues)

    scrape_finished_at = datetime.now(timezone.utc).isoformat()

    logger.info("=" * 60)
    logger.info(f"🎉 Scraping LDLC Terminé !")
    logger.info(f"   - Total Offres Qualifiées : {len(all_rows)}")
    logger.info(f"   - Total Anomalies Notées : {len(all_issues)}")

    # Déduplication par product_id + price pour garantir une propreté absolue
    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for r in all_rows:
        key = (r["product_id"], r["price"], r["condition"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    logger.info(f"   - Offres uniques après déduplication : {len(unique_rows)}")

    # Export standardisé des 5 artefacts E-commerce AI V2
    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="ldlc",
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
        csv_file = out_dir / "db_ldlc.csv"
        import csv
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        logger.info(f"   ✓ CSV exporté : {csv_file}")

    # Synchronisation des fichiers dans les répertoires miroirs
    mirror_dirs = [
        PROJECT_ROOT / "dataset",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for mdir in mirror_dirs:
        try:
            mdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_ldlc*"):
                shutil.copy2(f, mdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés vers : {mdir}")
        except Exception as e:
            logger.warning(f"Erreur copie vers {mdir}: {e}")

    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper LDLC Smartphones Neufs & Reconditionnés (E-commerce AI V2)")
    parser.add_argument("--limit-pages", type=int, default=None, help="Limite de pages par catégorie pour test")
    parser.add_argument("--category", choices=["all", "new", "refurbished"], default="all", help="Filtrer la catégorie")
    parser.add_argument("--output-dir", type=str, default=None, help="Répertoire de sortie des artefacts")
    args = parser.parse_args()

    run_ldlc_scraper(
        limit_pages=args.limit_pages,
        category_filter=args.category,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
