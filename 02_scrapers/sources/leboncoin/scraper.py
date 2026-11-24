#!/usr/bin/env python3
"""
scraper_leboncoin.py
====================
Scraper haute performance & furtif pour Leboncoin (Smartphones Occasion & Vente).
Basé sur la stratégie validée de Darty (SeleniumBase UC Mode + uc_open_with_reconnect).

Stratégie anti-bot :
- Undetected ChromeDriver (UC Mode) avec headed=True masqué
- Reconnexion furtive (uc_open_with_reconnect) pour contourner DataDome / CloudFront
- Résolution automatique du slider captcha DataDome (uc_gui_handle_captcha)
- Défilement progressif humain naturel (smooth scroll) avec micro-pauses aléatoires
- Pause aléatoire humaine inter-pages (anti-burst rate limit)
- Extraction directe des données structurées natives Next.js (__NEXT_DATA__)
- Sauvegarde incrémentale à chaque lot de pages (zéro perte de données)
- Univers : product_sales (23 colonnes standardisées E-commerce AI V2)

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_leboncoin.json
  2. db_leboncoin.jsonl
  3. db_leboncoin.csv
  4. db_leboncoin_run.json
  5. db_leboncoin_issues.jsonl
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
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from seleniumbase import SB

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT / "unit_scrapers"))
sys.path.insert(0, str(PROJECT_ROOT / "ecommerce_ai_scraping_scraper_V2-main"))

try:
    from common_export import export_standard_artifacts
except ImportError:
    export_standard_artifacts = None

SOURCE_SITE = "leboncoin.fr"
BASE_URL = "https://www.leboncoin.fr"
TARGET_CATEGORY_URL = "https://www.leboncoin.fr/c/telephones_objets_connectes"
MAX_LEBONCOIN_PAGES = 100  # Plafond technique maximal exposé par l'API/pagination de Leboncoin

PRODUCT_SALE_KEYS = [
    "source_site", "source_url", "timestamp", "scrape_started_at", "data_type",
    "device_type", "device_family", "brand", "model", "model_normalized",
    "item_title", "storage_gb", "storage_raw", "ram_gb", "color", "condition",
    "product_id", "is_currently_sold", "price", "currency", "price_raw",
    "seller_name", "shipping_cost"
]

CONDITION_MAP = {
    "neuf": "new",
    "etatneuf": "new",
    "comme_neuf": "refurbished",
    "tresbonetat": "used",
    "bonetat": "used",
    "etatcorrect": "used",
    "pour_pieces": "used",
}

BLACKLIST_TITLE_WORDS = [
    "reparation", "réparation", "forfait", "vitre tactile", "bloc écran", "bloc ecran",
    "chassis", "connecteur", "nappe", "camera arriere", "haut-parleur",
    "coque", "housse", "chargeur", "cable", "câble", "film", "verre trempe", "verre trempé",
    "airpod", "montre", "watch", "bracelet", "pièce", "piece", "écran seul", "batterie seule",
    "boite vide", "boîte vide", "accessoire", "support", "adaptateur", "fixe", "sans fil", "gigaset",
    "dect", "combiné", "interphone"
]

KNOWN_BRANDS = [
    "Apple", "Samsung", "Xiaomi", "Google", "Huawei", "Honor", "Oppo",
    "OnePlus", "Sony", "Motorola", "Nokia", "Realme", "Vivo", "Nothing",
    "Asus", "Crosscall", "POCO", "Blackview", "TCL", "Fairphone", "Alcatel"
]


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def hide_browser_window(sb) -> None:
    """Minimise ou déplace la fenêtre du navigateur pour ne pas gêner l'utilisateur."""
    try:
        sb.driver.minimize_window()
    except Exception:
        try:
            sb.execute_script("window.moveTo(-3000, 0);")
        except Exception:
            pass


def handle_datadome_slider(sb) -> None:
    """Détecte et résout automatiquement le challenge DataDome slider."""
    try:
        if (
            sb.is_text_visible("Faites glisser")
            or sb.is_element_visible("div[class*='slider']")
            or sb.is_element_visible("#sec-cpt")
        ):
            print(f"[{SOURCE_SITE}] Challenge DataDome Slider détecté. Résolution en cours...", flush=True)
            if sys.platform == "darwin":
                sb.execute_script("window.focus();")
            sb.sleep(1)
            sb.uc_gui_handle_captcha()
            sb.sleep(3)
    except Exception as e:
        print(f"[{SOURCE_SITE}] Notice slider DataDome : {e}", flush=True)


def human_like_progressive_scroll(sb) -> None:
    """
    Exécute un défilement progressif et naturel vers le bas avec paliers aléatoires,
    suivi d'un léger réajustement vers le haut, simulant fidèlement la lecture humaine.
    """
    try:
        total_height = sb.execute_script(
            "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 2500);"
        )
        steps = random.randint(3, 5)
        for step in range(1, steps + 1):
            fraction = (step / steps) * random.uniform(0.85, 1.02)
            target_y = int(min(total_height, total_height * fraction))
            sb.execute_script(f"window.scrollTo({{top: {target_y}, behavior: 'smooth'}});")
            pause = random.uniform(0.35, 0.75)
            sb.sleep(pause)
            handle_datadome_slider(sb)

        # Micro-ajustement humain vers le haut (200 à 400px)
        adjust_up = random.randint(200, 450)
        sb.execute_script(f"window.scrollBy({{top: -{adjust_up}, behavior: 'smooth'}});")
        sb.sleep(random.uniform(0.3, 0.6))
    except Exception:
        pass


def parse_storage(memory_str: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    if not memory_str:
        return None, None
    m = re.search(r"(\d+)\s*(?:go|gb|giga)", memory_str, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if 4 <= val <= 1024:
            return val, f"{val}GB"
    return None, memory_str


def parse_leboncoin_ad(ad: Dict[str, Any], started_at: str) -> Optional[Dict[str, Any]]:
    subject = ad.get("subject", "").strip()
    if not subject:
        return None

    subj_low = subject.lower()
    if any(w in subj_low for w in BLACKLIST_TITLE_WORDS):
        return None

    attrs_dict = {
        at.get("key"): (at.get("value_label") or at.get("value"))
        for at in ad.get("attributes", [])
        if at.get("key")
    }

    # Filtrer si le champ phone_product exclut explicitement le smartphone
    phone_prod = (attrs_dict.get("phone_product") or "").lower()
    if phone_prod and phone_prod not in ("smartphone", "telephone", "téléphone", "mobile"):
        return None

    brand = attrs_dict.get("phone_brand")
    model = attrs_dict.get("phone_model")
    memory_raw = attrs_dict.get("phone_memory")
    color = attrs_dict.get("phone_color")
    cond_raw = (attrs_dict.get("condition") or "").lower()

    if not brand or brand.lower() in ("autre", "none"):
        matched_brand = None
        for b in KNOWN_BRANDS:
            if re.search(rf"\b{re.escape(b)}\b", subject, re.IGNORECASE):
                matched_brand = b
                break
        if matched_brand:
            brand = matched_brand
        elif "iphone" in subj_low:
            brand = "Apple"
        elif "galaxy" in subj_low:
            brand = "Samsung"
        elif "redmi" in subj_low or "poco" in subj_low:
            brand = "Xiaomi"
        elif "pixel" in subj_low:
            brand = "Google"
        else:
            if "smartphone" not in subj_low and "téléphone" not in subj_low:
                return None
            brand = "Générique"

    if not model or model.lower() in ("autre", "none"):
        model = subject

    storage_gb, storage_raw = parse_storage(memory_raw)
    if not storage_gb:
        storage_gb, storage_raw = parse_storage(subject)

    condition = CONDITION_MAP.get(cond_raw, "used")

    raw_price = ad.get("price")
    if isinstance(raw_price, list) and raw_price:
        price_val = float(raw_price[0])
    elif isinstance(raw_price, (int, float)):
        price_val = float(raw_price)
    else:
        return None

    if price_val < 20.0 or price_val > 3500.0:
        return None

    ad_url = ad.get("url") or f"https://www.leboncoin.fr/ad/telephones_objets_connectes/{ad.get('list_id')}"

    location = ad.get("location", {})
    city = location.get("city") or location.get("city_label") or "France"
    owner = ad.get("owner", {})
    owner_type = "Pro" if owner.get("type") == "pro" else "Particulier"
    seller_name = f"{owner.get('name', owner_type)} ({city})"

    return {
        "source_site": SOURCE_SITE,
        "source_url": ad_url,
        "timestamp": now_iso_utc(),
        "scrape_started_at": started_at,
        "data_type": "product_sales",
        "device_type": "smartphone",
        "device_family": brand.lower(),
        "brand": brand,
        "model": model,
        "model_normalized": model.lower(),
        "item_title": subject,
        "storage_gb": storage_gb,
        "storage_raw": storage_raw,
        "ram_gb": None,
        "color": color if color and color.lower() != "autre" else None,
        "condition": condition,
        "product_id": str(ad.get("list_id", "")),
        "is_currently_sold": True,
        "price": price_val,
        "currency": "EUR",
        "price_raw": f"{price_val:.2f} €",
        "seller_name": seller_name,
        "shipping_cost": "0.0 € (Remise en main propre / Livraison leboncoin)"
    }


def save_checkpoint(
    output_dir: Path,
    all_rows: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
    started_at: str
) -> None:
    """Sauvegarde incrémentale pour éviter toute perte de données en cours de route."""
    output_dir.mkdir(parents=True, exist_ok=True)
    finished_at = now_iso_utc()
    if export_standard_artifacts:
        export_standard_artifacts(
            source_name="leboncoin",
            domain="product_sales",
            rows=all_rows,
            output_dir=output_dir,
            issues=issues,
            scrape_started_at=started_at,
            scrape_finished_at=finished_at
        )
    _flush_direct_artifacts(output_dir, all_rows, issues, started_at, finished_at)


def _flush_direct_artifacts(output_dir: Path, rows: List[Dict[str, Any]], issues: List[Dict[str, Any]], started_at: str, finished_at: str):
    prefix = "db_leboncoin"
    standardized_rows = []
    for r in rows:
        item = {k: r.get(k) for k in PRODUCT_SALE_KEYS}
        standardized_rows.append(item)

    # Export JSON avec flush disque
    with open(output_dir / f"{prefix}.json", "w", encoding="utf-8") as f:
        json.dump(standardized_rows, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

    # Export JSONL
    with open(output_dir / f"{prefix}.jsonl", "w", encoding="utf-8") as f:
        for r in standardized_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

    # Export CSV
    with open(output_dir / f"{prefix}.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRODUCT_SALE_KEYS)
        writer.writeheader()
        writer.writerows(standardized_rows)
        f.flush()
        os.fsync(f.fileno())

    run_data = {
        "source_site": SOURCE_SITE,
        "data_domain": "product_sales",
        "scrape_started_at": started_at,
        "scrape_finished_at": finished_at,
        "status": "in_progress" if len(rows) > 0 else "empty",
        "row_count": len(standardized_rows),
        "columns_count": len(PRODUCT_SALE_KEYS),
        "columns": PRODUCT_SALE_KEYS,
        "paths": {
            "json": str(output_dir / f"{prefix}.json"),
            "jsonl": str(output_dir / f"{prefix}.jsonl"),
            "csv": str(output_dir / f"{prefix}.csv"),
            "run_json": str(output_dir / f"{prefix}_run.json"),
            "issues_jsonl": str(output_dir / f"{prefix}_issues.jsonl")
        },
        "counts": {
            "fetch_errors": len(issues),
            "issues_total": len(issues)
        }
    }
    with open(output_dir / f"{prefix}_run.json", "w", encoding="utf-8") as f:
        json.dump(run_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    with open(output_dir / f"{prefix}_issues.jsonl", "w", encoding="utf-8") as f:
        for iss in issues:
            f.write(json.dumps(iss, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def scrape(
    output_dir: Path,
    max_pages: int = 0,
    reconnect_time: int = 4
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    started_at = now_iso_utc()
    target_pages = MAX_LEBONCOIN_PAGES if max_pages <= 0 else min(max_pages, MAX_LEBONCOIN_PAGES)

    print("=" * 80)
    print("🚀 [E-commerce AI Scraping Scraper V2] Démarrage extraction Leboncoin Intégrale (Stratégie Darty UC + Scroll Humain)")
    print(f"🌐 Cible : {TARGET_CATEGORY_URL}")
    print(f"🛡️  Mode furtif : SeleniumBase UC Mode + Reconnexion CloudFront/DataDome")
    print(f"📜 Comportement : Défilement progressif humain + pause inter-page aléatoire")
    print(f"📄 Pages ciblées : {target_pages} (Plafond maximal disponible)")
    print("=" * 80)

    all_rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    consecutive_empty = 0

    with SB(uc=True, headed=True, locale_code="fr-FR") as sb:
        for page_num in range(1, target_pages + 1):
            url = f"{TARGET_CATEGORY_URL}/page-{page_num}" if page_num > 1 else TARGET_CATEGORY_URL
            rec_time = reconnect_time if page_num == 1 else max(3, reconnect_time - 1)
            print(f"[{SOURCE_SITE}] [Page {page_num}/{target_pages}] Navigation : {url}", flush=True)

            try:
                sb.uc_open_with_reconnect(url, reconnect_time=rec_time)
                sb.sleep(1.0)
                hide_browser_window(sb)
                handle_datadome_slider(sb)

                # Bannière cookies sur la page 1
                if page_num == 1:
                    try:
                        if sb.is_element_visible("#didomi-notice-agree-button"):
                            sb.click("#didomi-notice-agree-button")
                            sb.sleep(1)
                    except Exception:
                        pass

                # Défilement progressif naturel (human smooth scroll) avec micro-pauses
                human_like_progressive_scroll(sb)

                html = sb.get_page_source()
                soup = BeautifulSoup(html, "html.parser")
                next_data_el = soup.select_one("#__NEXT_DATA__")

                if not next_data_el or not next_data_el.string:
                    print(f"[{SOURCE_SITE}] ⚠️ __NEXT_DATA__ non trouvé sur la page {page_num}", flush=True)
                    issues.append({
                        "severity": "WARNING",
                        "issue_type": "NO_NEXT_DATA",
                        "page": page_num,
                        "url": url,
                        "message": "Balise __NEXT_DATA__ absente"
                    })
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        print(f"[{SOURCE_SITE}] 2 pages consécutives sans données, fin de la pagination.", flush=True)
                        break
                    continue

                payload = json.loads(next_data_el.string)
                search_data = payload.get("props", {}).get("pageProps", {}).get("searchData", {})
                raw_ads = search_data.get("ads", [])
                total_count = search_data.get("total", 0)

                if not raw_ads:
                    print(f"[{SOURCE_SITE}] Page {page_num} : 0 annonce retournée par Leboncoin.", flush=True)
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        print(f"[{SOURCE_SITE}] Fin de pagination atteinte à la page {page_num}.", flush=True)
                        break
                    continue
                else:
                    consecutive_empty = 0

                page_added = 0
                for ad in raw_ads:
                    parsed = parse_leboncoin_ad(ad, started_at)
                    if parsed and parsed["product_id"] not in seen_ids:
                        seen_ids.add(parsed["product_id"])
                        all_rows.append(parsed)
                        page_added += 1

                print(
                    f"[{SOURCE_SITE}] Page {page_num} : {len(raw_ads)} annonces brutes reçues "
                    f"(Total catalogue : {total_count:,}) -> +{page_added} smartphones qualifiés (Total cumulé : {len(all_rows)})",
                    flush=True
                )

                # Sauvegarde incrémentale toutes les 3 pages pour garantir la persistance continue
                if page_num % 3 == 0 or page_num == 1:
                    save_checkpoint(output_dir, all_rows, issues, started_at)

                # Pause humaine anti-rate-limit aléatoire entre 3.0s et 5.5s
                inter_page_delay = random.uniform(3.0, 5.5)
                print(f"[{SOURCE_SITE}] Pause humaine avant page suivante ({inter_page_delay:.1f}s)...", flush=True)
                sb.sleep(inter_page_delay)

            except Exception as e:
                print(f"[{SOURCE_SITE}] ❌ Erreur sur la page {page_num} : {e}", flush=True)
                issues.append({
                    "severity": "ERROR",
                    "issue_type": "PAGE_ERROR",
                    "page": page_num,
                    "url": url,
                    "message": str(e)
                })
                save_checkpoint(output_dir, all_rows, issues, started_at)
                sb.sleep(4)

    # Sauvegarde finale intégrale
    save_checkpoint(output_dir, all_rows, issues, started_at)
    _print_summary(output_dir, all_rows, issues)
    return all_rows, issues


def _print_summary(output_dir: Path, rows: List[Dict[str, Any]], issues: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 80)
    print("📊 RÉCAPITULATIF DE L'EXTRACTION — LEBONCOIN INTÉGRAL (AVEC SCROLL HUMAIN)")
    print("=" * 80)
    print(f"  Total smartphones extraits : {len(rows)}")
    apple_cnt = sum(1 for r in rows if r["brand"] == "Apple")
    samsung_cnt = sum(1 for r in rows if r["brand"] == "Samsung")
    xiaomi_cnt = sum(1 for r in rows if r["brand"] == "Xiaomi")
    google_cnt = sum(1 for r in rows if r["brand"] == "Google")
    other_cnt = len(rows) - (apple_cnt + samsung_cnt + xiaomi_cnt + google_cnt)
    print(f"  🍎 Apple                   : {apple_cnt}")
    print(f"  📱 Samsung                 : {samsung_cnt}")
    print(f"  ⚡ Xiaomi                  : {xiaomi_cnt}")
    print(f"  🔍 Google Pixel            : {google_cnt}")
    print(f"  📦 Autres marques          : {other_cnt}")
    if rows:
        prices = [r["price"] for r in rows]
        print(f"  💶 Fourchette de prix      : {min(prices):.2f} € — {max(prices):.2f} € (Moy : {sum(prices)/len(prices):.2f} €)")
    print(f"  ⚠️ Alertes / Incidents     : {len(issues)}")
    print("-" * 80)
    print(f"  📁 Fichiers générés dans   : {output_dir.resolve()}/")
    print("     - db_leboncoin.json")
    print("     - db_leboncoin.jsonl")
    print("     - db_leboncoin.csv")
    print("     - db_leboncoin_run.json")
    print("     - db_leboncoin_issues.jsonl")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Scraper Leboncoin Smartphones (Stratégie Darty UC + Scroll Humain)")
    parser.add_argument("--output-dir", type=Path, default=CURRENT_DIR / "outputs", help="Dossier de sortie des artefacts")
    parser.add_argument("--max-pages", type=int, default=0, help="Nombre max de pages à scraper (0 = toutes les pages jusqu'au plafond)")
    parser.add_argument("--reconnect-time", type=int, default=4, help="Délai de reconnexion UC en secondes")
    args = parser.parse_args()

    rows, issues = scrape(
        output_dir=args.output_dir,
        max_pages=args.max_pages,
        reconnect_time=args.reconnect_time
    )
    sys.exit(0 if rows else 1)


if __name__ == "__main__":
    main()
