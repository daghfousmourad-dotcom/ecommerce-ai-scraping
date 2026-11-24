"""Pro Mobile 83 Scraper (https://promobile83.fr).

Atelier de réparation smartphone, tablette et multimédia à Toulon et dans le Var (83) :
- Secteurs : Toulon, La Seyne, Hyères, La Ciotat
- Tarifs détaillés par modèle avec pannes express (Écran, Batterie, Vitre, Caméra)

Stratégie d'extraction (Niveau 1 — API WordPress REST Directe) :
    1. Pagination de l'API WP REST officielle : /wp-json/wp/v2/pages?per_page=100
    2. Extraction des pages modèles smartphones (Apple iPhone, Samsung Galaxy, Xiaomi, Redmi, Poco)
    3. Parsing des tables HTML structurées contenues dans content.rendered (Panne, Délai, Prix)
    4. Normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.request
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
    from scraper.core.normalization import normalize_repair_type
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
    from core.normalization import normalize_repair_type
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

BASE_URL = "https://promobile83.fr"
SOURCE_NAME = "pro_mobile_83"
SOURCE_DOMAIN = "promobile83.fr"
API_URL = f"{BASE_URL}/wp-json/wp/v2/pages"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Pro Mobile 83 (Toulon & Var)",
    "address": "Atelier Toulon",
    "city": "Toulon",
    "postal_code": "83000",
    "delay": "30 à 60 minutes en atelier",
    "warranty": "Garantie pièces et main-d'œuvre Pro Mobile 83",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    "écran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "batterie": "Batterie",
    "vitre arrière": "Façade arrière",
    "arriere": "Façade arrière",
    "caméra": "Caméra arrière",
    "connecteur": "Connecteur de charge",
}


def parse_price(val_str: str) -> float | None:
    m = re.search(r"(\d+[\.,]?\d*)\s*€?", val_str.replace(" ", "").replace("\xa0", ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def clean_model_name(title: str) -> tuple[str, str] | None:
    # Nettoyer "Réparation Galaxy S24 Ultra à Toulon et dans le Var (83)" -> "Samsung", "Galaxy S24 Ultra"
    t = re.sub(r"à\s*Toulon.*$", "", title, flags=re.IGNORECASE).strip()
    t = re.sub(r"^Réparation\s+", "", t, flags=re.IGNORECASE).strip()

    # Ignorer automobile / calculateurs
    if any(k in t.lower() for k in ["calculateur", "ecu", "peugeot", "renault", "clio", "megane", "citroen", "audi", "vw", "ford", "cle/"]):
        return None

    if "iphone" in t.lower():
        brand = "Apple"
        model = t
    elif "galaxy" in t.lower():
        brand = "Samsung"
        model = t
    elif "xiaomi" in t.lower() or "redmi" in t.lower() or "poco" in t.lower():
        brand = "Xiaomi"
        model = t
    elif "huawei" in t.lower():
        brand = "Huawei"
        model = t
    elif "pixel" in t.lower():
        brand = "Google"
        model = t
    else:
        brand = "Smartphone"
        model = t

    return brand, model


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

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Pagination API WordPress REST : {API_URL}", flush=True)

    page = 1
    all_pages: list[dict[str, Any]] = []
    while True:
        url = f"{API_URL}?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
                all_pages.extend(batch)
                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                print(f"[{SOURCE_NAME.upper()}] 📄 Page WP {page}/{total_pages} reçue ({len(batch)} pages)")
                if page >= total_pages or len(batch) < 100:
                    break
                page += 1
        except Exception as exc:
            print(f"⚠️ [{SOURCE_NAME.upper()}] Fin ou erreur pagination page {page}: {exc}")
            break

    print(f"[{SOURCE_NAME.upper()}] 📦 Total pages WP récupérées : {len(all_pages)}")

    rows: list[dict[str, Any]] = []
    models_seen: set[str] = set()

    for p in all_pages:
        raw_title = p.get("title", {}).get("rendered", "")
        model_meta = clean_model_name(raw_title)
        if not model_meta:
            continue

        brand, model = model_meta
        if max_models and len(models_seen) >= max_models and model not in models_seen:
            continue
        models_seen.add(model)

        content_html = p.get("content", {}).get("rendered", "")
        if not content_html or "€" not in content_html:
            continue

        soup = BeautifulSoup(content_html, "html.parser")
        tables = soup.find_all("table")

        page_link = p.get("link", BASE_URL)
        slug = p.get("slug", "")

        for table in tables:
            trs = table.find_all("tr")
            for tr in trs:
                tds = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
                if len(tds) < 2 or "Panne" in tds[0]:
                    continue

                panne_raw = tds[0].replace("📱", "").replace("🔋", "").replace("🪟", "").replace("📷", "").strip()
                delay_raw = tds[1].replace("🕐", "").strip() if len(tds) >= 3 else SHOP_INFO["delay"]
                price_str = tds[-1]

                price = parse_price(price_str)
                if price is None or price <= 0:
                    continue

                panne_lower = panne_raw.lower()
                canon_rep = "Autre réparation"
                for k, v in CANONICAL_REPAIR_MAP.items():
                    if k in panne_lower:
                        canon_rep = v
                        break

                price_raw_str = f"{int(price) if price.is_integer() else price} €"
                item_name = f"{panne_raw} {brand} {model}".strip()

                row = {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": page_link,
                    "source": page_link,
                    "timestamp": scrape_started_at,
                    "data_type": "repair_price",
                    "device_family": "Smartphone",
                    "brand": brand,
                    "model": model,
                    "item_name": item_name,
                    "repair_type_raw": panne_raw,
                    "repair_type": canon_rep,
                    "price": price,
                    "price_raw": price_raw_str,
                    "currency": "EUR",
                    "currency_symbol": "€",
                    "shop_name": SHOP_INFO["shop_name"],
                    "address": SHOP_INFO["address"],
                    "city": SHOP_INFO["city"],
                    "postal_code": SHOP_INFO["postal_code"],
                    "delay": f"Réparation en {delay_raw}" if delay_raw else SHOP_INFO["delay"],
                    "warranty": SHOP_INFO["warranty"],
                    "model_sku": slug,
                    "repair_category": "Réparation en atelier",
                    "scraped_at": scrape_started_at,
                }
                rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données extraites : {len(rows)} tarifs sur {len(models_seen)} modèles")

    # Déduplication
    deduped_rows = dedupe_dict_rows(rows, keys=["model", "repair_type_raw", "price", "shop_name"])

    # Nettoyage
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        deduped_rows, clean_report = clean_dataset(
            deduped_rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in deduped_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Export des 5 artefacts
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(deduped_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return deduped_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Pro Mobile 83 Scraper (WP REST)")
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
