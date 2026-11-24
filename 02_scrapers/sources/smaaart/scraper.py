#!/usr/bin/env python3
"""
scraper_smaaart.py
==================
Scraper haute performance pour SMAAART (Smartphones Reconditionnés en France).
Reconditionneur certifié français situé en région Occitanie / PACA.
Garantie 24 mois, tests sur 60 points de contrôle, livraison express 24-48h sur Marseille.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Catalogue complet Téléphonie / Smartphones Reconditionnés :
    https://smaaart.fr/12-smartphones-reconditionnes?page=N
  - Extraction multi-variantes par grade esthétique (État correct, Très bon état, Parfait état)
  - Exclusion stricte des ruptures de stock ("Bientôt disponible")

Génère 5 fichiers d'artefacts standardisés dans ./outputs/ :
  1. db_smaaart.json
  2. db_smaaart.jsonl
  3. db_smaaart.csv
  4. db_smaaart_run.json
  5. db_smaaart_issues.jsonl

Note : Conforme à la consigne de staging manuel (pas d'export automatique vers dataset/).
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
logger = logging.getLogger("scraper_smaaart")

SOURCE_SITE = "smaaart.fr"
BASE_URL = "https://smaaart.fr"
CATALOG_BASE_URL = "https://smaaart.fr/12-smartphones-reconditionnes"
DEFAULT_SELLER = "SMAAART (Reconditionneur Certifié France - Occitanie/PACA)"
DEFAULT_SHIPPING = "Livraison express 24-48h Marseille & France (Offerte dès 50€)"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0"
]

STORAGE_RE = re.compile(r'(\d+)\s*(?:Go|GB|To|TB)', re.I)
RAM_RE = re.compile(r'(\d+)\s*(?:Go|GB)\s*(?:de\s*)?RAM', re.I)

KNOWN_COLORS = [
    'noir sidéral', 'gris sidéral', 'bleu intense', 'bleu marine', 'blanc nuage',
    'titane naturel', 'titane blanc', 'titane noir', 'titane bleu', 'titane désert',
    'bleu pacifique', 'bleu nuit', 'bleu glacier', 'bleu crépuscule', 'bleu ciel',
    'vert nuit', 'vert forêt', 'vert sauge', "vert d'eau", 'vert',
    'noir volcanique', 'noir prisme', 'noir mat', 'noir brillant', 'noir',
    'blanc perle', 'blanc céramique', 'blanc prisme', 'blanc',
    'argent', 'silver', 'or rose', 'rose gold', 'or', 'gold', 'jaune', 'rouge',
    'rose poudré', 'rose', 'violet', 'pourpre', 'mauve', 'lavande', 'corail',
    'minuit', 'lumière stellaire', 'starlight', 'midnight', 'outremer',
    'graphite', 'crème', 'fantôme', 'phantom black', 'phantom white',
    'obsidienne', 'porcelaine', 'baie', 'noisette', 'menthe',
    'orange cosmique', 'orange', 'nacré', 'nacre', 'ambre', 'bleu', 'gris',
    'anthracite', 'bronze', 'space black'
]


def get_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://smaaart.fr/",
        "Connection": "keep-alive"
    }


def fetch_url(url: str, max_retries: int = 3, backoff: float = 1.0) -> Optional[str]:
    """Effectue une requête HTTP GET résiliente en encodant les caractères non-ASCII."""
    safe_url = urllib.parse.quote(url, safe="/:?=&%#")
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(safe_url, headers=get_headers())
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


def extract_attributes_from_title(title: str, raw_brand: Optional[str] = None) -> Tuple[str, Optional[int], Optional[str], Optional[str]]:
    """Extrait le modèle, le stockage, et la couleur d'un titre SMAAART."""
    cleaned = title.strip()

    # Storage
    storage_gb = None
    storage_raw = None
    sm = STORAGE_RE.search(cleaned)
    if sm:
        num = int(sm.group(1))
        unit = sm.group(0).lower()
        if "to" in unit or "tb" in unit:
            num *= 1024
        storage_gb = num
        storage_raw = f"{storage_gb} Go" if storage_gb < 1024 else f"{storage_gb // 1024} To"

    # Color
    color = None
    tl = cleaned.lower()
    for col in KNOWN_COLORS:
        if re.search(r'\b' + re.escape(col) + r'\b', tl):
            color = col.capitalize()
            break

    # Clean model
    m_clean = cleaned
    m_clean = re.sub(r'^(?:Smartphone|Téléphone portable|Mobile)\s*', '', m_clean, flags=re.I)
    if raw_brand:
        m_clean = re.sub(r'^' + re.escape(raw_brand) + r'\s*', '', m_clean, flags=re.I)
    if storage_raw:
        m_clean = re.sub(r'\b' + re.escape(storage_raw) + r'\b', '', m_clean, flags=re.I)
        m_clean = re.sub(r'\b\d+\s*(?:Go|GB|To|TB)\b', '', m_clean, flags=re.I)
    if color:
        m_clean = re.sub(r'\b' + re.escape(color) + r'\b', '', m_clean, flags=re.I)
    m_clean = re.sub(r'-\s*Reconditionn[eé].*$', '', m_clean, flags=re.I)
    m_clean = re.sub(r'\bReconditionn[eé]\b.*$', '', m_clean, flags=re.I)
    m_clean = re.sub(r'\b(?:Double SIM|SIM \+ eSIM|Dual SIM)\b', '', m_clean, flags=re.I)
    m_clean = re.sub(r'\s+', ' ', m_clean).strip(" -_,;")

    if not m_clean and raw_brand:
        m_clean = raw_brand

    return m_clean, storage_gb, storage_raw, color


def detect_device_family(brand: str, model: str) -> str:
    """Déduit la famille d'appareils de façon canonique."""
    b = brand.lower()
    m = model.lower()
    if b == "apple":
        return "iPhone"
    if b == "samsung":
        if "fold" in m or "flip" in m:
            return "Galaxy Z"
        if "s2" in m or "s1" in m or "s20" in m or "s21" in m or "s22" in m or "s23" in m or "s24" in m or "s25" in m or "s26" in m:
            return "Galaxy S"
        if "galaxy a" in m or re.search(r'a\d{2}', m):
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
        return "Honor"
    return brand


def collect_product_urls(max_pages: Optional[int] = None) -> List[str]:
    """Parcourt la pagination exhaustive du catalogue SMAAART jusqu'à la dernière page disponible."""
    urls: Set[str] = set()
    p = 1
    while True:
        if max_pages and p > max_pages:
            logger.info(f"   ⏹️ Limite de {max_pages} page(s) atteinte.")
            break
        cat_url = f"{CATALOG_BASE_URL}?page={p}"
        html = fetch_url(cat_url)
        if not html:
            logger.info(f"   ℹ️ Fin de pagination SMAAART atteinte à la page {p} (HTML vide ou inexistant).")
            break
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("article", class_=re.compile(r"product-miniature", re.I))
        if not cards:
            logger.info(f"   ℹ️ Fin de pagination SMAAART atteinte à la page {p} (0 produit).")
            break
        count_page = 0
        for c in cards:
            link = c.find("a", class_=re.compile(r"thumbnail|product-thumbnail", re.I))
            if link and link.get("href"):
                h = link["href"].strip()
                if h.endswith(".html"):
                    urls.add(h)
                    count_page += 1
        logger.info(f"   ✓ Page catalogue {p:02d} : {count_page} fiches produits trouvées (Total unique : {len(urls)})")
        if count_page == 0:
            logger.info(f"   ℹ️ Aucun nouveau produit sur la page {p}, fin de pagination.")
            break
        p += 1
        time.sleep(random.uniform(0.3, 0.6))
    return sorted(list(urls))


def parse_product_page(
    product_url: str,
    scrape_started_at: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extrait toutes les offres de grades en stock d'une fiche produit SMAAART."""
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    html = fetch_url(product_url)
    if not html:
        issues.append({
            "source": SOURCE_SITE,
            "url": product_url,
            "reason": "Échec de chargement HTML"
        })
        return rows, issues

    soup = BeautifulSoup(html, "html.parser")

    # 1. Titre brut
    h1 = soup.find("h1")
    raw_title = h1.get_text(" ", strip=True) if h1 else ""
    if not raw_title:
        issues.append({
            "source": SOURCE_SITE,
            "url": product_url,
            "reason": "Titre H1 introuvable"
        })
        return rows, issues

    # 2. Extraction marque depuis JSON-LD ou titre
    brand_raw = ""
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string or "")
            if d.get("@type") == "Product":
                b_obj = d.get("brand", {})
                brand_raw = b_obj.get("name", "") if isinstance(b_obj, dict) else str(b_obj)
        except Exception:
            pass

    if not brand_raw:
        tl = raw_title.lower()
        if "iphone" in tl or "apple" in tl:
            brand_raw = "Apple"
        elif "samsung" in tl or "galaxy" in tl:
            brand_raw = "Samsung"
        elif "xiaomi" in tl or "redmi" in tl:
            brand_raw = "Xiaomi"
        elif "huawei" in tl:
            brand_raw = "Huawei"
        elif "pixel" in tl or "google" in tl:
            brand_raw = "Google"

    brand_canon = None
    if validate_and_canonicalize_brand:
        brand_canon = validate_and_canonicalize_brand(brand_raw, raw_title, raw_title)
    if not brand_canon:
        brand_canon = brand_raw.capitalize() if brand_raw else None

    if not brand_canon:
        issues.append({
            "source": SOURCE_SITE,
            "url": product_url,
            "title": raw_title,
            "reason": f"Marque non retenue : {brand_raw}"
        })
        return rows, issues

    # 3. Attributs : modèle, stockage, couleur
    model_raw, storage_gb, storage_raw, color = extract_attributes_from_title(raw_title, raw_brand=brand_raw)

    if is_valid_smartphone_device:
        valid_dev, reason_dev = is_valid_smartphone_device(brand_canon, model_raw, raw_title)
        if not valid_dev:
            issues.append({
                "source": SOURCE_SITE,
                "url": product_url,
                "title": raw_title,
                "reason": reason_dev
            })
            return rows, issues

    model_normalized = None
    if canonicalize_model_name:
        model_normalized = canonicalize_model_name(brand_canon, model_raw)
    if not model_normalized:
        model_normalized = f"{brand_canon} {model_raw}".strip()

    family = detect_device_family(brand_canon, model_raw)

    pm = re.search(r"/(\d+)-", product_url)
    base_pid = pm.group(1) if pm else "0"

    # 4. Parcours des déclinaisons de grades esthétiques
    var_div = soup.find("div", class_="product-variants")

    if var_div:
        for li in var_div.find_all("li", class_="states-wrapper"):
            if "out-of-stock" in li.get("class", []):
                continue

            name_span = li.find(class_="attribute-name")
            price_span = li.find(class_="attribute-price")
            inp = li.find("input")
            attr_val = inp.get("value", "") if inp else ""

            if not (name_span and price_span):
                continue

            grade_name = name_span.get_text(" ", strip=True)
            p_text = price_span.get("content") or price_span.get_text(" ", strip=True)
            p_clean = p_text.replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")

            try:
                price_val = float(p_clean)
            except ValueError:
                continue

            if is_valid_sale_price:
                valid_p, reason_p = is_valid_sale_price(price_val, brand_canon, model_raw, raw_title)
                if not valid_p:
                    issues.append({
                        "source": SOURCE_SITE,
                        "url": product_url,
                        "title": raw_title,
                        "price": price_val,
                        "reason": reason_p
                    })
                    continue

            pid = f"{base_pid}_{attr_val}" if attr_val else f"{base_pid}_{grade_name.lower().replace(' ', '_')}"
            full_title = f"{raw_title} — {grade_name}"

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
                "item_title": full_title,
                "storage_gb": storage_gb,
                "storage_raw": storage_raw,
                "ram_gb": None,
                "color": color,
                "condition": "reconditionne",
                "product_id": pid,
                "is_currently_sold": True,
                "price": price_val,
                "currency": "EUR",
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "seller_name": DEFAULT_SELLER,
                "shipping_cost": DEFAULT_SHIPPING
            }
            rows.append(row)

    return rows, issues


def run_smaaart_scraper(
    max_pages: Optional[int] = None,
    workers: int = 5,
    output_dir: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Exécute la collecte intégrale sur le catalogue SMAAART Smartphones Reconditionnés."""
    scrape_started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 70)
    logger.info("🚀 Démarrage du Scraper SMAAART Smartphones Reconditionnés (E-commerce AI V2)")
    logger.info(f"   - Enseigne : SMAAART (Occitanie / PACA / Desserte Marseille)")
    logger.info(f"   - Concurrence : {workers} threads")
    logger.info(f"   - Horodatage : {scrape_started_at}")
    logger.info("=" * 70)

    out_dir = Path(output_dir) if output_dir else CURRENT_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("⏳ Étape 1 : Collecte de l'index des fiches produits smartphones...")
    product_urls = collect_product_urls(max_pages=max_pages)
    logger.info(f"   ✓ Total fiches produits à analyser : {len(product_urls)}")

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []

    logger.info(f"⏳ Étape 2 : Extraction détaillée des offres et grades ({workers} workers)...")
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_product_page, u, scrape_started_at): u for u in product_urls}
        for future in concurrent.futures.as_completed(futures):
            p_rows, p_issues = future.result()
            all_rows.extend(p_rows)
            all_issues.extend(p_issues)

    total_duration = time.time() - t0
    scrape_finished_at = datetime.now(timezone.utc).isoformat()

    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for r in all_rows:
        key = (r["product_id"], r["price"], r["condition"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    logger.info("=" * 70)
    logger.info("🎉 Scraping SMAAART Terminé avec Succès !")
    logger.info(f"   - Durée Totale : {total_duration:.2f}s")
    logger.info(f"   - Fiches Produits Analysées : {len(product_urls)}")
    logger.info(f"   - Offres Uniques Qualifiées en Stock : {len(unique_rows)}")
    logger.info(f"   - Condition : 100% Reconditionné certifié")
    logger.info(f"   - Total Anomalies / Rejets : {len(all_issues)}")
    logger.info("=" * 70)

    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="smaaart",
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
        csv_file = out_dir / "db_smaaart.csv"
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        logger.info(f"   ✓ CSV exporté : {csv_file}")

    # Synchronisation vers répertoires de staging UNIQUEMENT (PAS dataset/ selon consigne utilisateur)
    staging_dirs = [
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for sdir in staging_dirs:
        try:
            sdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_smaaart*"):
                shutil.copy2(f, sdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés vers staging : {sdir}")
        except Exception as e:
            logger.warning(f"Erreur copie staging {sdir}: {e}")

    logger.info("ℹ️ NOTE CONFORME : Aucun envoi vers dataset/ (Base Production) effectué conformément aux instructions.")
    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper SMAAART — Smartphones Reconditionnés France (E-commerce AI V2)")
    parser.add_argument("--pages", type=int, default=None, help="Nombre max de pages catalogue (défaut: None / Exhaustif)")
    parser.add_argument("--workers", type=int, default=5, help="Nombre de threads concurrents (défaut: 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Dossier de sortie personnalisé")
    args = parser.parse_args()

    run_smaaart_scraper(
        max_pages=args.pages,
        workers=args.workers,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
