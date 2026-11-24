"""Pulsotech Scraper (https://pulsotech.fr).

Atelier de réparation informatique et multimédia à Dijon :
- Univers : repair_price (Tarifs de réparation smartphones, tablettes, Mac/PC, consoles)
- Racine catalogue : https://pulsotech.fr/repair-shop/public/
- Stratégie d'extraction : Exploration du catalogue en arborescence récursive et parsing des cartes de réparation
- Données : prestations avec prix fixes TTC, durée d'intervention et garantie 6 mois
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
if str(_SCRAPERS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT.parent))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

try:
    from scraper.core.filtering import CleaningConfig, clean_dataset
    from scraper.core.parser_utils import dedupe_dict_rows, now_iso_utc
    from scraper.core.row_export import write_jsonl_and_csv
    from scraper.core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )
except ImportError:
    from core.filtering import CleaningConfig, clean_dataset
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

SOURCE_NAME = "pulsotech"
SOURCE_DOMAIN = "pulsotech.fr"
CATALOG_ROOT = "https://pulsotech.fr/repair-shop/public/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

BRAND_MAP = {
    "apple": "Apple",
    "apple-phone": "Apple",
    "samsung": "Samsung",
    "samsung-phone": "Samsung",
    "huawei": "Huawei",
    "huawei-phone": "Huawei",
    "xiaomi": "Xiaomi",
    "xiaomi-phone": "Xiaomi",
    "oneplus": "OnePlus",
    "google": "Google",
    "google-pixel": "Google",
    "sony": "Sony",
    "sony-phone": "Sony",
    "oppo": "Oppo",
    "motorola": "Motorola",
    "vivo": "Vivo",
    "nokia": "Nokia",
    "microsoft": "Microsoft",
    "microsoft-surface": "Microsoft",
    "nintendo": "Nintendo",
    "playstation": "Sony",
    "xbox": "Microsoft",
}

CANONICAL_REPAIR_MAP = {
    "ecran": "Écran",
    "écran": "Écran",
    "oled": "Écran",
    "lcd": "Écran",
    "batterie": "Batterie",
    "charge": "Connecteur de charge",
    "connecteur": "Connecteur de charge",
    "usb": "Connecteur de charge",
    "vitre": "Façade arrière",
    "arrière": "Façade arrière",
    "arriere": "Façade arrière",
    "camera": "Caméra",
    "caméra": "Caméra",
    "dégât": "Désoxydation",
    "eau": "Désoxydation",
    "ultrason": "Désoxydation",
    "haut-parleur": "Audio / Haut-parleur",
    "micro": "Audio / Haut-parleur",
    "face id": "Module biométrique / Face ID",
    "power": "Boutons & Nappes",
    "bouton": "Boutons & Nappes",
    "soudure": "Micro-soudure",
}


def get_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(pool_connections=15, pool_maxsize=15, max_retries=retries))
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    return s


def map_repair_category(repair_name: str) -> str:
    low = repair_name.lower()
    for kw, cat in CANONICAL_REPAIR_MAP.items():
        if kw in low:
            return cat
    return "Autre réparation"


def crawl_catalog_pages(session: requests.Session) -> list[str]:
    visited = set()
    to_visit = deque([CATALOG_ROOT])
    leaf_pages: list[str] = []

    while to_visit:
        url = to_visit.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            r = session.get(url, timeout=10)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".repair-card")
            if cards:
                leaf_pages.append(url)

            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if "/repair-shop/public/" in href:
                    full = href if href.startswith("https://") else f"https://pulsotech.fr{href}"
                    full = full.split("#")[0].split("?")[0]
                    if not full.endswith("/"):
                        full += "/"
                    if full not in visited and full not in to_visit and full.startswith("https://pulsotech.fr/repair-shop/public/"):
                        to_visit.append(full)
        except Exception:
            pass

    return leaf_pages


def parse_leaf_page(url: str, html: str, scraped_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    h1 = soup.select_one("h1")
    raw_h1 = h1.get_text(strip=True) if h1 else ""
    model_name = raw_h1
    for prefix in ["Réparation", "Reparation", "Forfait"]:
        model_name = re.sub(rf"^\b{prefix}\b", "", model_name, flags=re.I).strip()

    # Déterminer brand et device_family d'après l'URL
    # URL format: /repair-shop/public/<device_family_path>/<brand_path>/<series>/<model>/
    parts = url.replace("https://pulsotech.fr/repair-shop/public/", "").strip("/").split("/")
    device_path = parts[0] if len(parts) > 0 else ""
    brand_path = parts[1] if len(parts) > 1 else ""

    if device_path == "smartphone":
        device_type = "smartphone"
        device_family = "Smartphone"
    elif device_path == "tablettes":
        device_type = "tablette"
        device_family = "Tablette"
    elif device_path in ["ordinateur", "pc"]:
        device_type = "ordinateur"
        device_family = "Ordinateur"
    elif device_path == "console":
        device_type = "console"
        device_family = "Console"
    else:
        device_type = "multimedia"
        device_family = "Appareil multimédia"

    brand = BRAND_MAP.get(brand_path.lower(), "")
    if not brand:
        for b_key, b_val in BRAND_MAP.items():
            if b_key in model_name.lower():
                brand = b_val
                break
    if not brand:
        brand = brand_path.capitalize() if brand_path else "Générique"

    cards = soup.select(".repair-card")
    for card in cards:
        name_el = card.select_one(".repair-name, [itemprop='name']")
        repair_raw = name_el.get_text(strip=True) if name_el else ""
        if not repair_raw:
            continue

        price_el = card.select_one(".repair-price")
        price_txt = price_el.get_text(strip=True) if price_el else ""
        m = re.search(r"(\d+[\.,]?\d*)\s*€", price_txt)
        if not m:
            continue
        try:
            price_val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue

        if price_val <= 0:
            continue

        desc_el = card.select_one(".repair-desc, [itemprop='description']")
        desc_txt = desc_el.get_text(strip=True) if desc_el else ""

        meta_tags = [t.get_text(strip=True) for t in card.select(".repair-meta .meta-tag")]
        delay_txt = next((t for t in meta_tags if "⏱" in t or "min" in t or "h" in t), "1h en atelier")
        warranty_txt = next((t for t in meta_tags if "🛡" in t or "garantie" in t.lower()), "Garantie 6 mois pièces et main-d'œuvre")

        canonical_cat = map_repair_category(repair_raw)
        item_name = f"{repair_raw} {brand} {model_name}".strip()

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": url,
            "source": url,
            "timestamp": scraped_at,
            "scrape_started_at": scraped_at,
            "data_type": "repair_price",
            "device_type": device_type,
            "device_family": device_family,
            "brand": brand,
            "model": model_name or raw_h1,
            "model_normalized": f"{brand} {model_name}".strip() if model_name else raw_h1,
            "item_name": item_name,
            "repair_category": canonical_cat,
            "repair_type": canonical_cat,
            "repair_type_raw": repair_raw,
            "repair_description": desc_txt,
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "workshop_name": "Pulsotech Dijon",
            "seller_name": "Pulsotech",
            "delay": delay_txt,
            "warranty": warranty_txt,
            "scraped_at": scraped_at,
        }
        rows.append(row)

    return rows


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp_clean = scrape_started_at.replace(":", "").replace("-", "").replace("+", "")
    prefix = f"{SOURCE_NAME}_repair_prices_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()

    print(f"[{SOURCE_NAME}] Exploration récursive du catalogue ({CATALOG_ROOT})...")
    leaf_pages = crawl_catalog_pages(session)
    print(f"[{SOURCE_NAME}] Trouvé {len(leaf_pages)} pages de modèles avec cartes de réparation.")

    if max_models:
        leaf_pages = leaf_pages[:max_models]

    print(f"[{SOURCE_NAME}] Extraction concurrente de {len(leaf_pages)} fiches modèles...")
    pages_html: dict[str, str] = {}

    def fetch_page(u: str):
        try:
            r = session.get(u, timeout=12)
            if r.status_code == 200:
                return u, r.text, None
            return u, None, f"HTTP {r.status_code}"
        except Exception as e:
            return u, None, str(e)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_page, u): u for u in leaf_pages}
        for future in as_completed(futures):
            u, html, err = future.result()
            if err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=u, reason=REASON_FETCH_ERROR, message=err))
            elif html:
                pages_html[u] = html

    for u, html in pages_html.items():
        parsed = parse_leaf_page(u, html, scrape_started_at)
        rows.extend(parsed)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=3000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        import shutil
        shutil.copy(jsonl_path, convenience_jsonl)
        shutil.copy(csv_path, convenience_csv)
        shutil.copy(json_path, convenience_json)
    except Exception:
        pass

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} forfaits exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{SOURCE_NAME} Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport")
    parser.add_argument("--max-models", type=int, default=None, help="Max modèles")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
