"""iPhoenix Scraper (https://www.iphoenix-reparation.com).

Atelier de réparation smartphones et tablettes à Avignon et dans le Vaucluse (Grand Avignon) :
- Labellisé QualiRépar officiel (Bonus réparation -25€ déduit immédiatement)
- Tarifs transparents par modèle (Écran OLED/LCD, Batterie, Connecteur)

Stratégie d'extraction (Niveau 1 — API WordPress REST Directe) :
    1. Interrogation de l'API WP REST officielle : /wp-json/wp/v2/pages?per_page=100
    2. Extraction des pages tarifs et modèles (classes structurées .iphx-card et tables .iphx-table)
    3. Capture des tarifs écran (plein tarif + avec QualiRépar) et tarifs batterie
    4. Normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
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

BASE_URL = "https://www.iphoenix-reparation.com"
SOURCE_NAME = "iphoenix"
SOURCE_DOMAIN = "iphoenix-reparation.com"
API_URL = f"{BASE_URL}/wp-json/wp/v2/pages"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "iPhoenix Avignon & Grand Avignon (Labellisé QualiRépar)",
    "address": "Atelier Avignon / Grand Avignon",
    "city": "Avignon",
    "postal_code": "84000",
    "delay": "30 à 45 minutes en atelier sans rendez-vous",
    "warranty": "Garantie pièces et main-d'œuvre, labellisé QualiRépar officiel",
}


def parse_price(val_str: str) -> float | None:
    m = re.search(r"(\d+[\.,]?\d*)", val_str.replace(" ", "").replace("\xa0", ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


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

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Récupération des pages via API WP REST : {API_URL}", flush=True)

    req = urllib.request.Request(f"{API_URL}?per_page=100", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            pages = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"❌ [{SOURCE_NAME.upper()}] Erreur fetch API WP: {exc}")
        fetch_errors.append(fetch_issue(API_URL, REASON_FETCH_ERROR, str(exc)))
        return []

    print(f"[{SOURCE_NAME.upper()}] 📦 {len(pages)} pages WP récupérées")

    rows: list[dict[str, Any]] = []
    models_seen: set[str] = set()

    for p in pages:
        content_html = p.get("content", {}).get("rendered", "")
        if not content_html or "€" not in content_html:
            continue

        page_link = p.get("link", BASE_URL)
        soup = BeautifulSoup(content_html, "html.parser")

        # 1. Extraction des cartes .iphx-card (Écran & Batterie)
        cards = soup.find_all(class_=lambda c: c and "iphx-card" in c)
        for card in cards:
            name_el = card.find(class_=lambda c: c and "iphx-card-name" in c)
            if not name_el:
                continue

            model_name = name_el.get_text(strip=True)
            if not model_name or "Modèle non listé" in model_name:
                continue

            brand = "Apple" if ("iphone" in model_name.lower() or any(k in model_name.lower() for k in ["pro", "mini", "plus"]) and "galaxy" not in model_name.lower()) else "Samsung"
            if not model_name.lower().startswith("iphone") and brand == "Apple":
                full_model = f"iPhone {model_name}"
            else:
                full_model = model_name

            if max_models and len(models_seen) >= max_models and full_model not in models_seen:
                continue
            models_seen.add(full_model)

            # Écran
            old_price_el = card.find(class_=lambda c: c and "iphx-card-old" in c)
            new_price_el = card.find(class_=lambda c: c and "iphx-card-new" in c)

            if old_price_el:
                price_ecran = parse_price(old_price_el.get_text(strip=True))
                if price_ecran and price_ecran > 0:
                    price_qr = parse_price(new_price_el.get_text(strip=True)) if new_price_el else (price_ecran - 25.0)
                    price_raw_str = f"{int(price_ecran) if price_ecran.is_integer() else price_ecran} €"
                    row = {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": page_link,
                        "source": page_link,
                        "timestamp": scrape_started_at,
                        "data_type": "repair_price",
                        "device_family": "Smartphone",
                        "brand": brand,
                        "model": full_model,
                        "item_name": f"Remplacement Écran {brand} {full_model}",
                        "repair_type_raw": "Remplacement Écran",
                        "repair_type": "Ecran LCD",
                        "price": price_ecran,
                        "price_raw": price_raw_str,
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

            # Batterie
            bat_el = card.find(class_=lambda c: c and "iphx-card-bat" in c)
            if bat_el:
                bat_text = bat_el.get_text(strip=True)
                prices_bat = re.findall(r"(\d+[\.,]?\d*)\s*€", bat_text)
                if prices_bat:
                    price_bat = parse_price(prices_bat[0])
                    if price_bat and price_bat > 0:
                        price_raw_str = f"{int(price_bat) if price_bat.is_integer() else price_bat} €"
                        row = {
                            "source_site": SOURCE_DOMAIN,
                            "source_url": page_link,
                            "source": page_link,
                            "timestamp": scrape_started_at,
                            "data_type": "repair_price",
                            "device_family": "Smartphone",
                            "brand": brand,
                            "model": full_model,
                            "item_name": f"Remplacement Batterie {brand} {full_model}",
                            "repair_type_raw": "Remplacement Batterie",
                            "repair_type": "Batterie",
                            "price": price_bat,
                            "price_raw": price_raw_str,
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

        # 2. Extraction des tables
        tables = soup.find_all("table")
        for table in tables:
            for tr in table.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
                if len(tds) < 2 or "Modèle" in tds[0]:
                    continue

                raw_model_group = tds[0]
                price_str = tds[-1]
                price_val = parse_price(price_str)
                if not price_val or price_val <= 0:
                    continue

                # Éclater les modèles groupés (ex: 'iPhone 8, 8 Plus' -> ['iPhone 8', 'iPhone 8 Plus'])
                parts = [p.strip() for p in raw_model_group.split(",")]
                base_name = parts[0]
                brand = "Apple" if "iphone" in base_name.lower() else "Samsung"

                for part in parts:
                    sub_model = part if part.lower().startswith("iphone") else f"iPhone {part}" if brand == "Apple" else part
                    if max_models and len(models_seen) >= max_models and sub_model not in models_seen:
                        continue
                    models_seen.add(sub_model)

                    price_raw_str = f"{int(price_val) if price_val.is_integer() else price_val} €"
                    row = {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": page_link,
                        "source": page_link,
                        "timestamp": scrape_started_at,
                        "data_type": "repair_price",
                        "device_family": "Smartphone",
                        "brand": brand,
                        "model": sub_model,
                        "item_name": f"Remplacement Batterie {brand} {sub_model}",
                        "repair_type_raw": "Remplacement Batterie",
                        "repair_type": "Batterie",
                        "price": price_val,
                        "price_raw": price_raw_str,
                        "currency": "EUR",
                        "currency_symbol": "€",
                        "shop_name": SHOP_INFO["shop_name"],
                        "address": SHOP_INFO["address"],
                        "city": SHOP_INFO["city"],
                        "postal_code": SHOP_INFO["postal_code"],
                        "delay": SHOP_INFO["delay"],
                        "warranty": SHOP_INFO["warranty"],
                        "model_sku": sub_model.lower().replace(" ", "_"),
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
    parser = argparse.ArgumentParser(description="iPhoenix Scraper (WP REST)")
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
