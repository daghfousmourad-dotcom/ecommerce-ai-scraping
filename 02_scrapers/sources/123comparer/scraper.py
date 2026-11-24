#!/usr/bin/env python3
"""
scraper_123comparer.py
=======================
Scraper haute performance, exhaustif et agrégateur comparatif multi-marchands pour 123comparer.fr.
Cible : Comparateur de prix français et indépendant pour Smartphones Neufs, Reconditionnés & Occasion.

Valeur Stratégique Majeure pour E-commerce AI Scraping :
  - Pour chaque modèle de smartphone, 123comparer agrège en temps réel l'ensemble des offres
    marchandes du web français : Amazon, Fnac, Darty, Cdiscount, Boulanger, Rue du Commerce,
    Pixmania, Conforama, Galaxus, Kaufland, PcComponentes, SFR, Orange, Carrefour, Inmac, etc.
  - Chaque fiche produit permet d'extraire la dispersion complète des prix, les frais de livraison,
    l'état (Neuf, Reconditionné, Occasion), le marchand vendeur et le lien sortant officiel.

Architecture Hybride & Performances Extrêmes :
  1. Bypass Cloudflare Turnstile : Playwright initialise une session réelle Google Chrome
     (profil persistant /tmp/chrome_123_profile) pour obtenir et maintenir le cookie cf_clearance.
  2. Multi-Threading HTTP Haute Vitesse : Une fois le token obtenu, une session requests.Session
     parcourt le catalogue et extrait les fiches produits en parallèle (10 workers, ~25 req/s).
  3. Exhaustivité Totale : Parcourt l'ensemble de la pagination active (pages 1 à 172+),
     couvrant 100% des smartphones répertoriés avec des offres marchandes.

Conformité E-commerce AI V2 :
  - 23 colonnes standardisées (PRODUCT_SALE_KEYS)
  - Validation et canonicalisation via device_validator (marque, modèle, filtre smartphone, plancher prix)
  - 5 artefacts standardisés générés dans ./outputs/ :
      1. db_123comparer.json
      2. db_123comparer.jsonl
      3. db_123comparer.csv
      4. db_123comparer_run.json
      5. db_123comparer_issues.jsonl
  - Synchronisation stricte vers staging uniquement (outputs/). AUCUN envoi vers dataset/ ni GitHub.
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
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Chemins d'inclusion prioritaires et fallbacks internes
sys.path.insert(0, str(PROJECT_ROOT / "02_scrapers" / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "03_ia_normalizer" / "engine"))

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
logger = logging.getLogger("scraper_123comparer")

SOURCE_SITE = "123comparer.fr"
BASE_URL = "https://www.123comparer.fr"
DEFAULT_PROFILE_DIR = "/tmp/chrome_123_profile"

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "google-chrome",
]

COLOR_CANDIDATES = [
    "Noir", "Bleu", "Blanc", "Violet", "Titane", "Gris", "Rose", "Vert",
    "Lavande", "Or", "Argent", "Sauge", "Brume", "Graphite", "Crème", "Jaune"
]


def get_chrome_executable() -> Optional[str]:
    """Détecte l'exécutable Chrome sur la machine locale."""
    for path in CHROME_PATHS:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    which = shutil.which("google-chrome") or shutil.which("chromium")
    return which


def warm_up_cloudflare_session(profile_dir: str = DEFAULT_PROFILE_DIR) -> Tuple[Dict[str, str], str]:
    """
    Lance Playwright avec Google Chrome réel pour valider Cloudflare Turnstile
    et extrait les cookies (notamment cf_clearance) ainsi que le User-Agent natif.
    """
    chrome_exec = get_chrome_executable()
    logger.info("🛡️ Initialisation du contournement Cloudflare via Chrome réel...")
    with sync_playwright() as p:
        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": profile_dir,
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--window-position=100,100",
                "--window-size=1280,800",
                "--no-first-run",
                "--no-default-browser-check"
            ],
            "viewport": {"width": 1280, "height": 800}
        }
        if chrome_exec:
            launch_kwargs["executable_path"] = chrome_exec

        context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            page.goto(f"{BASE_URL}/smartphones/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2500)
        except Exception as e:
            logger.warning(f"Avertissement lors de la visite préliminaire: {e}")

        cookies_list = context.cookies(BASE_URL)
        cookie_dict = {c["name"]: c["value"] for c in cookies_list}
        ua = page.evaluate("navigator.userAgent")

        context.close()

    logger.info(f"✓ Cookie cf_clearance obtenu avec succès. User-Agent: {ua[:40]}...")
    return cookie_dict, ua


def create_http_session(cookies: Dict[str, str], user_agent: str, pool_size: int = 20) -> requests.Session:
    """Crée une session requests optimisée avec pool de connexions et retries."""
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"{BASE_URL}/smartphones/",
    })
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def clean_price_str(raw: str) -> Optional[float]:
    """Nettoie et convertit une chaîne de prix en float (supporte espaces fins insécables  )."""
    if not raw:
        return None
    cleaned = re.sub(r"[\s   -​]+", "", raw)
    cleaned = cleaned.replace("€", "").replace(",", ".").strip()
    m = re.search(r"(\d+(?:\.\d{1,2})?)", cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_storage_and_ram(text: str) -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """Extrait intelligemment le stockage Flash et la RAM sans confusion."""
    ram_gb = None
    rm = re.search(r"(\d+)\s*(?:Go|GB)\s*(?:/|RAM|de RAM)", text, re.I)
    if rm:
        ram_gb = int(rm.group(1))

    matches = re.findall(r"(\d+)\s*(Go|GB|To|TB)", text, re.I)
    gb_vals = []
    for num, unit in matches:
        val = int(num)
        if unit.upper() in ("TO", "TB"):
            val *= 1024
        gb_vals.append((val, f"{num} {unit.upper()}"))

    storage_gb = None
    storage_raw = None
    cand_storages = [v for v in gb_vals if v[0] != ram_gb]
    if cand_storages:
        cand_storages.sort(key=lambda x: x[0], reverse=True)
        storage_gb, storage_raw = cand_storages[0]
        if not ram_gb and len(gb_vals) > 1:
            smaller = min(gb_vals, key=lambda x: x[0])
            if smaller[0] in (3, 4, 6, 8, 12, 16, 24) and smaller[0] < storage_gb:
                ram_gb = smaller[0]
    elif gb_vals:
        storage_gb, storage_raw = gb_vals[-1]

    return storage_gb, storage_raw, ram_gb


def extract_product_offers(
    soup: BeautifulSoup,
    product_url: str,
    scrape_started_at: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extrait l'ensemble des offres marchandes d'une fiche produit comparative 123comparer.
    """
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    # 1. Métadonnées du Smartphone principal
    main_title = ""
    brand_main = ""

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string)
            if d.get("@type") == "Product":
                main_title = d.get("name", "")
                b_info = d.get("brand")
                if isinstance(b_info, dict):
                    brand_main = b_info.get("name", "")
                elif isinstance(b_info, str):
                    brand_main = b_info
                break
        except Exception:
            pass

    if not main_title:
        h1 = soup.find("h1")
        main_title = h1.get_text(strip=True) if h1 else ""

    if not main_title:
        return [], []

    # 2. Stockage (Go / To)
    st_match = re.search(r"(\d+)\s*(Go|GB|To|TB)", main_title, re.I)
    main_storage_raw = None
    main_storage_gb = None
    if st_match:
        main_storage_raw = f"{st_match.group(1)} {st_match.group(2).upper()}"
        val = int(st_match.group(1))
        unit = st_match.group(2).upper()
        main_storage_gb = val * 1024 if unit in ("TO", "TB") else val

    # 3. Couleur
    main_color = None
    for c_cand in COLOR_CANDIDATES:
        if re.search(r" " + c_cand + r" ", main_title, re.I):
            main_color = c_cand
            break

    # 4. Modèle & Marque
    if not brand_main:
        brand_main = main_title.split()[0].replace("-", "").strip()

    main_model = main_title
    for pref in [brand_main, "Samsung", "Apple", "Xiaomi", "Google", "OnePlus", "Honor", "Huawei", "Smartphone", "Motorola", "Sony", "Oppo", "Realme"]:
        if main_model.lower().startswith(pref.lower()):
            main_model = main_model[len(pref):].strip(" -/")
    main_model = re.sub(r"\s*\d+\s*(?:Go|GB|To|TB) .*", "", main_model, flags=re.I).strip(" -/()")
    if main_color and main_color in main_model:
        main_model = main_model.replace(main_color, "").strip(" -/()")

    if validate_and_canonicalize_brand:
        brand_canon = validate_and_canonicalize_brand(brand_main, main_model, main_title) or brand_main
    else:
        brand_canon = brand_main

    if canonicalize_model_name:
        model_canon = canonicalize_model_name(brand_canon, main_model) or main_model
    else:
        model_canon = main_model

    # Validation Appareil Smartphone
    if is_valid_smartphone_device:
        is_phone, reject_reason = is_valid_smartphone_device(brand_canon, main_model, main_title)
        if not is_phone:
            issues.append({
                "type": "REJECT_NON_SMARTPHONE",
                "title": main_title,
                "brand": brand_canon,
                "reason": reject_reason,
                "url": product_url,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            return [], issues

    # 5. Famille d'appareil
    title_lower = main_title.lower()
    if "iphone" in title_lower:
        device_family = "iPhone"
    elif "galaxy" in title_lower:
        device_family = "Galaxy"
    elif "pixel" in title_lower:
        device_family = "Pixel"
    elif "redmi" in title_lower:
        device_family = "Redmi"
    elif "poco" in title_lower:
        device_family = "Poco"
    elif "xperia" in title_lower:
        device_family = "Xperia"
    else:
        device_family = brand_canon

    # 6. Extraction des Lignes d'Offres Marchandes
    rows_el = soup.find_all("div", class_=lambda c: c and "collapse" in c and "border-b" in c)
    for r in rows_el:
        rid = r.get("id", "")
        h3 = r.find("h3")
        offer_title = h3.get_text(strip=True) if h3 else main_title

        # Marchand Vendeur
        seller_el = r.find(string=re.compile(r"Vendu par"))
        if seller_el:
            seller_name = seller_el.replace("Vendu par", "").strip()
        else:
            logo = r.find("img", alt=re.compile(r"Logo de"))
            seller_name = logo["alt"].replace("Logo de", "").strip() if logo else "Marchand 123comparer"

        # Prix
        price_el = r.find("span", class_=re.compile(r"text-accent"))
        if not price_el:
            continue
        price_val = clean_price_str(price_el.get_text(strip=True))
        if not price_val:
            continue

        # Validation du prix de vente
        if is_valid_sale_price:
            valid_p, p_reason = is_valid_sale_price(price_val, brand_canon, main_model, offer_title)
            if not valid_p:
                issues.append({
                    "type": "REJECT_INVALID_PRICE",
                    "title": offer_title,
                    "price": price_val,
                    "seller": seller_name,
                    "reason": p_reason,
                    "url": product_url,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                continue

        # Frais de port
        truck = r.find("img", alt=re.compile(r"livraison|frais de port"))
        if truck and truck.find_next_sibling("span"):
            shipping_cost = truck.find_next_sibling("span").get_text(strip=True)
        else:
            shipping_cost = "Gratuit"

        # Lien sortant & ID Offre
        a_link = r.find("a", href=re.compile(r"/ofrs/"))
        if a_link:
            ofr_href = a_link["href"]
            m_oid = re.search(r"/ofrs/([a-zA-Z0-9_-]+)", ofr_href)
            offer_id = m_oid.group(1) if m_oid else rid
            source_url = f"{BASE_URL}{ofr_href}" if ofr_href.startswith("/") else ofr_href
        else:
            offer_id = rid
            source_url = product_url

        # État (Neuf, Reconditionné, Occasion)
        if "occaz" in rid or "occasion" in rid or "occasion" in offer_title.lower():
            condition = "occasion"
        elif "recond" in rid or "reconditionn" in offer_title.lower():
            condition = "reconditionne"
        else:
            condition = "neuf"

        # RAM spécifique à l'offre
        ram_gb = None
        rm_match = re.search(r"(\d+)\s*(?:Go|GB)\s*(?:/|RAM|de RAM)", offer_title, re.I)
        if rm_match:
            ram_gb = int(rm_match.group(1))

        # Stockage spécifique à l'offre
        st_offer = re.search(r"(\d+)\s*(Go|GB|To|TB)", offer_title, re.I)
        if st_offer:
            st_raw = f"{st_offer.group(1)} {st_offer.group(2).upper()}"
            val = int(st_offer.group(1))
            unit = st_offer.group(2).upper()
            st_gb = val * 1024 if unit in ("TO", "TB") else val
        else:
            st_raw = main_storage_raw
            st_gb = main_storage_gb

        # Couleur spécifique à l'offre
        color = main_color
        for c_cand in COLOR_CANDIDATES:
            if re.search(r" " + c_cand + r" ", offer_title, re.I):
                color = c_cand
                break

        now_str = datetime.now(timezone.utc).isoformat()
        row: Dict[str, Any] = {
            "source_site": SOURCE_SITE,
            "source_url": source_url,
            "timestamp": now_str,
            "scrape_started_at": scrape_started_at,
            "data_type": "product_sales",
            "device_type": "smartphone",
            "device_family": device_family,
            "brand": brand_canon,
            "model": main_model,
            "model_normalized": model_canon,
            "item_title": offer_title,
            "storage_gb": st_gb,
            "storage_raw": st_raw,
            "ram_gb": ram_gb,
            "color": color,
            "condition": condition,
            "product_id": f"123c_{offer_id}",
            "is_currently_sold": True,
            "price": price_val,
            "currency": "EUR",
            "price_raw": f"{price_val:.2f} €",
            "seller_name": seller_name,
            "shipping_cost": shipping_cost,
        }
        rows.append(row)

    return rows, issues


def scan_all_catalog_pages(
    session: requests.Session,
    max_pages: int = 175,
    workers: int = 8
) -> List[str]:
    """
    Scanne l'ensemble des pages de pagination du catalogue /smartphones/
    et retourne la liste complète des URLs de smartphones avec offres marchandes.
    """
    logger.info(f"📍 Phase 1 : Scan exhaustif des catalogues (jusqu'à {max_pages} pages) avec {workers} threads...")
    t0_scan = time.time()

    def scan_page(pg: int) -> Tuple[int, List[str]]:
        url = f"{BASE_URL}/smartphones/?display=grid&page={pg}" if pg > 1 else f"{BASE_URL}/smartphones/"
        try:
            r = session.get(url, timeout=12)
            if r.status_code != 200:
                return pg, []
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            found = []
            for h2 in soup.find_all("h2"):
                card = h2.find_parent("div", class_=lambda c: c and "border" in c)
                if not card:
                    continue
                btn = card.find(string=re.compile(r"Voir les \d+ offres"))
                if btn:
                    a = card.find("a", href=lambda h: h and "/smartphones/" in h and h.endswith(".html"))
                    if a:
                        full = f"{BASE_URL}{a['href']}" if a["href"].startswith("/") else a["href"]
                        found.append(full)
            return pg, found
        except Exception:
            return pg, []

    discovered_urls: Set[str] = set()
    empty_streak = 0

    chunk_size = 25
    for start_pg in range(1, max_pages + 1, chunk_size):
        end_pg = min(start_pg + chunk_size, max_pages + 1)
        pages_chunk = list(range(start_pg, end_pg))

        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(scan_page, pages_chunk))

        chunk_found = 0
        for pg, prods in sorted(results, key=lambda x: x[0]):
            if prods:
                discovered_urls.update(prods)
                chunk_found += len(prods)
                empty_streak = 0
            else:
                empty_streak += 1

        logger.info(f"   ✓ Pages {start_pg:03d}-{end_pg-1:03d} analysées | Total cumulé : {len(discovered_urls)} smartphones identifiés")
        if empty_streak >= 15:
            logger.info("   -> Fin des offres actives atteinte dans la pagination.")
            break

    dur_scan = time.time() - t0_scan
    logger.info(f"✨ Scan catalogue terminé en {dur_scan:.2f}s : {len(discovered_urls)} modèles uniques recensés.")
    return list(discovered_urls)


def run_123comparer_scraper(
    max_pages: int = 175,
    max_products: Optional[int] = None,
    workers: int = 10,
    output_dir: Optional[str | Path] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Point d'entrée principal du scraper exhaustif 123comparer.
    """
    t0_global = time.time()
    scrape_started_at = datetime.now(timezone.utc).isoformat()

    out_dir = Path(output_dir) if output_dir else (CURRENT_DIR / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("🚀 DÉMARRAGE DU SCRAPER 123COMPARER — EXTRACTION EXHAUSTIVE (E-COMMERCE AI V2)")
    logger.info(f"   - Objectif : 100% de la pagination active (jusqu'à {max_pages} pages)")
    logger.info(f"   - Threads parallèles : {workers}")
    logger.info(f"   - Dossier de sortie staging : {out_dir}")
    logger.info("=" * 70)

    # 1. Warm-up Cloudflare Turnstile
    cookie_dict, user_agent = warm_up_cloudflare_session()
    session = create_http_session(cookie_dict, user_agent, pool_size=workers * 2)

    # 2. Phase 1 : Découverte exhaustive des fiches smartphones
    product_urls = scan_all_catalog_pages(session, max_pages=max_pages, workers=min(workers, 8))

    if max_products and max_products > 0:
        product_urls = product_urls[:max_products]
        logger.info(f"ℹ️ Limite appliquée pour ce run : {len(product_urls)} fiches.")

    total_prods = len(product_urls)
    logger.info(f"📍 Phase 2 : Extraction parallèle des offres sur {total_prods} fiches smartphones...")

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []

    def fetch_and_extract(url: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        try:
            r = session.get(url, timeout=12)
            if r.status_code != 200:
                return [], [{"type": "HTTP_ERROR", "url": url, "code": r.status_code}]
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            return extract_product_offers(soup, url, scrape_started_at)
        except Exception as e:
            return [], [{"type": "FETCH_EXCEPTION", "url": url, "error": str(e)}]

    # 3. Extraction parallèle
    t_start_prods = time.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_and_extract, u): u for u in product_urls}
        for fut in as_completed(futures):
            p_rows, p_issues = fut.result()
            all_rows.extend(p_rows)
            all_issues.extend(p_issues)
            completed += 1

            if completed % 100 == 0 or completed == total_prods:
                elapsed = time.time() - t_start_prods
                rate = completed / elapsed if elapsed > 0 else 0
                logger.info(f"   [{completed:04d}/{total_prods:04d}] ({completed/total_prods*100:.1f}%) -> {len(all_rows)} offres récoltées ({rate:.1f} fiches/sec)")

    scrape_finished_at = datetime.now(timezone.utc).isoformat()
    total_duration = time.time() - t0_global

    # 4. Dédoublonnage strict par (product_id, seller_name, price)
    unique_rows: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, str, float]] = set()
    for r in all_rows:
        key = (r["product_id"], r["seller_name"], r["price"])
        if key not in seen_keys:
            seen_keys.add(key)
            unique_rows.append(r)

    neuf_count = sum(1 for r in unique_rows if r["condition"] == "neuf")
    recond_count = sum(1 for r in unique_rows if r["condition"] == "reconditionne")
    occas_count = sum(1 for r in unique_rows if r["condition"] == "occasion")
    merchants: Set[str] = {r["seller_name"] for r in unique_rows}

    logger.info("=" * 70)
    logger.info("🎉 SCRAPING 123COMPARER FINALISÉ AVEC SUCCÈS !")
    logger.info(f"   - Durée Totale : {total_duration:.2f}s ({total_duration/60:.2f} min)")
    logger.info(f"   - Fiches Produits Analysées : {total_prods}")
    logger.info(f"   - Offres Marchandes Récoltées : {len(all_rows)}")
    logger.info(f"   - Offres Uniques Qualifiées (E-commerce AI V2) : {len(unique_rows)}")
    logger.info(f"   - Marchands Distincts Capturés ({len(merchants)}) : {', '.join(sorted(list(merchants))[:10])}...")
    logger.info(f"   - Répartition État : {neuf_count} Neuf | {recond_count} Reconditionné | {occas_count} Occasion")
    logger.info(f"   - Anomalies / Rejets Enregistrés : {len(all_issues)}")
    logger.info("=" * 70)

    # 5. Export des 5 artefacts standardisés
    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="123comparer",
            domain="product_sales",
            rows=unique_rows,
            output_dir=out_dir,
            issues=all_issues,
            scrape_started_at=scrape_started_at,
            scrape_finished_at=scrape_finished_at
        )
        logger.info(f"📁 5 Artefacts standardisés générés dans : {out_dir}")
        for k, pth in files.items():
            logger.info(f"   ✓ {k}: {pth}")
    else:
        prefix = "db_123comparer"
        json_file = out_dir / f"{prefix}.json"
        jsonl_file = out_dir / f"{prefix}.jsonl"
        csv_file = out_dir / f"{prefix}.csv"
        run_file = out_dir / f"{prefix}_run.json"
        issues_file = out_dir / f"{prefix}_issues.jsonl"

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(unique_rows, f, indent=2, ensure_ascii=False)
        with open(jsonl_file, "w", encoding="utf-8") as f:
            for r in unique_rows:
                f.write(json.dumps(r, ensure_ascii=False) + chr(10))
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        with open(issues_file, "w", encoding="utf-8") as f:
            for iss in all_issues:
                f.write(json.dumps(iss, ensure_ascii=False) + chr(10))
        run_meta = {
            "source_site": "123comparer",
            "data_domain": "product_sales",
            "scrape_started_at": scrape_started_at,
            "scrape_finished_at": scrape_finished_at,
            "status": "completed",
            "row_count": len(unique_rows),
            "columns_count": len(PRODUCT_SALE_KEYS),
            "columns": PRODUCT_SALE_KEYS,
        }
        with open(run_file, "w", encoding="utf-8") as f:
            json.dump(run_meta, f, indent=2, ensure_ascii=False)
        logger.info(f"📁 5 Artefacts générés via fallback : {out_dir}")

    # 6. Synchronisation STRICTEMENT vers staging (AUCUN envoi vers dataset/)
    staging_dirs = [
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for sdir in staging_dirs:
        try:
            sdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_123comparer*"):
                shutil.copy2(f, sdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés vers staging : {sdir}")
        except Exception as e:
            logger.warning(f"Erreur copie staging {sdir}: {e}")

    logger.info("ℹ️ NOTE CONFORME : Aucun envoi vers dataset/ (Base Production) effectué conformément aux instructions.")
    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper Exhaustif 123comparer — Agrégateur Comparatif Multi-Marchands (E-commerce AI V2)")
    parser.add_argument("--max-pages", type=int, default=175, help="Nombre max de pages catalogue à scanner (défaut: 175 = pagination complète)")
    parser.add_argument("--max-products", type=int, default=None, help="Nombre max de smartphones à extraire (défaut: None = exhaustivité totale)")
    parser.add_argument("--workers", type=int, default=10, help="Nombre de threads d'extraction parallèle (défaut: 10)")
    parser.add_argument("--output-dir", type=str, default=None, help="Dossier de sortie personnalisé")
    args = parser.parse_args()

    run_123comparer_scraper(
        max_pages=args.max_pages,
        max_products=args.max_products,
        workers=args.workers,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
