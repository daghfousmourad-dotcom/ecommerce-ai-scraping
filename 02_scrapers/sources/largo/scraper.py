"""Largo Scraper (https://www.largo.fr).

Plateforme française de vente d'appareils tech reconditionnés (Nantes) :
- Univers : product_sales (Smartphones, tablettes, ordinateurs, montres connectées)
- Sitemap officiel : https://www.largo.fr/sitemap_fr_product_1.xml (1 500+ produits)
- Données structurées : Microdonnées Schema.org Product (LD+JSON) intégrées dans chaque page.

Stratégie d'extraction (Niveau 2 & 3 — Sitemap XML + Pool HTTP Keep-Alive & JSON-LD) :
    1. Récupération des URLs de produits via /sitemap_fr_product_1.xml
    2. Extraction concurrente avec requests.Session, pooling de connexions et retries
    3. Parsing des blocs de données structurées LD+JSON (@type: Product)
    4. Normalisation standard E-commerce AI V2 (product_sales)
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
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

BASE_URL = "https://www.largo.fr"
SOURCE_NAME = "largo"
SOURCE_DOMAIN = "largo.fr"
SITEMAP_PRODUCTS_URL = f"{BASE_URL}/sitemap_fr_product_1.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STORAGE_RE = re.compile(r"(\d+)\s*(?:Go|GB|To|TB)", re.I)


def get_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    })
    return s


def fetch_product(url: str, session: requests.Session) -> tuple[str, str | None, str | None]:
    try:
        resp = session.get(url, timeout=12)
        if resp.status_code == 200:
            return url, resp.text, None
        return url, None, f"HTTP {resp.status_code}"
    except Exception as e:
        return url, None, str(e)


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
    prefix = f"{SOURCE_NAME}_product_sales_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"
    run_path = out_dir / f"{prefix}_run.json"
    issues_path = out_dir / f"{prefix}_issues.jsonl"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()

    print(f"[{SOURCE_NAME}] Téléchargement du sitemap ({SITEMAP_PRODUCTS_URL})...")
    product_urls: list[str] = []
    try:
        resp = session.get(SITEMAP_PRODUCTS_URL, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text and elem.text.endswith(".html"):
                    product_urls.append(elem.text.strip())
        else:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=SITEMAP_PRODUCTS_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur sitemap: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=SITEMAP_PRODUCTS_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    product_urls = list(dict.fromkeys(product_urls))
    print(f"[{SOURCE_NAME}] Trouvé {len(product_urls)} fiches produits dans le sitemap.")

    if max_models:
        product_urls = product_urls[:max_models]

    print(f"[{SOURCE_NAME}] Scraping concurrent de {len(product_urls)} fiches avec pooling keep-alive...")
    pages_html: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_product, url, session): url for url in product_urls}
        for future in as_completed(futures):
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=error))
            elif html:
                pages_html[url] = html

    for url, html in pages_html.items():
        soup = BeautifulSoup(html, "html.parser")
        product_schema: dict[str, Any] | None = None

        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.get_text())
                if isinstance(data, dict) and data.get("@type") == "Product":
                    product_schema = data
                    break
            except Exception:
                continue

        if not product_schema:
            continue

        raw_title = str(product_schema.get("name", "")).strip()
        raw_brand = product_schema.get("brand")
        brand = raw_brand.get("name", "") if isinstance(raw_brand, dict) else str(raw_brand or "")
        brand = brand.replace("Reconditionné", "").strip()
        sku = str(product_schema.get("sku", "")).strip()

        offers = product_schema.get("offers", {})
        price_val: float | None = None
        availability = "InStock"
        if isinstance(offers, dict):
            p = offers.get("price")
            if p:
                try:
                    price_val = float(str(p).replace(",", "."))
                except ValueError:
                    price_val = None
            avail = offers.get("availability", "")
            if "OutOfStock" in str(avail):
                availability = "OutOfStock"

        if not price_val or price_val <= 0:
            continue

        # Stockage
        storage_gb: int | None = None
        storage_raw: str | None = None
        sm = STORAGE_RE.search(raw_title)
        if sm:
            storage_raw = sm.group(0).strip()
            try:
                num = int(sm.group(1))
                if "to" in storage_raw.lower() or "tb" in storage_raw.lower():
                    storage_gb = num * 1024
                else:
                    storage_gb = num
            except ValueError:
                pass

        # Catégorie d'appareil
        title_lower = raw_title.lower()
        if any(k in title_lower for k in ["macbook", "imac", "ordinateur", "pc", "laptop"]):
            device_type = "ordinateur"
            device_family = "Ordinateur"
        elif any(k in title_lower for k in ["ipad", "tablette", "tab"]):
            device_type = "tablette"
            device_family = "Tablette"
        elif any(k in title_lower for k in ["watch", "montre"]):
            device_type = "smartwatch"
            device_family = "Montre connectée"
        else:
            device_type = "smartphone"
            device_family = "Smartphone"

        if not brand:
            if "iphone" in title_lower or "macbook" in title_lower or "ipad" in title_lower or "apple" in title_lower:
                brand = "Apple"
            elif "galaxy" in title_lower or "samsung" in title_lower:
                brand = "Samsung"
            elif "pixel" in title_lower or "google" in title_lower:
                brand = "Google"
            elif "huawei" in title_lower:
                brand = "Huawei"
            elif "xiaomi" in title_lower or "redmi" in title_lower:
                brand = "Xiaomi"

        model = raw_title
        for remove_word in ["Reconditionné", "Reconditionnee", "reconditionné"]:
            model = model.replace(remove_word, "").strip()

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": url,
            "source": url,
            "timestamp": scrape_started_at,
            "scrape_started_at": scrape_started_at,
            "data_type": "product_sale",
            "device_type": device_type,
            "device_family": device_family,
            "brand": brand,
            "model": model,
            "model_normalized": f"{brand} {model}".strip(),
            "item_title": raw_title,
            "storage_gb": storage_gb,
            "storage_raw": storage_raw,
            "condition": "reconditionne",
            "product_id": sku or url.split("/")[-1].replace(".html", ""),
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "seller_name": "Largo France (Nantes)",
            "shipping_cost": 0.0,
            "warranty": "Garantie 24 mois reconditionné en France",
            "availability": availability,
            "scraped_at": scrape_started_at,
        }
        rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "product_id", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=5000.0,
            dedupe_keys_product_sale=("source_url", "product_id", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="product_sale",
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

    # Convenience files
    try:
        import shutil
        shutil.copy(jsonl_path, convenience_jsonl)
        shutil.copy(csv_path, convenience_csv)
        shutil.copy(json_path, convenience_json)
    except Exception:
        pass

    print(f"[{SOURCE_NAME.upper()}] ✅ Succès : {len(rows)} offres exportées dans {out_dir}", flush=True)

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
