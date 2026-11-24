"""Microtech86 Scraper (https://microtech86.fr/tarifs).

Atelier de micro-soudure et réparation informatique, smartphones, tablettes et consoles (Poitiers & national par envoi) :
- Tarifs par intervention et par modèle : Écran Relife/OLED, Batterie, Connecteur de charge, Carte mère, Face ID, FPC, SWAP IC.
- Couverture : Apple iPhone (X à 17 Pro Max), tablettes, PC portables, consoles (PS5, Xbox, Switch), Android.

Stratégie d'extraction (Niveau 2 — Crawl BFS des paramètres d'URL & Parsing HTML Concurrent) :
    1. Exploration BFS des URLs tarifaires : /tarifs?category=...&brand=...&model=...
    2. Extraction concurrente des pages avec ThreadPoolExecutor
    3. Parsing des composants li.pricing-row (.pricing-row__name & .pricing-row__price)
    4. Normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
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

BASE_URL = "https://microtech86.fr"
SOURCE_NAME = "microtech86"
SOURCE_DOMAIN = "microtech86.fr"
START_URL = f"{BASE_URL}/tarifs"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Microtech86 (Atelier Poitiers & Envois nationaux)",
    "address": "15 Rue de la Croix Blanche",
    "city": "Poitiers",
    "postal_code": "86000",
    "delay": "Réparation express en atelier ou retour postal sécurisé",
    "warranty": "Garantie pièces et main-d'œuvre",
}


def parse_price(val_str: str) -> float | None:
    cleaned = val_str.replace(" ", "").replace("\xa0", "").replace("&nbsp;", "")
    m = re.search(r"(\d+[\.,]?\d*)", cleaned)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def fetch_url(url: str, ctx: ssl.SSLContext, headers: dict[str, str]) -> tuple[str, str | None, str | None]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            return url, resp.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return url, None, str(e)


def extract_metadata_from_url(url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)

    cat = qs.get("category", ["telephone"])[0]
    brand_param = qs.get("brand", [""])[0]
    model_param = qs.get("model", [""])[0]
    serie_param = qs.get("serie", [""])[0]

    family = "smartphone"
    if cat == "pc":
        family = "computer"
    elif cat == "tablette":
        family = "tablet"
    elif cat == "console":
        family = "console"

    brand = "Apple"
    if brand_param == "autres-marques":
        brand = "Android / Autres"
    elif cat == "console":
        brand = "Consoles (Sony/Nintendo/Microsoft)"
    elif cat == "pc":
        brand = "PC / Mac"
    elif cat == "tablette" and not brand_param:
        brand = "Tablettes (iPad / Android)"

    model_name = "Modèle standard"
    target = model_param or serie_param
    if target:
        # Format e.g. iphone-16-pro-max -> iPhone 16 Pro Max
        clean_target = target.replace("-", " ")
        clean_target = re.sub(r"\biphone\b", "iPhone", clean_target, flags=re.IGNORECASE)
        clean_target = clean_target.title()
        clean_target = clean_target.replace("Iphone", "iPhone")
        model_name = clean_target
    elif cat == "console":
        model_name = "Consoles de jeux"
    elif cat == "pc":
        model_name = "Ordinateurs portables et fixes"
    elif cat == "tablette":
        model_name = "Tablettes tactiles"
    elif brand_param == "autres-marques":
        model_name = "Smartphones Android"

    return brand, model_name, family


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else _SCRAPERS_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"User-Agent": USER_AGENT}

    print(f"[{SOURCE_NAME.upper()}] Étape 1 : Exploration BFS des paramètres d'URL à partir de {START_URL}...", flush=True)

    queue = [START_URL]
    visited: set[str] = set()
    pages_html: dict[str, str] = {}
    issues: list[dict[str, Any]] = []

    while queue:
        curr = queue.pop(0)
        if curr in visited:
            continue
        visited.add(curr)

        _, html, error = fetch_url(curr, ctx, headers)
        if error:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=curr, reason=REASON_FETCH_ERROR, message=error))
            continue
        if not html:
            continue

        pages_html[curr] = html
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href.startswith("/tarifs") or "microtech86.fr/tarifs" in href:
                full_u = urllib.parse.urljoin(BASE_URL, href)
                if full_u not in visited and full_u not in queue:
                    queue.append(full_u)

        if max_models and len(pages_html) >= max_models:
            print(f"[{SOURCE_NAME.upper()}] Limitation à {max_models} pages atteinte", flush=True)
            break

    print(f"[{SOURCE_NAME.upper()}] {len(pages_html)} pages explorées. Étape 2 : Parsing des li.pricing-row...", flush=True)

    rows: list[dict[str, Any]] = []
    for url, html in pages_html.items():
        brand, model_name, family = extract_metadata_from_url(url)
        soup = BeautifulSoup(html, "html.parser")

        pricing_rows = soup.select("li.pricing-row")
        for p_row in pricing_rows:
            nom_el = p_row.select_one(".pricing-row__name")
            prix_el = p_row.select_one(".pricing-row__price")
            if not nom_el or not prix_el:
                continue

            raw_label = nom_el.get_text(strip=True)
            price = parse_price(prix_el.get_text(strip=True))

            if price is None or price <= 0:
                continue

            norm_repair = normalize_repair_type(raw_label)

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": url,
                "source": url,
                "timestamp": scrape_started_at,
                "data_type": "repair_price",
                "device_family": family,
                "brand": brand,
                "model": model_name,
                "item_name": f"{raw_label} {brand} {model_name}",
                "repair_type_raw": raw_label,
                "repair_type": norm_repair,
                "price": price,
                "price_raw": f"{int(price) if price.is_integer() else price} €",
                "currency": "EUR",
                "currency_symbol": "€",
                "shop_name": SHOP_INFO["shop_name"],
                "address": SHOP_INFO["address"],
                "city": SHOP_INFO["city"],
                "postal_code": SHOP_INFO["postal_code"],
                "delay": SHOP_INFO["delay"],
                "warranty": SHOP_INFO["warranty"],
                "model_sku": model_name.lower().replace(" ", "_"),
                "repair_category": "Réparation en atelier / à distance",
                "scraped_at": scrape_started_at,
            }
            rows.append(row)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type", "price"))
    print(f"[{SOURCE_NAME.upper()}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}", flush=True)

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        print(f"[{SOURCE_NAME.upper()}] Après nettoyage qualité: {len(rows)} tarifs valides", flush=True)

    # Export des 5 artefacts standard
    jsonl_path = out_dir / f"{SOURCE_NAME}.jsonl"
    csv_path = out_dir / f"{SOURCE_NAME}.csv"
    json_path = out_dir / f"{SOURCE_NAME}.json"

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source": SOURCE_NAME,
        "scraped_at": scrape_started_at,
        "count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(rows)} tarifs exportés dans {out_dir}", flush=True)

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
