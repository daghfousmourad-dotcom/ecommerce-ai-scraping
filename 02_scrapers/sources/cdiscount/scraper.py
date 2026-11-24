#!/usr/bin/env python3
"""
scraper_cdiscount.py
====================
Scraper haute performance pour Cdiscount (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°25 (Priorité 1) de la cartographie E-commerce AI Scraping.
Leader français du e-commerce avec livraison express 24-48h sur Marseille.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Catalogue complet Téléphonie / Smartphones :
    https://www.cdiscount.com/telephonie/telephone-mobile/l-14404.html (Pages 1 à N)
  - Différenciation stricte Neuf vs Reconditionné conformément au cahier des charges

Génère 5 fichiers d'artefacts standardisés dans ./outputs/ :
  1. db_cdiscount.json
  2. db_cdiscount.jsonl
  3. db_cdiscount.csv
  4. db_cdiscount_run.json
  5. db_cdiscount_issues.jsonl
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
import csv
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.sync_api import sync_playwright

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
        is_valid_smartphone_device,
    )
except ImportError:
    validate_and_canonicalize_brand = None
    is_valid_sale_price = None
    canonicalize_model_name = None
    is_valid_smartphone_device = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scraper_cdiscount")

SOURCE_SITE = "cdiscount.com"
BASE_URL = "https://www.cdiscount.com"
CATALOG_BASE_URL = "https://www.cdiscount.com/telephonie/telephone-mobile/l-14404"
DEFAULT_SHIPPING = "Livraison express 24-48h région Marseille"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
]

STORAGE_RE = re.compile(r'(\d+)\s*(?:Go|GB|To|TB)', re.I)
RAM_RE = re.compile(r'(\d+)\s*(?:Go|GB)\s*(?:de\s*)?RAM', re.I)

KNOWN_COLORS = [
    'noir sidéral', 'gris sidéral', 'bleu intense', 'bleu marine', 'blanc nuage',
    'titane naturel', 'titane blanc', 'titane noir', 'titane bleu', 'titane désert',
    'bleu pacifique', 'bleu nuit', 'bleu glacier', 'bleu crépuscule', 'bleu ciel',
    'vert nuit', 'vert forêt', 'vert sauge', 'vert d\'eau', 'vert',
    'noir volcanique', 'noir prisme', 'noir mat', 'noir brillant', 'noir',
    'blanc perle', 'blanc céramique', 'blanc prisme', 'blanc',
    'argent', 'silver', 'or rose', 'rose gold', 'or', 'gold', 'jaune', 'rouge',
    'rose poudré', 'rose', 'violet', 'pourpre', 'mauve', 'lavande', 'corail',
    'minuit', 'lumière stellaire', 'starlight', 'midnight', 'outremer',
    'graphite', 'crème', 'fantôme', 'phantom black', 'phantom white',
    'obsidienne', 'porcelaine', 'baie', 'noisette', 'menthe', 'cosmic orange',
    'orange', 'nacré', 'nacre'
]


def extract_attributes(title: str, raw_brand: str) -> Tuple[str, str, Optional[int], str, Optional[int]]:
    """Extrait le modèle, la couleur, le stockage et la RAM d'un titre Cdiscount."""
    cleaned = title.strip()

    # 1. Extraction RAM
    ram_gb = None
    rm = RAM_RE.search(cleaned)
    if rm:
        ram_gb = int(rm.group(1))

    # Retrait de la RAM pour éviter les collisions avec le stockage
    title_no_ram = RAM_RE.sub('', cleaned)

    # 2. Extraction Stockage
    storage_gb = None
    storage_raw = ""
    sm = STORAGE_RE.search(title_no_ram)
    if sm:
        storage_raw = sm.group(0).strip()
        val = int(sm.group(1))
        if 'to' in storage_raw.lower() or 'tb' in storage_raw.lower():
            val = val * 1024
        storage_gb = val

    before_storage = title_no_ram[:sm.start()].strip() if sm else title_no_ram
    after_storage = title_no_ram[sm.end():].strip() if sm else ""

    # Retrait de la marque en tête
    brand_regex = re.compile(rf"^{re.escape(raw_brand)}\s+", re.I)
    rest = brand_regex.sub("", before_storage).strip()
    rest = re.sub(r"^(?:Smartphone|Téléphone mobile)\s+", "", rest, flags=re.I).strip()
    rest = re.sub(r"\s+d[ée]bloqu[ée]\b", "", rest, flags=re.I).strip()
    rest = re.sub(r"\s+5G\b", "", rest, flags=re.I).strip()

    # 3. Extraction Couleur (recherche dans after_storage puis before_storage)
    color = ""
    if after_storage:
        for col in sorted(KNOWN_COLORS, key=len, reverse=True):
            if col in after_storage.lower():
                color = col.capitalize()
                break

    if not color:
        lower_rest = rest.lower()
        for col in sorted(KNOWN_COLORS, key=len, reverse=True):
            if lower_rest.endswith(col):
                color = rest[-len(col):].strip()
                rest = rest[:-len(col)].strip()
                break
        else:
            parts = rest.rsplit(" ", 1)
            if len(parts) == 2 and not parts[1].isdigit() and parts[1].lower() not in ["max", "pro", "plus", "ultra", "lite", "fe", "mini"]:
                rest = parts[0]
                color = parts[1]

    model_raw = rest.strip()
    return model_raw, color, storage_gb, storage_raw, ram_gb

def scrape_page(page, page_num: int, scrape_started_at: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape une page Cdiscount et extrait les produits et leurs statuts Neuf/Reconditionné."""
    url = f"{CATALOG_BASE_URL}.html" if page_num == 1 else f"{CATALOG_BASE_URL}-{page_num}.html"
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        logger.warning(f"Timeout ou erreur sur {url}: {e}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e2:
            logger.error(f"Échec définitif du chargement de {url}: {e2}")
            return [], [{"source_url": url, "reason": str(e2)}]

    # 1. Extraction des liens produits et de l'indicateur Reconditionné depuis le DOM
    card_data = page.eval_on_selector_all('a[href*="/f-"]', '''elements => elements.map(e => {
        const text = e.innerText || "";
        const href = e.href || "";
        const isRecond = /reconditionn|occasion|2nd life/i.test(text) || /reconditionne|rcd/i.test(href);
        const isCdiscountDirect = /livré par cdiscount/i.test(text);
        return {
            href: href.split("#")[0],
            isRecond: isRecond,
            isCdiscountDirect: isCdiscountDirect
        };
    })''')

    recond_map = {c['href']: c['isRecond'] for c in card_data if c.get('href')}
    seller_map = {c['href']: "Cdiscount" if c.get('isCdiscountDirect') else "Vendeur Marketplace Cdiscount" for c in card_data if c.get('href')}

    # 2. Extraction du JSON-LD structuré
    scripts = page.eval_on_selector_all('script[type="application/ld+json"]', 'elements => elements.map(e => e.innerText)')
    json_products: List[Dict[str, Any]] = []
    for s in scripts:
        try:
            d = json.loads(s)
            if d.get('@type') == 'CollectionPage':
                json_products = d.get('mainEntity', {}).get('itemListElement', [])
                break
        except Exception:
            pass

    if not json_products:
        logger.warning(f"Aucun produit JSON-LD trouvé sur la page {page_num} ({url})")
        return [], [{"source_url": url, "reason": "JSON-LD CollectionPage introuvable"}]

    for item in json_products:
        item_title = item.get('name', '')
        if not item_title:
            continue

        offers = item.get('offers', {})
        price = offers.get('price')
        try:
            price = float(price) if price is not None else None
        except (ValueError, TypeError):
            price = None

        product_url = offers.get('url', '')
        if product_url.startswith('/'):
            product_url = f"{BASE_URL}{product_url}"
        clean_url = product_url.split('#')[0]

        sku = item.get('sku', '') or clean_url

        # Détection Neuf vs Reconditionné
        is_recond = recond_map.get(clean_url, False)
        if not is_recond:
            is_recond = bool(re.search(r'reconditionn|occasion|rcd', item_title, re.I) or re.search(r'rcd|recond', clean_url, re.I))

        condition = "reconditionne" if is_recond else "neuf"
        seller_name = seller_map.get(clean_url, "Cdiscount")

        # Marque brute
        raw_brand = item_title.split()[0]
        if raw_brand.lower() in ['smartphone', 'téléphone', 'mobile'] and len(item_title.split()) > 1:
            raw_brand = item_title.split()[1]

        # Normalisation canonique
        brand_canon = raw_brand.capitalize()
        if validate_and_canonicalize_brand:
            v_brand = validate_and_canonicalize_brand(raw_brand, None, item_title)
            if v_brand:
                brand_canon = v_brand

        model_raw, color, storage_gb, storage_raw, ram_gb = extract_attributes(item_title, raw_brand)

        model_canon = model_raw
        if canonicalize_model_name:
            v_model = canonicalize_model_name(brand_canon, model_raw)
            if v_model:
                model_canon = v_model

        # Validation terminal smartphone
        if is_valid_smartphone_device:
            valid, reason = is_valid_smartphone_device(brand_canon, model_canon, item_title)
            if not valid:
                issues.append({
                    "source_url": clean_url,
                    "item_title": item_title,
                    "brand": brand_canon,
                    "model": model_canon,
                    "price": price,
                    "reason": f"Rejet taxonomie terminal : {reason}"
                })
                continue

        # Validation tarifaire
        if is_valid_sale_price:
            p_valid, p_reason = is_valid_sale_price(price, brand_canon, model_canon, item_title)
            if not p_valid:
                issues.append({
                    "source_url": clean_url,
                    "item_title": item_title,
                    "brand": brand_canon,
                    "model": model_canon,
                    "price": price,
                    "reason": f"Rejet tarifaire : {p_reason}"
                })
                continue

        # Famille de produits
        device_family = "Smartphone"
        if brand_canon.lower() == "apple":
            device_family = "iPhone"
        elif brand_canon.lower() == "samsung":
            if "galaxy s" in model_canon.lower():
                device_family = "Galaxy S"
            elif "galaxy z" in model_canon.lower():
                device_family = "Galaxy Z"
            elif "galaxy a" in model_canon.lower():
                device_family = "Galaxy A"
            else:
                device_family = "Galaxy"
        elif brand_canon.lower() == "google":
            device_family = "Pixel"
        elif brand_canon.lower() == "xiaomi":
            device_family = "Xiaomi / Redmi"

        row = {
            "source_site": SOURCE_SITE,
            "source_url": clean_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scrape_started_at": scrape_started_at,
            "data_type": "product_sale",
            "device_type": "smartphone",
            "device_family": device_family,
            "brand": brand_canon,
            "model": model_raw,
            "model_normalized": model_canon,
            "item_title": item_title,
            "storage_gb": storage_gb,
            "storage_raw": storage_raw,
            "ram_gb": ram_gb,
            "color": color,
            "condition": condition,
            "product_id": sku,
            "is_currently_sold": True,
            "price": price,
            "currency": "EUR",
            "price_raw": f"{price:.2f} €" if price else "",
            "seller_name": seller_name,
            "shipping_cost": DEFAULT_SHIPPING
        }
        rows.append(row)

    return rows, issues


def run_cdiscount_scraper(
    max_pages: int = 15,
    output_dir: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Exécute la collecte Playwright sur Cdiscount avec export multi-miroirs."""
    scrape_started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 70)
    logger.info("⚡ Lancement du Scraper Cdiscount France (Neuf & Reconditionné)")
    logger.info(f"   - Horodatage début : {scrape_started_at}")
    logger.info(f"   - Pages cibles : 1 à {max_pages} (~{max_pages * 47} offres attendues)")
    logger.info("=" * 70)

    out_dir = Path(output_dir) if output_dir else CURRENT_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1440, "height": 900}
        )
        page = context.new_page()

        for page_num in range(1, max_pages + 1):
            logger.info(f"⏳ Collecte de la page {page_num}/{max_pages} sur Cdiscount...")
            t0 = time.time()
            p_rows, p_issues = scrape_page(page, page_num, scrape_started_at)
            all_rows.extend(p_rows)
            all_issues.extend(p_issues)
            elapsed = time.time() - t0
            logger.info(f"   ✓ Page {page_num} traitée en {elapsed:.2f}s : {len(p_rows)} offres qualifiées (Cumul: {len(all_rows)})")
            # Pause respectueuse inter-pages
            time.sleep(random.uniform(0.6, 1.2))

        browser.close()

    scrape_finished_at = datetime.now(timezone.utc).isoformat()

    # Déduplication par (product_id, price, condition)
    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for r in all_rows:
        key = (r["product_id"], r["price"], r["condition"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    logger.info("=" * 70)
    logger.info("🎉 Scraping Cdiscount Terminé avec Succès !")
    logger.info(f"   - Total Offres Brutes Collectées : {len(all_rows)}")
    logger.info(f"   - Offres Uniques Qualifiées : {len(unique_rows)}")
    neuf_count = sum(1 for r in unique_rows if r["condition"] == "neuf")
    recond_count = sum(1 for r in unique_rows if r["condition"] == "reconditionne")
    logger.info(f"   - Répartition Condition : {neuf_count} Neuf | {recond_count} Reconditionné")
    logger.info(f"   - Total Rejets / Anomalies : {len(all_issues)}")
    logger.info("=" * 70)

    # Export des 5 artefacts standardisés E-commerce AI V2
    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="cdiscount",
            domain="product_sales",
            rows=unique_rows,
            output_dir=out_dir,
            issues=all_issues,
            scrape_started_at=scrape_started_at,
            scrape_finished_at=scrape_finished_at
        )
        logger.info(f"📁 5 Artefacts standardisés générés dans : {out_dir}")
        for k, path in files.items():
            logger.info(f"   ✓ {k}: {path}")
    else:
        csv_file = out_dir / "db_cdiscount.csv"
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        logger.info(f"   ✓ CSV exporté : {csv_file}")

    # Synchronisation vers les répertoires de staging UNIQUEMENT (AUCUN envoi vers dataset/)
    staging_dirs = [
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    mirror_dirs = staging_dirs
    for mdir in mirror_dirs:
        try:
            mdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_cdiscount*"):
                shutil.copy2(f, mdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés avec succès vers : {mdir}")
        except Exception as e:
            logger.warning(f"Erreur lors de la copie vers {mdir}: {e}")

    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper Cdiscount — Smartphones Neufs & Reconditionnés (E-commerce AI V2)")
    parser.add_argument("--pages", type=int, default=50, help="Nombre de pages à scraper (défaut: 15)")
    parser.add_argument("--output-dir", type=str, default=None, help="Dossier de sortie personnalisé")
    args = parser.parse_args()

    run_cdiscount_scraper(
        max_pages=args.pages,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
