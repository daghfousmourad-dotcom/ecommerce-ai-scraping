"""Koko Store Scraper (https://koko-store.fr).

Atelier de réparation smartphones, tablettes, ordinateurs, montres connectées et consoles à Villeneuve-d'Ascq :
- Tarifs par intervention et par modèle : Écran (Compatible, Soft OLED, Original, Reconditionné), Batterie, Connecteur de charge, Vitre arrière.
- Couverture : Apple, Samsung, Xiaomi, Huawei, Google Pixel, Honor, Oppo, OnePlus, Sony, Nintendo Switch, PS4/PS5, PC/Mac.

Stratégie d'extraction (Niveau 1 — Appel API REST Interne /wp-json/kokorepair/v1/navigate) :
    1. Récupération du nonce et du page_id sur /reparation/
    2. Découverte de l'arborescence des catégories, marques et modèles via l'API de navigation
    3. Requêtes directes de 'jump' sur chaque modèle (15 workers concurrents)
    4. Parsing des items [data-kr-item] (data-kr-price, data-kr-label, data-kr-quote)
    5. Normalisation standard 22 colonnes E-commerce AI V2.
    6. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import ssl
import sys
import time
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

BASE_URL = "https://koko-store.fr"
SOURCE_NAME = "koko_store"
SOURCE_DOMAIN = "koko-store.fr"
REPARATION_URL = f"{BASE_URL}/reparation/"
NAVIGATE_API = f"{BASE_URL}/wp-json/kokorepair/v1/navigate"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Koko Store (Villeneuve-d'Ascq)",
    "address": "147 Boulevard de Valmy",
    "city": "Villeneuve-d'Ascq",
    "postal_code": "59650",
    "delay": "Réparation express sans rendez-vous en atelier",
    "warranty": "Garantie pièces et main-d'œuvre",
}

CATEGORIES = [
    (1, "Smartphone", "smartphone"),
    (2, "Tablette", "tablet"),
    (3, "Ordinateur portable", "computer"),
    (4, "Montre connectée", "smartwatch"),
    (5, "Console de jeu", "console"),
    (6, "Ordinateur bureau", "computer"),
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


def fetch_jump(
    model: dict[str, Any],
    page_id: str,
    nonce: str,
    ctx: ssl.SSLContext,
    headers: dict[str, str],
    retries: int = 2,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    payload = {
        "action": "pick",
        "what": "jump",
        "id": model["id"],
        "page_id": page_id,
        "nonce": nonce,
    }
    req_data = json.dumps(payload).encode("utf-8")
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                NAVIGATE_API,
                data=req_data,
                headers={**headers, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, context=ctx, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return model, data, None
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(1.0 + attempt * 1.0)
    return model, None, last_err


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

    print(f"[{SOURCE_NAME.upper()}] Étape 1 : Récupération du nonce et session sur {REPARATION_URL}...", flush=True)

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj),
    )

    req0 = urllib.request.Request(REPARATION_URL, headers=headers)
    try:
        with opener.open(req0, timeout=12) as r:
            soup0 = BeautifulSoup(r.read().decode("utf-8"), "html.parser")
            page_id = soup0.select_one("[data-kr-page]")["data-kr-page"]
            nonce = soup0.select_one('input[name="_wpnonce"]')["value"]
    except Exception as e:
        print(f"[{SOURCE_NAME.upper()}] Erreur initialisation: {e}", flush=True)
        return []

    print(f"[{SOURCE_NAME.upper()}] Étape 2 : Exploration de l'arborescence des catégories, marques et modèles...", flush=True)

    all_models: list[dict[str, Any]] = []

    for cat_id, cat_name, family in CATEGORIES:
        p_cat = {"action": "pick", "what": "category", "id": cat_id, "page_id": page_id, "nonce": nonce}
        req_cat = urllib.request.Request(
            NAVIGATE_API,
            data=json.dumps(p_cat).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
        )
        try:
            with opener.open(req_cat, timeout=12) as r:
                d_cat = json.loads(r.read().decode("utf-8"))
                soup_b = BeautifulSoup(d_cat.get("html", ""), "html.parser")
                brand_links = soup_b.select("a.kr-choice")
                brands: list[tuple[int, str]] = []
                for bl in brand_links:
                    m = re.search(r"kr_id=(\d+)", bl.get("href", ""))
                    if m:
                        brands.append((int(m.group(1)), bl.get_text(strip=True)))

                print(f"[{SOURCE_NAME.upper()}] {cat_name} : {len(brands)} marques", flush=True)

                for b_id, b_name in brands:
                    p_b = {"action": "pick", "what": "brand", "id": b_id, "page_id": page_id, "nonce": nonce}
                    req_b = urllib.request.Request(
                        NAVIGATE_API,
                        data=json.dumps(p_b).encode("utf-8"),
                        headers={**headers, "Content-Type": "application/json"},
                    )
                    try:
                        with opener.open(req_b, timeout=12) as r_b:
                            d_b = json.loads(r_b.read().decode("utf-8"))
                            soup_m = BeautifulSoup(d_b.get("html", ""), "html.parser")
                            model_links = soup_m.select("a.kr-choice")
                            for ml in model_links:
                                m_id = re.search(r"kr_id=(\d+)", ml.get("href", ""))
                                if m_id:
                                    m_name = ml.get_text(strip=True)
                                    all_models.append({
                                        "id": int(m_id.group(1)),
                                        "name": m_name,
                                        "brand": b_name,
                                        "family": family,
                                        "category": cat_name,
                                    })
                    except Exception:
                        continue
        except Exception:
            continue

    print(f"[{SOURCE_NAME.upper()}] {len(all_models)} modèles découverts au total", flush=True)

    if max_models:
        all_models = all_models[:max_models]
        print(f"[{SOURCE_NAME.upper()}] Limitation à {len(all_models)} modèles pour test", flush=True)

    print(f"[{SOURCE_NAME.upper()}] Étape 3 : Requêtes directes de jump et extraction des tarifs (8 workers)...", flush=True)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    total_models = len(all_models)
    done_count = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_jump, m, page_id, nonce, ctx, headers): m for m in all_models}
        for future in as_completed(futures):
            done_count += 1
            if done_count % 100 == 0 or done_count == total_models:
                print(f"[{SOURCE_NAME.upper()}] Progression: {done_count}/{total_models} modèles traités ({len(rows)} tarifs extraits)...", flush=True)

            model_info, resp_data, error = future.result()
            model_url = f"{REPARATION_URL}?kr_action=pick&kr_what=jump&kr_id={model_info['id']}"

            if error:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=model_url, reason=REASON_FETCH_ERROR, message=error))
                continue

            if not resp_data or resp_data.get("step") != "repairs":
                continue

            soup = BeautifulSoup(resp_data.get("html", ""), "html.parser")
            items = soup.select("[data-kr-item]")

            brand = model_info["brand"]
            model_name = model_info["name"]
            # Nettoyer l'année ou suffixes dans model_name (ex: 'iPhone 17 Pro2025' -> 'iPhone 17 Pro')
            model_name = re.sub(r"\d{4}$", "", model_name).strip()
            family = model_info["family"]

            for it in items:
                quote_flag = it.get("data-kr-quote", "0")
                if quote_flag == "1":
                    continue  # Sur devis

                price_str = it.get("data-kr-price", "")
                price = parse_price(price_str)
                if price is None or price <= 0:
                    continue

                raw_label_attr = it.get("data-kr-label", "")
                # Format: "Remplacement de l'écran|Soft OLED" -> "Remplacement de l'écran (Soft OLED)"
                if "|" in raw_label_attr:
                    parts = raw_label_attr.split("|")
                    raw_label = f"{parts[0].strip()} ({parts[1].strip()})"
                else:
                    raw_label = raw_label_attr.strip()

                if not raw_label:
                    continue

                norm_repair = normalize_repair_type(raw_label)

                row = {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": model_url,
                    "source": model_url,
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
                    "repair_category": "Réparation en atelier",
                    "scraped_at": scrape_started_at,
                }
                rows.append(row)

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
