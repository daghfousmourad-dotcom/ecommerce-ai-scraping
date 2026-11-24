#!/usr/bin/env python3
"""
scraper_boulanger.py
====================
Scraper haute performance pour Boulanger (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°20 (Priorité 1) de la cartographie E-commerce AI Scraping.
Présence locale stratégique :
  - Boulanger Marseille Prado (261 Avenue du Prado, 13008 Marseille)
  - Boulanger Aubagne (ZI des Paluds, 13400 Aubagne)
  - Boulanger Cabriès - Plan de Campagne (13480 Cabriès)
  + Desserte e-commerce livraison express Marseille & national.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d extraction :
  - Catalogue complet Téléphonie / Smartphones :
    https://www.boulanger.com/c/smartphone-telephone-portable?numPage=N (Pages 1 à 49)
  - Extraction native des 2 univers : Neuf constructeur & Reconditionné "2nd Life"
  - Extraction hybride : Flux JSON analytics intégré + JSON-LD Schema.org + DOM

Génère 5 fichiers d artefacts standardisés dans ./outputs/ :
  1. db_boulanger.json
  2. db_boulanger.jsonl
  3. db_boulanger.csv
  4. db_boulanger_run.json
  5. db_boulanger_issues.jsonl
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from bs4 import BeautifulSoup

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
logger = logging.getLogger("scraper_boulanger")

SOURCE_SITE = "boulanger.com"
BASE_URL = "https://www.boulanger.com"
CATALOG_BASE_URL = "https://www.boulanger.com/c/smartphone-telephone-portable"
DEFAULT_SELLER = "Boulanger (Magasins Marseille Prado / Aubagne & Web)"
DEFAULT_SHIPPING = "Retrait 1h en magasin Marseille Prado / Aubagne ou Livraison offerte dès 20€"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0"
]

STORAGE_RE = re.compile(r"(\d+)\s*(?:Go|GB|To|TB)", re.I)
RAM_RE = re.compile(r"(\d+)\s*(?:Go|GB)\s*(?:de\s*)?RAM", re.I)

KNOWN_COLORS = [
    "noir sidéral", "gris sidéral", "bleu intense", "bleu marine", "blanc nuage",
    "titane naturel", "titane blanc", "titane noir", "titane bleu", "titane désert",
    "bleu pacifique", "bleu nuit", "bleu glacier", "bleu crépuscule", "bleu ciel",
    "vert nuit", "vert forêt", "vert sauge", "vert d eau", "vert",
    "noir volcanique", "noir prisme", "noir mat", "noir brillant", "noir",
    "blanc perle", "blanc céramique", "blanc prisme", "blanc",
    "argent", "silver", "or rose", "rose gold", "or", "gold", "jaune", "rouge",
    "rose poudré", "rose", "violet", "pourpre", "mauve", "lavande", "corail",
    "minuit", "lumière stellaire", "starlight", "midnight", "outremer",
    "graphite", "crème", "fantôme", "phantom black", "phantom white",
    "obsidienne", "porcelaine", "baie", "noisette", "menthe", "cosmic orange",
    "orange cosmique", "orange", "nacré", "nacre", "ambre", "bleu", "vert",
    "anthracite", "bronze", "space black"
]


def get_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.boulanger.com/",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }


def fetch_page_html(url: str, max_retries: int = 3, backoff: float = 1.0) -> Optional[str]:
    """Effectue une requête HTTP GET résiliente."""
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=get_headers())
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return resp.read().decode(charset, errors="replace")
                logger.warning(f"HTTP {resp.status} sur {url} (tentative {attempt}/{max_retries})")
        except urllib.error.HTTPError as e:
            logger.warning(f"HTTPError {e.code} sur {url} (tentative {attempt}/{max_retries})")
            if e.code in (404, 410):
                return None
        except Exception as e:
            logger.warning(f"Exception réseau {e} sur {url} (tentative {attempt}/{max_retries})")
        time.sleep(backoff * attempt + random.uniform(0.1, 0.4))
    return None


def extract_attributes_from_title(
    title: str,
    raw_brand: Optional[str] = None,
    product_color_attr: Optional[str] = None
) -> Tuple[str, Optional[int], Optional[str], Optional[int], Optional[str]]:
    """
    Extrait le modèle propre, le stockage, le libellé brut de stockage, la RAM et la couleur.
    """
    cleaned = title.strip()

    # 1. RAM
    ram_gb = None
    rm = RAM_RE.search(cleaned)
    if rm:
        ram_gb = int(rm.group(1))

    title_no_ram = RAM_RE.sub("", cleaned)

    # 2. Storage
    storage_gb = None
    storage_raw = None
    sm = STORAGE_RE.search(title_no_ram)
    if sm:
        num = int(sm.group(1))
        unit = sm.group(0).lower()
        if "to" in unit or "tb" in unit:
            num *= 1024
        storage_gb = num
        storage_raw = f"{storage_gb} Go" if storage_gb < 1024 else f"{storage_gb // 1024} To"

    # 3. Color
    color = None
    tl = cleaned.lower()
    for col in KNOWN_COLORS:
        if re.search(r"\b" + re.escape(col) + r"\b", tl):
            color = col.capitalize()
            break
    if not color and product_color_attr and product_color_attr.lower() not in ("autre", "inconnu", "none", ""):
        color = product_color_attr.capitalize()

    # 4. Model extraction
    m_clean = cleaned
    m_clean = re.sub(r"^(?:Smartphone|Pack|Téléphone portable|Mobile)\s*", "", m_clean, flags=re.I)
    if raw_brand:
        m_clean = re.sub(r"^" + re.escape(raw_brand) + r"\s*", "", m_clean, flags=re.I)
    if storage_raw:
        m_clean = re.sub(r"\b" + re.escape(storage_raw) + r"\b", "", m_clean, flags=re.I)
        m_clean = re.sub(r"\b\d+\s*(?:Go|GB|To|TB)\b", "", m_clean, flags=re.I)
    if ram_gb:
        m_clean = re.sub(r"\b\d+\s*(?:Go|GB)\s*(?:de\s*)?RAM\b", "", m_clean, flags=re.I)
    if color:
        m_clean = re.sub(r"\b" + re.escape(color) + r"\b", "", m_clean, flags=re.I)
    m_clean = re.sub(r"-\s*Reconditionn[eé].*$", "", m_clean, flags=re.I)
    m_clean = re.sub(r"\b(?:Excellent état|Très bon état|Bon état|État correct|avec batterie neuve)\b.*$", "", m_clean, flags=re.I)
    m_clean = re.sub(r"\+\s*.*$", "", m_clean)  # Retrait des accessoires de packs
    m_clean = re.sub(r"\s+", " ", m_clean).strip(" -_,;")

    if not m_clean and raw_brand:
        m_clean = raw_brand

    return m_clean, storage_gb, storage_raw, ram_gb, color


def detect_device_family(brand: str, model: str) -> str:
    """Déduit la famille d appareils de façon canonique."""
    b = brand.lower()
    m = model.lower()
    if b == "apple":
        return "iPhone"
    if b == "samsung":
        if "fold" in m or "flip" in m:
            return "Galaxy Z"
        if "s2" in m or "s1" in m or "s20" in m or "s21" in m or "s22" in m or "s23" in m or "s24" in m or "s25" in m or "s26" in m:
            return "Galaxy S"
        if "galaxy a" in m or re.search(r"\ba\d{2}\b", m):
            return "Galaxy A"
        return "Galaxy"
    if b == "google":
        return "Pixel"
    if b == "xiaomi":
        if "redmi" in m:
            return "Redmi"
        if "poco" in m:
            return "Poco"
        return "Xiaomi"
    if b == "honor":
        if "magic" in m:
            return "Magic"
        return "Honor"
    return brand


def parse_boulanger_page(html: str, page_num: int, scrape_started_at: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse l intégralité des offres d une page Boulanger."""
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    soup = BeautifulSoup(html, "html.parser")
    ld_items: Dict[str, Dict[str, Any]] = {}
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
            if d.get("@type") == "ItemList":
                for it in d.get("itemListElement", []):
                    u = it.get("url", "")
                    m = re.search(r"/ref/(\d+)", u)
                    if m:
                        ld_items[m.group(1)] = it
        except Exception:
            continue

    products_raw: List[Dict[str, Any]] = []
    m_script = re.search(r'\[\s*\{.*"list_product_id".*\}\s*\]', html)
    if m_script:
        try:
            products_raw = json.loads(m_script.group(0))
        except Exception as e:
            logger.warning(f"Erreur de décodage JSON analytics page {page_num} : {e}")

    if not products_raw:
        for a_tag in soup.find_all("a", href=re.compile(r"/ref/(\d+)")):
            attrs = a_tag.attrs
            if "data-analytics_product_sap" in attrs:
                pid = attrs.get("data-analytics_product_sap")
                products_raw.append({
                    "list_product_id": pid,
                    "list_product_name": attrs.get("data-analytics_product_name", ""),
                    "list_product_brand": attrs.get("data-analytics_product_brand", ""),
                    "list_product_unitprice_ati": attrs.get("data-analytics_product_unitprice_ati"),
                    "list_product_condition": attrs.get("data-analytics_product_condition", "neuf"),
                    "list_product_seller": attrs.get("data-analytics_product_seller", "boulanger"),
                    "list_product_availability": attrs.get("data-analytics_product_availability", "true"),
                    "list_product_color": attrs.get("data-analytics_product_color", "")
                })

    for p in products_raw:
        pid = str(p.get("list_product_id") or p.get("list_product_sap") or "").strip()
        if not pid:
            continue

        price_val = None
        raw_price_str = str(p.get("list_product_unitprice_ati") or "")
        try:
            price_val = float(raw_price_str)
        except (ValueError, TypeError):
            issues.append({
                "source": SOURCE_SITE,
                "product_id": pid,
                "reason": f"Prix invalide ou manquant : {raw_price_str}",
                "page": page_num
            })
            continue

        ld_info = ld_items.get(pid, {})
        display_title = ld_info.get("name")
        if not display_title:
            raw_slug = p.get("list_product_name") or ""
            display_title = raw_slug.replace("_", " ").title()

        product_url = ld_info.get("url") or f"{BASE_URL}/ref/{pid}"

        raw_brand = p.get("list_product_brand") or p.get("list_product_trademark") or ""
        brand_canon = None
        if validate_and_canonicalize_brand:
            brand_canon = validate_and_canonicalize_brand(raw_brand, display_title, display_title)
        else:
            brand_canon = raw_brand.capitalize()

        if not brand_canon:
            issues.append({
                "source": SOURCE_SITE,
                "product_id": pid,
                "title": display_title,
                "reason": f"Marque non retenue : {raw_brand}",
                "page": page_num
            })
            continue

        cond_raw = str(p.get("list_product_condition") or "").lower()
        title_lower = display_title.lower()
        if "recond" in cond_raw or "occas" in cond_raw or "reconditionn" in title_lower:
            condition = "reconditionne"
        else:
            condition = "neuf"

        color_attr = p.get("list_product_color")
        model_raw, storage_gb, storage_raw, ram_gb, color = extract_attributes_from_title(
            display_title,
            raw_brand=raw_brand,
            product_color_attr=color_attr
        )

        if is_valid_smartphone_device:
            valid_dev, reason_dev = is_valid_smartphone_device(brand_canon, model_raw, display_title)
            if not valid_dev:
                issues.append({
                    "source": SOURCE_SITE,
                    "product_id": pid,
                    "title": display_title,
                    "reason": reason_dev,
                    "page": page_num
                })
                continue

        if is_valid_sale_price:
            valid_p, reason_p = is_valid_sale_price(price_val, brand_canon, model_raw, display_title)
            if not valid_p:
                issues.append({
                    "source": SOURCE_SITE,
                    "product_id": pid,
                    "title": display_title,
                    "price": price_val,
                    "reason": reason_p,
                    "page": page_num
                })
                continue

        model_normalized = None
        if canonicalize_model_name:
            model_normalized = canonicalize_model_name(brand_canon, model_raw)
        if not model_normalized:
            model_normalized = f"{brand_canon} {model_raw}".strip()

        family = detect_device_family(brand_canon, model_raw)

        seller_raw = str(p.get("list_product_seller") or "").strip()
        if not seller_raw or seller_raw.lower() == "boulanger":
            seller_name = DEFAULT_SELLER
        else:
            seller_name = f"{seller_raw.title()} (Marketplace Boulanger)"

        avail = str(p.get("list_product_availability", "true")).lower() in ("true", "1", "yes")

        row = {
            "source_site": SOURCE_SITE,
            "source_url": product_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scrape_started_at": scrape_started_at,
            "data_type": "product_sales",
            "device_type": "smartphone",
            "device_family": family,
            "brand": brand_canon,
            "model": model_raw,
            "model_normalized": model_normalized,
            "item_title": display_title,
            "storage_gb": storage_gb,
            "storage_raw": storage_raw,
            "ram_gb": ram_gb,
            "color": color,
            "condition": condition,
            "product_id": pid,
            "is_currently_sold": avail,
            "price": price_val,
            "currency": "EUR",
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "seller_name": seller_name,
            "shipping_cost": DEFAULT_SHIPPING
        }
        rows.append(row)

    return rows, issues


def run_boulanger_scraper(
    max_pages: int = 49,
    workers: int = 4,
    output_dir: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Exécute la collecte globale sur le catalogue Boulanger Smartphones."""
    scrape_started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 70)
    logger.info("🚀 Démarrage du Scraper Boulanger Smartphones (E-commerce AI V2)")
    logger.info(f"   - Enseigne : Boulanger (Magasins Marseille Prado / Aubagne & Web)")
    logger.info(f"   - Objectif : {max_pages} pages du catalogue Téléphonie")
    logger.info(f"   - Concurrence : {workers} threads")
    logger.info(f"   - Horodatage : {scrape_started_at}")
    logger.info("=" * 70)

    out_dir = Path(output_dir) if output_dir else CURRENT_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []

    pages_to_fetch = list(range(1, max_pages + 1))

    def fetch_and_parse(p_num: int) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]]]:
        url = f"{CATALOG_BASE_URL}?numPage={p_num}"
        html = fetch_page_html(url)
        if not html:
            return p_num, [], [{"source": SOURCE_SITE, "page": p_num, "reason": "Échec téléchargement HTML"}]
        rows, issues = parse_boulanger_page(html, p_num, scrape_started_at)
        return p_num, rows, issues

    t0_global = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_and_parse, p): p for p in pages_to_fetch}
        for future in concurrent.futures.as_completed(futures):
            p_num, p_rows, p_issues = future.result()
            all_rows.extend(p_rows)
            all_issues.extend(p_issues)
            logger.info(f"   ✓ Page {p_num:02d}/{max_pages:02d} : {len(p_rows)} offres retenues, {len(p_issues)} rejets (Cumul : {len(all_rows)})")

    scrape_finished_at = datetime.now(timezone.utc).isoformat()
    total_duration = time.time() - t0_global

    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for r in all_rows:
        key = (r["product_id"], r["price"], r["condition"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    neuf_count = sum(1 for r in unique_rows if r["condition"] == "neuf")
    recond_count = sum(1 for r in unique_rows if r["condition"] == "reconditionne")

    logger.info("=" * 70)
    logger.info("🎉 Scraping Boulanger Terminé avec Succès !")
    logger.info(f"   - Durée Totale : {total_duration:.2f}s")
    logger.info(f"   - Total Offres Brutes : {len(all_rows)}")
    logger.info(f"   - Offres Uniques Qualifiées : {len(unique_rows)}")
    logger.info(f"   - Répartition : {neuf_count} Neuf | {recond_count} Reconditionné")
    logger.info(f"   - Total Anomalies / Rejets : {len(all_issues)}")
    logger.info("=" * 70)

    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="boulanger",
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
        csv_file = out_dir / "db_boulanger.csv"
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        logger.info(f"   ✓ CSV exporté : {csv_file}")

    mirror_dirs = [
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "dataset",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for mdir in mirror_dirs:
        try:
            mdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_boulanger*"):
                shutil.copy2(f, mdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés avec succès vers : {mdir}")
        except Exception as e:
            logger.warning(f"Erreur lors de la copie vers {mdir}: {e}")

    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper Boulanger — Smartphones Neufs & Reconditionnés (E-commerce AI V2)")
    parser.add_argument("--pages", type=int, default=49, help="Nombre de pages à scraper (défaut: 49)")
    parser.add_argument("--workers", type=int, default=4, help="Nombre de threads concurrents (défaut: 4)")
    parser.add_argument("--output-dir", type=str, default=None, help="Dossier de sortie personnalisé")
    args = parser.parse_args()

    run_boulanger_scraper(
        max_pages=args.pages,
        workers=args.workers,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
