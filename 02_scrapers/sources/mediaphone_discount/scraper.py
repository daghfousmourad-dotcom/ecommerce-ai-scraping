"""Mediaphone Discount Scraper (https://www.mediaphone-discount.fr).

Atelier de réparation smartphones, tablettes, montres connectées, consoles et PC (Bourg-la-Reine & national) :
- Tarifs par intervention et par modèle : Écran (Original / ReLife / Premium), Batterie, Connecteur, Caméra, Vitre arrière.
- Couverture : Apple iPhone, Samsung Galaxy, Xiaomi, Huawei, Apple Watch, Galaxy Watch, iPad, Galaxy Tab, Nintendo Switch, PS4/PS5, PC/Mac.

Stratégie d'extraction (Niveau 2 — Parsing Pages Configurateurs & Attributs Data) :
    1. Téléchargement des pages configurateurs :
        - /reparation-smartphone
        - /reparation-tablette
        - /reparation-montre-connectee
        - /reparation-console
        - /reparation-ordinateurs
    2. Extraction directe des cartes .rt3-rep (attributs data-marque, data-modele, data-nom et .rt3-prix)
    3. Parsing des forfaits consoles (.card)
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

BASE_URL = "https://www.mediaphone-discount.fr"
SOURCE_NAME = "mediaphone_discount"
SOURCE_DOMAIN = "mediaphone-discount.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Mediaphone Discount (Bourg-la-Reine & National)",
    "address": "153 Avenue du Général Leclerc",
    "city": "Bourg-la-Reine",
    "postal_code": "92340",
    "delay": "30 minutes en atelier ou retour postal sécurisé",
    "warranty": "Garantie pièces et main-d'œuvre jusqu'à 1 an",
}

SECTIONS = [
    {"url": f"{BASE_URL}/reparation-smartphone", "family": "smartphone"},
    {"url": f"{BASE_URL}/reparation-tablette", "family": "tablet"},
    {"url": f"{BASE_URL}/reparation-montre-connectee", "family": "smartwatch"},
    {"url": f"{BASE_URL}/reparation-console", "family": "console"},
    {"url": f"{BASE_URL}/reparation-ordinateurs", "family": "computer"},
]


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
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            return url, resp.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return url, None, str(e)


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

    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    print(f"[{SOURCE_NAME.upper()}] Étape 1 : Téléchargement des {len(SECTIONS)} rubriques...", flush=True)

    for sec in SECTIONS:
        url = sec["url"]
        family = sec["family"]
        print(f"[{SOURCE_NAME.upper()}] Extraction rubrique : {url}...", flush=True)
        _, html, error = fetch_url(url, ctx, headers)

        if error:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=error))
            continue
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")

        # 1. Parsing .rt3-rep (Smartphones, Tablettes, Montres connectées)
        cards = soup.select(".rt3-rep")
        for card in cards:
            brand = card.get("data-marque", "").strip() or "Générique"
            model_name = card.get("data-modele", "").strip() or "Modèle inconnu"
            raw_label = card.get("data-nom", "").strip()

            price_el = card.select_one(".rt3-prix")
            if not price_el or not raw_label:
                continue

            price = parse_price(price_el.get_text(strip=True))
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

        # 2. Parsing forfaits spécifiques Consoles & PC
        if family == "console":
            for card in soup.select(".card"):
                txt = card.get_text(" ", strip=True)
                m = re.search(r"(\d+[\.,]?\d*)\s*€", txt)
                if m and any(w in txt.lower() for w in ["switch", "ps4", "ps5", "xbox", "joy-con"]):
                    price = float(m.group(1).replace(",", "."))
                    if price > 0:
                        raw_label = txt.split(f"{m.group(1)}€")[0].strip()
                        # Extract console model
                        console_model = "Nintendo Switch"
                        brand = "Nintendo"
                        if "switch lite" in raw_label.lower():
                            console_model = "Nintendo Switch Lite"
                        elif "switch oled" in raw_label.lower():
                            console_model = "Nintendo Switch OLED"
                        elif "ps5" in raw_label.lower():
                            console_model = "Playstation 5"
                            brand = "Sony"
                        elif "ps4" in raw_label.lower():
                            console_model = "Playstation 4"
                            brand = "Sony"
                        elif "xbox" in raw_label.lower():
                            console_model = "Xbox Series"
                            brand = "Microsoft"

                        norm_repair = normalize_repair_type(raw_label)
                        row = {
                            "source_site": SOURCE_DOMAIN,
                            "source_url": url,
                            "source": url,
                            "timestamp": scrape_started_at,
                            "data_type": "repair_price",
                            "device_family": "console",
                            "brand": brand,
                            "model": console_model,
                            "item_name": f"{raw_label} {console_model}",
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
                            "model_sku": console_model.lower().replace(" ", "_"),
                            "repair_category": "Réparation en atelier / à distance",
                            "scraped_at": scrape_started_at,
                        }
                        rows.append(row)

        elif family == "computer":
            for tr in soup.find_all(["tr", "div", "p"]):
                txt = tr.get_text(" ", strip=True)
                if "diagnostic" in txt.lower() and "30€" in txt:
                    row = {
                        "source_site": SOURCE_DOMAIN,
                        "source_url": url,
                        "source": url,
                        "timestamp": scrape_started_at,
                        "data_type": "repair_price",
                        "device_family": "computer",
                        "brand": "Générique PC / Mac",
                        "model": "Ordinateur Portable & Fixe",
                        "item_name": "Diagnostic panne ordinateur PC / Mac",
                        "repair_type_raw": "Diagnostic panne ordinateur",
                        "repair_type": "diagnostic",
                        "price": 30.0,
                        "price_raw": "30 €",
                        "currency": "EUR",
                        "currency_symbol": "€",
                        "shop_name": SHOP_INFO["shop_name"],
                        "address": SHOP_INFO["address"],
                        "city": SHOP_INFO["city"],
                        "postal_code": SHOP_INFO["postal_code"],
                        "delay": SHOP_INFO["delay"],
                        "warranty": SHOP_INFO["warranty"],
                        "model_sku": "diagnostic_pc_mac",
                        "repair_category": "Réparation en atelier / à distance",
                        "scraped_at": scrape_started_at,
                    }
                    rows.append(row)
                    break

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
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
