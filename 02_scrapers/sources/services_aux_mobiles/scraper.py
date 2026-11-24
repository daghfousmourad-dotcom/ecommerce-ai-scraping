"""Services Aux Mobiles (SAM) Scraper (https://servicesauxmobiles.fr).

Atelier de réparation smartphone & tablette à Lille / Villeneuve-d'Ascq et Métropole Lilloise :
- Tarifs transparents par modèle : Écran (Soft OLED / LCD / Origine), Batterie, Connecteur, Vitre arrière.
- Couverture : Apple iPhone, Samsung Galaxy, Huawei, Xiaomi, Honor, Google Pixel, iPad, Tablettes.

Stratégie d'extraction (Niveau 1 — API WordPress REST Directe) :
    1. Interrogation de la page tarifaire dédiée via WP REST API : /wp-json/wp/v2/pages/93
    2. Parsing sémantique des listes de tarifs par section d'intervention (Écran, Batterie, Vitre, etc.)
    3. Normalisation standard 22 colonnes E-commerce AI V2.
    4. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
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

BASE_URL = "https://servicesauxmobiles.fr"
SOURCE_NAME = "services_aux_mobiles"
SOURCE_DOMAIN = "servicesauxmobiles.fr"
API_PAGE_URL = f"{BASE_URL}/wp-json/wp/v2/pages/93"
PAGE_PUBLIC_URL = f"{BASE_URL}/tarifs-reparation-telephone-lille-villeneuve/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Services Aux Mobiles (SAM) Lille",
    "address": "Atelier Mobile Lille & Métropole (Villeneuve-d'Ascq)",
    "city": "Lille",
    "postal_code": "59000",
    "delay": "30 minutes en atelier ou sur rendez-vous",
    "warranty": "Garantie 12 mois pièces et main-d'œuvre",
}


def parse_price(val_str: str) -> float | None:
    m = re.search(r"(\d+[\.,]?\d*)", val_str.replace(" ", "").replace("\xa0", ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def expand_model_tokens(raw_model: str, brand: str) -> list[str]:
    """Expands grouped models like 'iPhone 17 / 17 Plus' or '16 e / 17 e'."""
    text = raw_model.strip()
    text = re.sub(r"\(.*?\)", "", text).strip()
    
    # Split on '/' or ','
    if "/" in text:
        parts = [p.strip() for p in text.split("/") if p.strip()]
    elif "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
    else:
        parts = [text]

    results = []
    prefix = ""
    if brand == "Apple":
        prefix = "iPhone "
    elif brand == "Samsung":
        prefix = "Samsung Galaxy "
    elif brand == "Xiaomi":
        prefix = "Xiaomi "
    elif brand == "Honor":
        prefix = "Honor "
    elif brand == "Huawei":
        prefix = "Huawei "
    elif brand == "Google":
        prefix = "Google Pixel "

    for p in parts:
        p_clean = p.strip()
        if not p_clean:
            continue
        # Check if already contains prefix or brand
        if brand.lower() in p_clean.lower() or (prefix and prefix.strip().lower() in p_clean.lower()):
            results.append(p_clean)
        else:
            # If it's a numeric/short model like '17 Pro'
            if brand == "Apple" and not p_clean.lower().startswith("ipad"):
                results.append(f"iPhone {p_clean}")
            elif brand == "Samsung" and not p_clean.lower().startswith("tab"):
                results.append(f"Samsung Galaxy {p_clean}")
            elif brand == "Xiaomi":
                results.append(f"Xiaomi {p_clean}")
            elif brand == "Honor":
                results.append(f"Honor {p_clean}")
            elif brand == "Huawei":
                results.append(f"Huawei {p_clean}")
            elif brand == "Google":
                results.append(f"Google Pixel {p_clean}")
            else:
                results.append(p_clean)

    return results


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
        "Accept": "application/json",
    }

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    print(f"[{SOURCE_NAME}] Démarrage de l'extraction via WP REST API ({API_PAGE_URL})...")

    try:
        req = urllib.request.Request(API_PAGE_URL, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur d'accès à l'API: {e}")
        issues.append(fetch_issue(url=API_PAGE_URL, reason=REASON_FETCH_ERROR, message=str(e)))
        data = {}

    html_content = data.get("content", {}).get("rendered", "")
    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        uls = soup.find_all("ul")
        print(f"[{SOURCE_NAME}] Analyse sémantique de {len(uls)} listes tarifaires...")

        for ul in uls:
            prev = ul.find_previous_sibling()
            section_title = prev.get_text(" ", strip=True) if prev else "Divers"
            sec_lower = section_title.lower()

            # Identify section context
            brand = "Apple"
            family = "Smartphone"
            raw_repair = "Réparation"

            if "ipad" in sec_lower or "tablette" in sec_lower or "tab" in sec_lower:
                family = "Tablette"
            if "batterie" in sec_lower:
                raw_repair = "Remplacement Batterie"
            elif "ecran" in sec_lower or "oled" in sec_lower:
                raw_repair = "Remplacement Écran"
            elif "connecteur" in sec_lower:
                raw_repair = "Réparation Connecteur de charge"
            elif "vitre arriere" in sec_lower or "vitre arrière" in sec_lower:
                raw_repair = "Remplacement Vitre arrière"
            elif "châssis" in sec_lower or "chassis" in sec_lower:
                raw_repair = "Remplacement Châssis arrière"
            elif "microsoudure" in sec_lower or "carte mere" in sec_lower:
                raw_repair = "Réparation Carte Mère"

            if "samsung" in sec_lower or "galaxy" in sec_lower:
                brand = "Samsung"
            elif "huawei" in sec_lower:
                brand = "Huawei"
            elif "honor" in sec_lower:
                brand = "Honor"
            elif "xiaomi" in sec_lower:
                brand = "Xiaomi"
            elif "pixel" in sec_lower:
                brand = "Google"
            elif "iphone" in sec_lower or "ipad" in sec_lower or "apple" in sec_lower:
                brand = "Apple"

            for li in ul.find_all("li"):
                item_text = li.get_text(" ", strip=True)
                m = re.search(r"(\d+[\.,]?\d*)\s*€", item_text)
                if not m:
                    continue

                price = float(m.group(1).replace(",", "."))
                if price <= 0:
                    continue

                model_part = re.split(r"(\d+[\.,]?\d*)\s*€", item_text)[0].strip()
                if not model_part:
                    continue

                # Specific item repair type overrides
                item_lower = item_text.lower()
                current_repair = raw_repair
                if "batterie" in item_lower and "batterie" not in raw_repair.lower():
                    current_repair = "Remplacement Batterie"
                elif "connecteur" in item_lower:
                    current_repair = "Réparation Connecteur de charge"
                elif "devis" in item_lower:
                    continue

                norm_repair = normalize_repair_type(current_repair)

                expanded_models = expand_model_tokens(model_part, brand)
                for mod in expanded_models:
                    if not mod or len(mod) < 2:
                        continue

                    # Clean model name
                    mod_clean = re.sub(r"^(iPhone|Samsung Galaxy|Xiaomi|Honor|Huawei|Google Pixel)\s+", "", mod, flags=re.I).strip()
                    if brand == "Apple" and "ipad" not in mod.lower() and not mod_clean.lower().startswith("iphone"):
                        full_model = f"iPhone {mod_clean}"
                    elif brand == "Samsung" and "tab" not in mod.lower() and not mod_clean.lower().startswith("galaxy"):
                        full_model = f"Galaxy {mod_clean}"
                    else:
                        full_model = mod.strip()

                    row = {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": PAGE_PUBLIC_URL,
                        "source": PAGE_PUBLIC_URL,
                        "timestamp": scrape_started_at,
                        "data_type": "repair_price",
                        "device_family": family,
                        "brand": brand,
                        "model": full_model,
                        "item_name": f"{current_repair} {brand} {full_model}",
                        "repair_type_raw": current_repair,
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
                        "model_sku": full_model.lower().replace(" ", "_"),
                        "repair_category": "Réparation en atelier",
                        "scraped_at": scrape_started_at,
                    }
                    rows.append(row)

                    if max_models and len(rows) >= max_models:
                        break
            if max_models and len(rows) >= max_models:
                break

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

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
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Export des artefacts standard
    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Scraper {SOURCE_NAME}")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--max-models", type=int, default=None)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir, max_models=args.max_models)
