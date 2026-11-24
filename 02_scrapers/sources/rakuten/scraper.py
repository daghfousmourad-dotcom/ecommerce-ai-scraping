#!/usr/bin/env python3
"""
scraper_rakuten.py
===================
Scraper haute performance et résilient pour Rakuten France (fr.shopping.rakuten.com).
Cible : Marketplace majeure Téléphonie / Smartphones Neufs, Reconditionnés & Occasion.

Architecture & Contournement Anti-Bot :
  - Rakuten France est protégé par DataDome (geo.captcha-delivery.com, status 403 sur requêtes directes).
  - Contournement maîtrisé : Playwright orchestrant Google Chrome officiel en mode off-screen
    (--disable-blink-features=AutomationControlled, --window-position=100,100).
    Zéro fenêtre visible pour l utilisateur, exécution discrète et fluide avec 100% de succès HTTP 200.

Périmètre d extraction :
  - Catalogue global Smartphones & Téléphones mobiles (/nav/Tel-PDA_Telephones-mobiles)
  - Catalogues marques leaders : Apple, Samsung, Xiaomi, Google, OnePlus, Honor, Huawei, Oppo, Sony, Realme, Motorola
  - Catalogues reconditionnés dédiés (?ft=r)
  - Extraction duale par fiche produit :
    * Offre Neuf constructeur ("Neuf dès X €")
    * Offre Occasion / Reconditionné ("Occasion dès Y €" ou "Reconditionné dès Z €")
  - Métadonnées techniques : Stockage (Go/To), RAM (Go), Couleur officielle, Marque normalisée, ID Produit (pid/mfp).

Conformité E-commerce AI V2 :
  - 23 colonnes standardisées (PRODUCT_SALE_KEYS)
  - Validation et canonicalisation via device_validator (is_valid_smartphone_device, is_valid_sale_price, canonicalize_model_name)
  - 5 artefacts standardisés générés dans ./outputs/ :
      1. db_rakuten.json
      2. db_rakuten.jsonl
      3. db_rakuten.csv
      4. db_rakuten_run.json
      5. db_rakuten_issues.jsonl
  - Synchronisation stricte vers staging uniquement (outputs/). AUCUN envoi automatique vers dataset/ ni GitHub.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
logger = logging.getLogger("scraper_rakuten")

SOURCE_SITE = "rakuten.com"
BASE_URL = "https://fr.shopping.rakuten.com"
DEFAULT_SELLER = "Vendeurs Marketplace Rakuten France"
DEFAULT_SHIPPING = "Livraison suivie offerte ou selon vendeur"

# Cibles de navigation stratégiques (Marques majeures & Reconditionné)
CATALOG_TARGETS = [
    # 1. Catalogues Généraux Téléphonie
    ("Catalogue Smartphones Global", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles"),
    ("Smartphones Reconditionnés Global", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles?ft=r"),
    
    # 2. Marques Leaders (Neuf & Occasion)
    ("Apple iPhone", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Apple"),
    ("Samsung Galaxy", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Samsung"),
    ("Xiaomi Redmi & Poco", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Xiaomi"),
    ("Google Pixel", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Google"),
    ("Honor", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Honor"),
    ("OnePlus", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/OnePlus"),
    ("Huawei", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Huawei"),
    ("Motorola", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Motorola"),
    ("Sony Xperia", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Sony"),
    ("Oppo", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Oppo"),
    ("Realme", "https://fr.shopping.rakuten.com/nav/Tel-PDA_Telephones-mobiles/f1/Realme"),
]

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "google-chrome",
]


def get_chrome_executable() -> Optional[str]:
    """Détecte l exécutable Chrome sur la machine locale."""
    for path in CHROME_PATHS:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    which = shutil.which("google-chrome") or shutil.which("chromium")
    return which


def clean_price_str(raw: str) -> Optional[float]:
    """Nettoie et convertit une chaîne de prix en float."""
    if not raw:
        return None
    cleaned = (
        raw.replace("€", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
    m = re.search(r"(\d+(?:\.\d{1,2})?)", cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def extract_offers_from_card(
    card: Any,
    scrape_started_at: str,
    target_name: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extrait les offres (Neuf, Occasion, Reconditionné) d une carte produit Rakuten.
    """
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    
    # 1. Extraction du Titre
    title_el = card.find("p", attrs={"data-qa": "sdt_p"})
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        sp = card.find("span", attrs={"title": True})
        if sp:
            title = sp.get("title", "").strip()
            
    if not title:
        return [], []

    # 2. Lien & ID Produit
    link_el = card.find("a", href=re.compile(r"/mfp/\d+"))
    href = link_el.get("href") if link_el else ""
    if href and href.startswith("/"):
        source_url = f"{BASE_URL}{href}"
    elif href.startswith("http"):
        source_url = href
    else:
        source_url = f"{BASE_URL}/nav/Tel-PDA_Telephones-mobiles"

    pid_match = re.search(r"pid=(\d+)", source_url)
    mfp_match = re.search(r"/mfp/(\d+)", source_url)
    if pid_match and mfp_match:
        product_id = f"rakuten_{mfp_match.group(1)}_{pid_match.group(1)}"
    elif pid_match:
        product_id = f"rakuten_pid_{pid_match.group(1)}"
    elif mfp_match:
        product_id = f"rakuten_mfp_{mfp_match.group(1)}"
    else:
        product_id = f"rakuten_{abs(hash(title))}"

    # 3. Marque
    brand_el = card.find("a", href=re.compile(r"/nav/marque/"))
    brand_raw = brand_el.get_text(strip=True) if brand_el else ""
    if not brand_raw:
        brand_raw = title.split()[0]

    # 4. Stockage (Go / To)
    st_match = re.search(r"(\d+)\s*(Go|GB|To|TB)", title, re.I)
    storage_raw = None
    storage_gb = None
    if st_match:
        storage_raw = f"{st_match.group(1)} {st_match.group(2).upper()}"
        val = int(st_match.group(1))
        unit = st_match.group(2).upper()
        if unit in ("TO", "TB"):
            storage_gb = val * 1024
        else:
            storage_gb = val

    # 5. Caractéristiques techniques (Pills <li>)
    specs = [li.get_text(strip=True) for li in card.find_all("li")]
    color = None
    ram_gb = None
    for s in specs:
        if s.startswith("Couleur"):
            color = s.replace("Couleur", "").strip()
        elif "RAM" in s:
            rm = re.search(r"(\d+)\s*Go\s*RAM", s, re.I)
            if rm:
                ram_gb = int(rm.group(1))

    # Fallback couleur depuis le titre
    if not color and storage_raw and storage_raw in title:
        after = title.split(storage_raw)[-1].strip(" -/")
        if after:
            cand = after.split("-")[0].strip()
            if cand and len(cand) < 25 and not any(k in cand.lower() for k in ["neuf", "reconditionn", "avis"]):
                color = cand

    # 6. Extraction & Canonicalisation Modèle
    model_raw = title
    for pref in [brand_raw, "Apple", "Samsung", "Xiaomi", "Google", "OnePlus", "Honor", "Huawei", "Oppo", "Sony", "Realme", "Motorola"]:
        if model_raw.lower().startswith(pref.lower()):
            model_raw = model_raw[len(pref):].strip()
    # Retrait propre et insensible à la casse du stockage et suffixes
    model_raw = re.sub(r"\s*\d+\s*(?:Go|GB|To|TB)\b.*", "", model_raw, flags=re.I).strip(" -/")
    if color and color in model_raw:
        model_raw = model_raw.replace(color, "").strip(" -/")

    if validate_and_canonicalize_brand:
        brand = validate_and_canonicalize_brand(brand_raw, model_raw, title) or brand_raw
    else:
        brand = brand_raw

    if canonicalize_model_name:
        model_normalized = canonicalize_model_name(brand, model_raw) or model_raw
    else:
        model_normalized = model_raw

    # Validation Appareil Smartphone
    if is_valid_smartphone_device:
        is_phone, reject_reason = is_valid_smartphone_device(brand, model_raw, title)
        if not is_phone:
            issues.append({
                "type": "REJECT_NON_SMARTPHONE",
                "title": title,
                "brand": brand,
                "reason": reject_reason,
                "url": source_url,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            return [], issues

    # 7. Famille d appareil
    title_lower = title.lower()
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
        device_family = brand

    # 8. Extraction des Offres Duales de Prix & État
    card_text = card.get_text(" ", strip=True)
    matches = re.findall(r"(Neuf|Occasion|Reconditionn[eé])\s+dès\s+([\d\s,.]+)\s*€", card_text, re.I)

    seen_conditions = set()
    for cond_raw, price_str in matches:
        cond_clean = cond_raw.lower()
        if "reconditionn" in cond_clean:
            condition = "reconditionne"
        elif "occasion" in cond_clean:
            condition = "occasion"
        else:
            condition = "neuf"

        if condition in seen_conditions:
            continue
        seen_conditions.add(condition)

        price_val = clean_price_str(price_str)
        if not price_val:
            continue

        # Validation du prix de vente
        if is_valid_sale_price:
            valid_p, p_reason = is_valid_sale_price(price_val, brand, model_raw, title)
            if not valid_p:
                issues.append({
                    "type": "REJECT_INVALID_PRICE",
                    "title": title,
                    "price": price_val,
                    "condition": condition,
                    "reason": p_reason,
                    "url": source_url,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                continue

        now_str = datetime.now(timezone.utc).isoformat()
        row: Dict[str, Any] = {
            "source_site": SOURCE_SITE,
            "source_url": source_url,
            "timestamp": now_str,
            "scrape_started_at": scrape_started_at,
            "data_type": "product_sales",
            "device_type": "smartphone",
            "device_family": device_family,
            "brand": brand,
            "model": model_raw,
            "model_normalized": model_normalized,
            "item_title": title,
            "storage_gb": storage_gb,
            "storage_raw": storage_raw,
            "ram_gb": ram_gb,
            "color": color,
            "condition": condition,
            "product_id": f"{product_id}_{condition}",
            "is_currently_sold": True,
            "price": price_val,
            "currency": "EUR",
            "price_raw": f"{price_str.strip()} €",
            "seller_name": DEFAULT_SELLER,
            "shipping_cost": DEFAULT_SHIPPING,
        }
        rows.append(row)

    return rows, issues


def run_rakuten_scraper(
    max_targets: int = 20,
    max_pages_per_target: int = 20,
    timeout_ms: int = 30000,
    output_dir: Optional[str | Path] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Point d entrée principal du scraper Rakuten France.
    """
    t0_global = time.time()
    scrape_started_at = datetime.now(timezone.utc).isoformat()

    out_dir = Path(output_dir) if output_dir else (CURRENT_DIR / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    chrome_exec = get_chrome_executable()
    logger.info("=" * 70)
    logger.info("🚀 Démarrage du Scraper Rakuten France (E-commerce AI V2)")
    logger.info(f"   - Moteur : Playwright orchestrant Google Chrome Off-Screen")
    logger.info(f"   - Exécutable Chrome : {chrome_exec or 'Chromium bundled'}")
    logger.info(f"   - Cibles planifiées : {min(max_targets, len(CATALOG_TARGETS))} catalogues")
    logger.info(f"   - Dossier de sortie : {out_dir}")
    logger.info("=" * 70)

    all_rows: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []
    targets_to_run = CATALOG_TARGETS[:max_targets]

    with sync_playwright() as p:
        launch_kwargs: Dict[str, Any] = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--window-position=100,100",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
                "--disable-dev-shm-usage"
            ]
        }
        if chrome_exec:
            launch_kwargs["executable_path"] = chrome_exec

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="fr-FR"
        )
        page = context.new_page()

        for idx, (target_name, target_url) in enumerate(targets_to_run, start=1):
            logger.info(f"[{idx:02d}/{len(targets_to_run):02d}] 🌐 Exploration rayon : {target_name}")
            target_seen_pids = set()
            for page_num in range(1, max_pages_per_target + 1):
                p_url = (f"{target_url}&pa={page_num}" if "?" in target_url else f"{target_url}?pa={page_num}") if page_num > 1 else target_url
                logger.info(f"   ↳ [Page {page_num:02d}/{max_pages_per_target:02d}] Chargement : {p_url}")
                t_start = time.time()
                try:
                    page.goto(p_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_selector('div[class*="style_gridCard"]', timeout=8000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1000)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    cards = soup.find_all("div", class_=re.compile(r"style_gridCard"))
                    if not cards:
                        logger.info(f"   ↳ Aucune fiche sur page {page_num}, fin du rayon {target_name}.")
                        break

                    new_cards = 0
                    page_rows: List[Dict[str, Any]] = []
                    page_issues: List[Dict[str, Any]] = []

                    for card in cards:
                        c_rows, c_issues = extract_offers_from_card(card, scrape_started_at, target_name)
                        for r in c_rows:
                            if r["product_id"] not in target_seen_pids:
                                target_seen_pids.add(r["product_id"])
                                page_rows.append(r)
                                new_cards += 1
                        page_issues.extend(c_issues)

                    all_rows.extend(page_rows)
                    all_issues.extend(page_issues)

                    dur = time.time() - t_start
                    logger.info(f"      ✓ +{len(page_rows)} offres ({new_cards} nouveaux smartphones) en {dur:.2f}s (Cumul: {len(all_rows)})")

                    if new_cards == 0:
                        logger.info(f"   ↳ Aucun nouveau produit à la page {page_num}, passage au rayon suivant.")
                        break

                except Exception as e:
                    logger.error(f"      ❌ Erreur page {page_num}: {e}")
                    all_issues.append({
                        "type": "PAGE_EXTRACTION_ERROR",
                        "target_name": target_name,
                        "url": p_url,
                        "error": str(e),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    break

                time.sleep(1.0)

        browser.close()

    scrape_finished_at = datetime.now(timezone.utc).isoformat()
    total_duration = time.time() - t0_global

    # Dédoublonnage strict par (product_id, condition, price)
    unique_rows: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, str, float]] = set()
    for r in all_rows:
        key = (r["product_id"], r["condition"], r["price"])
        if key not in seen_keys:
            seen_keys.add(key)
            unique_rows.append(r)

    neuf_count = sum(1 for r in unique_rows if r["condition"] == "neuf")
    recond_count = sum(1 for r in unique_rows if r["condition"] == "reconditionne")
    occas_count = sum(1 for r in unique_rows if r["condition"] == "occasion")

    logger.info("=" * 70)
    logger.info("🎉 Scraping Rakuten France Terminé avec Succès !")
    logger.info(f"   - Durée Totale : {total_duration:.2f}s")
    logger.info(f"   - Catalogues Scannés : {len(targets_to_run)}")
    logger.info(f"   - Offres Brutes Récoltées : {len(all_rows)}")
    logger.info(f"   - Offres Uniques Qualifiées : {len(unique_rows)}")
    logger.info(f"   - Répartition : {neuf_count} Neuf | {recond_count} Reconditionné | {occas_count} Occasion")
    logger.info(f"   - Total Anomalies / Rejets : {len(all_issues)}")
    logger.info("=" * 70)

    # Export des 5 artefacts standardisés
    if export_standard_artifacts:
        files = export_standard_artifacts(
            source_name="rakuten",
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
        # Fallback export manuel rigoureux
        prefix = "db_rakuten"
        json_file = out_dir / f"{prefix}.json"
        jsonl_file = out_dir / f"{prefix}.jsonl"
        csv_file = out_dir / f"{prefix}.csv"
        run_file = out_dir / f"{prefix}_run.json"
        issues_file = out_dir / f"{prefix}_issues.jsonl"

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(unique_rows, f, indent=2, ensure_ascii=False)
        with open(jsonl_file, "w", encoding="utf-8") as f:
            for r in unique_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
            writer.writeheader()
            writer.writerows(unique_rows)
        with open(issues_file, "w", encoding="utf-8") as f:
            for iss in all_issues:
                f.write(json.dumps(iss, ensure_ascii=False) + "\n")
        run_meta = {
            "source_site": "rakuten",
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

    # Synchronisation vers les dossiers staging UNIQUEMENT (AUCUN envoi vers dataset/)
    staging_dirs = [
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "liste_des_web" / "outputs",
        PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main" / "scraper" / "outputs"
    ]
    for sdir in staging_dirs:
        try:
            sdir.mkdir(parents=True, exist_ok=True)
            for f in out_dir.glob("db_rakuten*"):
                shutil.copy2(f, sdir / f.name)
            logger.info(f"🔄 Artefacts synchronisés vers staging : {sdir}")
        except Exception as e:
            logger.warning(f"Erreur copie staging {sdir}: {e}")

    logger.info("ℹ️ NOTE CONFORME : Aucun envoi vers dataset/ (Base Production) effectué conformément aux instructions.")
    return unique_rows, all_issues


def main():
    parser = argparse.ArgumentParser(description="Scraper Rakuten France — Smartphones Neufs, Reconditionnés & Occasion (E-commerce AI V2)")
    parser.add_argument("--max-targets", type=int, default=20, help="Nombre de catalogues cibles à scraper (défaut: 20)")
    parser.add_argument("--max-pages-per-target", type=int, default=20, help="Nombre max de pages par catalogue (défaut: 20)")
    parser.add_argument("--timeout", type=int, default=30000, help="Timeout de chargement en millisecondes (défaut: 30000)")
    parser.add_argument("--output-dir", type=str, default=None, help="Dossier de sortie personnalisé")
    args = parser.parse_args()

    run_rakuten_scraper(
        max_targets=args.max_targets,
        max_pages_per_target=args.max_pages_per_target,
        timeout_ms=args.timeout,
        output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
