"""NomoPhone Scraper (https://nomophone.fr).

Plateforme française de smartphones reconditionnés garantis 24 mois :
- Univers : product_sales (offres e-commerce smartphones reconditionnés)
- Catalogue officiel : https://nomophone.fr/sitemap/fr-products.xml
- Modèles : Apple iPhone, Samsung Galaxy, Huawei, Google Pixel, etc.
- Données structurées : Schéma Schema.org Product (LD+JSON) officiel intégré dans chaque page.

Stratégie d'extraction (Niveau 1 & 2 — Sitemap XML + JSON-LD Microdata) :
    1. Récupération des URLs de produits via /sitemap/fr-products.xml
    2. Extraction concurrente des pages avec ThreadPoolExecutor
    3. Parsing des blocs de données structurées LD+JSON (@type: Product)
    4. Normalisation standard E-commerce AI V2 (product_sales)
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
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

BASE_URL = "https://nomophone.fr"
SOURCE_NAME = "nomophone"
SOURCE_DOMAIN = "nomophone.fr"
SITEMAP_PRODUCTS_URL = f"{BASE_URL}/sitemap/fr-products.xml"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STORAGE_RE = re.compile(r"(\d+)\s*(?:Go|GB|To|TB)", re.I)


def fetch_url(url: str, ctx: ssl.SSLContext, headers: dict[str, str]) -> tuple[str, str | None, str | None]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            return url, resp.read().decode("utf-8"), None
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

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    print(f"[{SOURCE_NAME}] Téléchargement du sitemap produits ({SITEMAP_PRODUCTS_URL})...")
    product_urls: list[str] = []
    try:
        req = urllib.request.Request(SITEMAP_PRODUCTS_URL, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            root = ET.fromstring(resp.read())
            for elem in root.iter():
                if elem.tag.endswith("loc") and elem.text and "/fr/produits/" in elem.text:
                    product_urls.append(elem.text.strip())
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur sitemap: {e}")
        issues.append(fetch_issue(url=SITEMAP_PRODUCTS_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    print(f"[{SOURCE_NAME}] Trouvé {len(product_urls)} fiches produits dans le sitemap.")

    if max_models:
        product_urls = product_urls[:max_models]

    print(f"[{SOURCE_NAME}] Scraping concurrent de {len(product_urls)} fiches produits...")
    pages_html: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_url, url, ctx, headers): url for url in product_urls}
        for future in as_completed(futures):
            url, html, error = future.result()
            if error:
                issues.append(fetch_issue(url=url, reason=REASON_FETCH_ERROR, message=error))
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

        raw_title = product_schema.get("name", "")
        raw_brand = product_schema.get("brand")
        brand = raw_brand.get("name", "") if isinstance(raw_brand, dict) else str(raw_brand or "")
        model = product_schema.get("model", "")
        sku = product_schema.get("sku", "")

        offers = product_schema.get("offers", {})
        price_val: float | None = None
        if isinstance(offers, dict):
            p = offers.get("price")
            if p:
                try:
                    price_val = float(str(p).replace(",", "."))
                except ValueError:
                    price_val = None

        if not price_val or price_val <= 0:
            continue

        # Extraction stockage
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

        # Nettoyage marque/modèle
        if not brand:
            if "iphone" in raw_title.lower():
                brand = "Apple"
            elif "samsung" in raw_title.lower() or "galaxy" in raw_title.lower():
                brand = "Samsung"
            elif "huawei" in raw_title.lower():
                brand = "Huawei"
            elif "pixel" in raw_title.lower():
                brand = "Google"

        if not model:
            # Nettoyer depuis le titre
            m_cand = re.sub(r"^(?:Apple|Samsung|Huawei|Google)\s+", "", raw_title, flags=re.I)
            m_cand = re.split(r"(\d+\s*(?:Go|GB|To|TB))", m_cand)[0].strip()
            model = m_cand

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": url,
            "source": url,
            "timestamp": scrape_started_at,
            "scrape_started_at": scrape_started_at,
            "data_type": "product_sale",
            "device_type": "smartphone",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model,
            "model_normalized": f"{brand} {model}".strip(),
            "item_title": raw_title,
            "storage_gb": storage_gb,
            "storage_raw": storage_raw,
            "condition": "reconditionne",
            "product_id": sku or url.split("/")[-1],
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "seller_name": "NomoPhone France",
            "shipping_cost": 0.0,
            "warranty": "Garantie 24 mois reconditionné certifié",
            "scraped_at": scrape_started_at,
        }
        rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "product_id", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
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

    # Export des 5 artefacts standard
    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(rows)} offres exportées dans {out_dir}", flush=True)

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
