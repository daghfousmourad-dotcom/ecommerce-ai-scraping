#!/usr/bin/env python3
"""
scraper_easy_cash.py
====================
Scraper haute performance et résilient pour Easy Cash (Smartphones Reconditionnés & Occasion Garantie).
Cible : Enseigne N°13 (Priorité 1) de la cartographie E-commerce AI Scraping.
Réseau national leader de l'achat-vente d'occasion avec garantie 2 ans et desserte région Marseille.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Catalogue complet Téléphonie / Smartphones :
    https://bons-plans.easycash.fr/telephonie/smartphones?filterType=searchResults

Génère 5 fichiers d'artefacts standardisés dans ./outputs/ :
  1. db_easy_cash.json
  2. db_easy_cash.jsonl
  3. db_easy_cash.csv
  4. db_easy_cash_run.json
  5. db_easy_cash_issues.jsonl
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
        is_valid_smartphone_device,
    )
except ImportError:
    validate_and_canonicalize_brand = None
    is_valid_sale_price = None
    canonicalize_model_name = None
    is_valid_smartphone_device = None

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scraper_easy_cash")

SOURCE_SITE = "easycash.fr"
BASE_URL = "https://bons-plans.easycash.fr"
CATALOG_URL = "https://bons-plans.easycash.fr/telephonie/smartphones?filterType=searchResults"
SELLER_NAME = "Easy Cash"
DEFAULT_WARRANTY = "Garantie 2 ans Easy Cash"
DEFAULT_SHIPPING = "Livraison express 24-48h région Marseille / Retrait magasin"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0"
]

STORAGE_RE = re.compile(r'(\d+)\s*(?:Go|GB|To|TB)', re.I)
RAM_RE = re.compile(r'(\d+)\s*(?:Go|GB)\s*(?:de\s*)?RAM', re.I)

# Liste des teintes et finitions courantes pour le parsing de couleur
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
    'obsidienne', 'porcelaine', 'baie', 'noisette', 'menthe'
]


def get_random_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://bons-plans.easycash.fr/telephonie/smartphones",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }


def fetch_url_with_retry(url: str, max_retries: int = 4, backoff: float = 1.2) -> Optional[str]:
    """Effectue un appel HTTP GET avec gestion d'erreurs et retries progressifs."""
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=get_random_headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return resp.read().decode(charset, errors="replace")
                logger.warning(f"HTTP {resp.status} sur {url} (tentative {attempt}/{max_retries})")
        except urllib.error.HTTPError as e:
            logger.warning(f"Erreur HTTP {e.code} sur {url} (tentative {attempt}/{max_retries})")
            if e.code in [404, 410]:
                return None
        except Exception as e:
            logger.warning(f"Exception réseau {e} sur {url} (tentative {attempt}/{max_retries})")

        time.sleep(backoff * (1.5 ** (attempt - 1)) + random.uniform(0.1, 0.4))

    logger.error(f"Échec définitif du fetch après {max_retries} tentatives : {url}")
    return None


def parse_price(price_str: Optional[str]) -> Optional[float]:
    """Nettoie et convertit une chaîne de prix '1 334,99 €' en float 1334.99."""
    if not price_str:
        return None
    cleaned = (
        price_str.replace('\xa0', '')
        .replace(' ', '')
        .replace('€', '')
        .replace(',', '.')
        .replace('Àpartirde', '')
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_attributes_from_title(title: str, raw_brand: str) -> Tuple[str, str, Optional[int], str, Optional[int]]:
    """
    Extrait précisément le modèle, la couleur, le stockage et la RAM depuis le titre Easy Cash.
    Exemple: 'APPLE iPhone 17 Pro Max Argent 512 Go' ->
      (model='iPhone 17 Pro Max', color='Argent', storage_gb=512, storage_raw='512 Go', ram_gb=None)
    """
    cleaned_title = title.strip()
    
    # 1. Extraction de la RAM
    ram_gb = None
    rm = RAM_RE.search(cleaned_title)
    if rm:
        ram_gb = int(rm.group(1))

    # 2. Extraction du stockage
    storage_gb = None
    storage_raw = ""
    sm = STORAGE_RE.search(cleaned_title)
    if sm:
        storage_raw = sm.group(0).strip()
        val = int(sm.group(1))
        if 'to' in storage_raw.lower() or 'tb' in storage_raw.lower():
            val = val * 1024
        storage_gb = val

    # Découpage du titre avant la mention de stockage
    before_storage = cleaned_title[:sm.start()].strip() if sm else cleaned_title

    # Retrait de la marque en tête
    brand_regex = re.compile(rf'^{re.escape(raw_brand)}\s+', re.I)
    rest = brand_regex.sub('', before_storage).strip()

    # Nettoyage des suffixes fréquents comme 'débloqué' ou '5G'
    rest = re.sub(r'\s+d[ée]bloqu[ée]\b', '', rest, flags=re.I).strip()

    # 3. Extraction de la couleur (finition)
    color = ""
    lower_rest = rest.lower()
    for col in sorted(KNOWN_COLORS, key=len, reverse=True):
        if lower_rest.endswith(col):
            color = rest[-len(col):].strip()
            model_candidate = rest[:-len(col)].strip()
            rest = model_candidate
            break
    else:
        # Fallback si aucune couleur connue en fin de chaîne
        parts = rest.rsplit(' ', 1)
        if len(parts) == 2 and not parts[1].isdigit():
            rest = parts[0]
            color = parts[1]

    # Modèle brut final
    model_raw = rest.strip()
    return model_raw, color, storage_gb, storage_raw, ram_gb


def extract_impressions_json(html: str) -> List[Dict[str, Any]]:
    """Extrait le tableau JSON d'impressions e-commerce depuis dataLayer.push."""
    idx = html.find('dataLayer.push(')
    if idx == -1:
        return []
    try:
        start = idx + len('dataLayer.push(')
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(html[start:])
        return data.get('ecommerce', {}).get('impressions', [])
    except Exception as e:
        logger.debug(f"Erreur de décodage JSON dataLayer : {e}")
        return []


def discover_max_offset() -> int:
    """Découvre le nombre maximal de pages de smartphones sur Easy Cash."""
    html = fetch_url_with_retry(CATALOG_URL)
    if not html:
        logger.warning("Impossible de charger la page 1 pour la pagination. Offset max par défaut : 1860")
        return 1860

    soup = BeautifulSoup(html, 'html.parser')
    pag = soup.find('ul', class_='list-pagination')
    max_offset = 0
    if pag:
        for a in pag.find_all('a', href=True):
            m = re.search(r'offset=(\d+)', a['href'])
            if m:
                max_offset = max(max_offset, int(m.group(1)))

    if max_offset == 0:
        max_offset = 1860

    logger.info(f"🔍 Pagination Easy Cash détectée : max_offset = {max_offset} (soit {(max_offset // 30) + 1} pages)")
    return max_offset


def parse_page_items(
    html: str,
    page_offset: int,
    scrape_started_at: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse l'ensemble des offres d'une page de résultats Easy Cash."""
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    soup = BeautifulSoup(html, 'html.parser')
    cards = soup.find_all('li', class_='block-link')
    impressions = extract_impressions_json(html)
    impressions_by_id = {imp.get('id'): imp for imp in impressions if imp.get('id')}

    for card in cards:
        try:
            href = card.get('data-href', '')
            if not href.startswith('http'):
                href = f"https://bons-plans.easycash.fr{href}"

            title_el = card.find('a', class_='link-buy')
            item_title = title_el.get_text(strip=True) if title_el else ""
            if not item_title:
                continue

            price_el = card.find('p', class_='infos-price-number')
            price_raw = price_el.get_text(strip=True) if price_el else ""
            price = parse_price(price_raw)

            offers_el = card.find('p', class_='offers')
            offers_badge = offers_el.find('span').get('data-title', '') if offers_el and offers_el.find('span') else ""

            # Extraction ID produit depuis l'URL ou dataLayer
            id_match = re.search(r'-(\d{6,12})$', href)
            product_id = id_match.group(1) if id_match else ""

            # Enrichissement avec impressions si dispo
            imp = impressions_by_id.get(product_id, {})
            raw_brand = imp.get('brand', '')
            if not raw_brand:
                if item_title.lower().startswith("one plus"):
                    raw_brand = "OnePlus"
                elif item_title.lower().startswith("real me"):
                    raw_brand = "Realme"
                else:
                    raw_brand = item_title.split()[0]
            if price is None and imp.get('price'):
                try:
                    price = float(imp.get('price'))
                    price_raw = f"{price:.2f} €"
                except (ValueError, TypeError):
                    pass

            # Extraction des attributs fins
            model_raw, color, storage_gb, storage_raw, ram_gb = extract_attributes_from_title(item_title, raw_brand)

            # Normalisation canonique via device_validator
            brand_canon = raw_brand.capitalize()
            if validate_and_canonicalize_brand:
                v_brand = validate_and_canonicalize_brand(raw_brand, model_raw, item_title)
                if v_brand:
                    brand_canon = v_brand

            model_canon = model_raw
            if canonicalize_model_name:
                v_model = canonicalize_model_name(brand_canon, model_raw)
                if v_model:
                    model_canon = v_model

            # Validation du terminal smartphone
            if is_valid_smartphone_device:
                is_valid, reason = is_valid_smartphone_device(brand_canon, model_canon, item_title)
                if not is_valid:
                    issues.append({
                        "source_url": href,
                        "item_title": item_title,
                        "brand": brand_canon,
                        "model": model_canon,
                        "price": price,
                        "reason": f"Rejet taxonomie terminal : {reason}"
                    })
                    continue

            # Validation du prix de vente
            if is_valid_sale_price:
                price_valid, price_reason = is_valid_sale_price(price, brand_canon, model_canon, item_title)
                if not price_valid:
                    issues.append({
                        "source_url": href,
                        "item_title": item_title,
                        "brand": brand_canon,
                        "model": model_canon,
                        "price": price,
                        "reason": f"Rejet tarifaire : {price_reason}"
                    })
                    continue

            # Détermination de la famille d'appareils
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

            shipping_desc = DEFAULT_SHIPPING
            if offers_badge:
                shipping_desc = f"{DEFAULT_SHIPPING} ({offers_badge})"

            row = {
                "source_site": SOURCE_SITE,
                "source_url": href,
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
                "condition": "reconditionne",
                "product_id": product_id or href,
                "is_currently_sold": True,
                "price": price,
                "currency": "EUR",
                "price_raw": price_raw,
                "seller_name": SELLER_NAME,
                "shipping_cost": shipping_desc
            }
            rows.append(row)

        except Exception as e:
            issues.append({
                "source_url": href if 'href' in locals() else "",
                "item_title": item_title if 'item_title' in locals() else "",
                "reason": f"Exception parsing item : {str(e)}"
            })

    return rows, issues


def scrape_easy_cash(
    limit_pages: Optional[int] = None,
    workers: int = 8,
    output_dir: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Exécute le scraping intégral du catalogue Easy Cash avec parallélisation multithread.
    """
    scrape_started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 70)
    logger.info("⚡ Lancement du Scraper Easy Cash (Reconditionné & Occasion Garantie)")
    logger.info(f"   - Horodatage début : {scrape_started_at}")
    logger.info(f"   - Workers simultanés : {workers}")
    logger.info("=" * 70)

    out_dir = Path(output_dir) if output_dir else CURRENT_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    max_offset = discover_max_offset()
    offsets = list(range(0, max_offset + 30, 30))

    if limit_pages:
        offsets = offsets[:limit_pages]
        logger.info(f"⚠️ Mode restreint activé : limitation à {limit_pages} pages ({len(offsets)} requêtes)")

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []
    completed_count = 0
    total_offsets = len(offsets)

    def process_offset(off: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        url = f"{CATALOG_URL}&offset={off}"
        html = fetch_url_with_retry(url)
        if not html:
            return [], [{"source_url": url, "reason": "Échec de récupération HTTP de la page"}]
        return parse_page_items(html, off, scrape_started_at)

    logger.info(f"🚀 Démarrage de la collecte sur {total_offsets} pages Easy Cash...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_offset = {executor.submit(process_offset, off): off for off in offsets}
        for future in concurrent.futures.as_completed(future_to_offset):
            off = future_to_offset[future]
            completed_count += 1
            try:
                page_rows, page_issues = future.result()
                all_rows.extend(page_rows)
                all_issues.extend(page_issues)
                if completed_count % 10 == 0 or completed_count == total_offsets:
                    logger.info(f"⏳ Progression : {completed_count}/{total_offsets} pages traitées ({len(all_rows)} offres collectées)")
            except Exception as e:
                logger.error(f"Erreur inattendue sur offset {off}: {e}")
                all_issues.append({"offset": off, "reason": str(e)})

    scrape_finished_at = datetime.now(timezone.utc).isoformat()

    # Déduplication par (product_id, price, storage_gb, color)
    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for r in all_rows:
        key = (r["product_id"], r["price"], r["storage_gb"], r["color"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    logger.info("=" * 70)
    logger.info("🎉 Scraping Easy Cash Terminé avec Succès !")
    logger.info(f"   - Total Offres Brutes Collectées : {len(all_rows)}")
    logger.info(f"   - Offres Uniques Qualifiées : {len(unique_rows)}")
    logger.info(f"   - Total Anomalies / Rejets : {len(all_issues)}")
    logger.info("=" * 70)

    # Export des 5 artefacts standardisés E-commerce AI V2
    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="easy_cash",
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
        csv_file = out_dir / "db_easy_cash.csv"
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        logger.info(f"   ✓ CSV exporté : {csv_file}")

    # Synchronisation des fichiers dans dataset/ et ecommerce_ai_scraping_scraper_V2-main/liste_des_web/outputs/
    mirror_dirs = [
        PROJECT_ROOT / "dataset",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for mdir in mirror_dirs:
        try:
            mdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_easy_cash*"):
                shutil.copy2(f, mdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés avec succès vers : {mdir}")
        except Exception as e:
            logger.warning(f"Erreur lors de la copie vers {mdir}: {e}")

    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(
        description="Scraper Easy Cash — Smartphones Reconditionnés & Occasion (E-commerce AI V2)"
    )
    parser.add_argument("--limit-pages", type=int, default=None, help="Limiter le nombre de pages à scraper (test)")
    parser.add_argument("--workers", type=int, default=8, help="Nombre de threads simultanés (défaut: 8)")
    parser.add_argument("--output-dir", type=str, default=None, help="Répertoire de sortie personnalisé")
    args = parser.parse_args()

    scrape_easy_cash(
        limit_pages=args.limit_pages,
        workers=args.workers,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
