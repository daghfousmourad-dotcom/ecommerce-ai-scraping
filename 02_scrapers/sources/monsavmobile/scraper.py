"""MonSAVMobile Scraper (https://monsavmobile.fr).

SAV agréé constructeur - Plateforme SBE Global Care
Site: https://monsavmobile.fr/#!/fr/price
API: https://monsavmobile.fr/srv-sbe-js (JSON, AngularJS XSRF protection)
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
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

BASE_URL = "https://monsavmobile.fr/srv-sbe-js"
SOURCE_NAME = "monsavmobile"
SOURCE_DOMAIN = "monsavmobile.fr"
PROVIDER_NAME = "Mon SAV Mobile (SBE Agréé Constructeur)"
DELAY = 0.4
MAX_RETRIES = 3

OPTION_TYPE_TO_CANON = {
    "DISPLAY": "Ecran LCD",
    "DISPLAYLOGICBOARD": "Ecran LCD",
    "BATTERY": "Batterie",
    "CAMERA": "Appareil photo arrière",
    "CAMERA_REAR": "Appareil photo arrière",
    "CAMERA_FRONT": "Appareil photo avant",
    "CONNECTEUR": "Connecteur de charge",
    "AUDIO": "Haut-parleur",
    "VIBRATOR": "Vibreur",
    "OXIDATION": "Désoxydation",
    "COSMETIQUE": "Façade arrière",
    "LOGICIEL_SOFTWARE": "Diagnostic / Logiciel",
    "SOFTWARE": "Diagnostic / Logiciel",
}

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://monsavmobile.fr/",
    "Origin": "https://monsavmobile.fr",
}


def parse_angular_json(txt: str) -> dict:
    """Retire le préfixe AngularJS XSRF )]}',\\n avant de parser le JSON."""
    if txt.startswith(")]}',\n"):
        txt = txt[5:].strip()
    elif txt.startswith(")]}',"):
        txt = txt[4:].strip()
    return json.loads(txt)


def api_call(endpoint: str, payload: dict | None = None, retries: int = MAX_RETRIES) -> dict:
    """Appel API avec retry et délai."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            resp = opener.open(req, timeout=30)
            return parse_angular_json(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)
    return {}


def init_session() -> bool:
    """Initialise la session SBE (XSRF-TOKEN + cookies)."""
    req1 = urllib.request.Request(f"{BASE_URL}/session/init", headers=headers)
    opener.open(req1)

    xsrf_token = None
    for c in cj:
        if c.name == "XSRF-TOKEN":
            xsrf_token = c.value
            break

    if not xsrf_token:
        return False

    headers["X-XSRF-Token"] = xsrf_token
    res = api_call("session/initSession", {"reinit": False})
    return res.get("code", 0) == 200


def get_all_manufacturers() -> list[dict[str, Any]]:
    res = api_call("reference/listManufacturers", {})
    return res.get("manufacturers", [])


def get_models_for_manufacturer(manufacturer_id: int) -> list[dict[str, Any]]:
    res = api_call("reference/listModels", {"manufacturerId": manufacturer_id})
    return res.get("models", [])


def get_repair_prices(catalog_model_id: int) -> list[dict[str, Any]]:
    try:
        res = api_call(
            "quotation/findLogicticQuoteConfig",
            {
                "channelCode": "WEB",
                "quoteType": "PREACCEPTED_QUOTE",
                "catalogModelId": int(catalog_model_id),
            },
        )
        return res.get("options", [])
    except Exception:
        return []


def normalize_brand(raw: str) -> str:
    mapping = {
        "APPLE": "Apple",
        "SAMSUNG": "Samsung",
        "HUAWEI": "Huawei",
        "XIAOMI": "Xiaomi",
        "ONEPLUS": "OnePlus",
        "OPPO": "Oppo",
        "NOKIA": "Nokia",
        "HMD_GLOBAL": "Nokia",
        "ALCATEL": "Alcatel",
        "ESSENTIEL_B": "Essentiel B",
        "BEATS": "Apple",
        "VIVO": "Vivo",
        "REALME": "Realme",
        "BLACKVIEW": "Blackview",
        "CUBOT": "Cubot",
        "DOOGEE": "Doogee",
        "OUKITEL": "Oukitel",
        "KONROW": "Konrow",
        "MYPHONE": "MyPhone",
        "NEOW": "NEOW",
        "OSCAL": "Oscal",
        "TECNO": "Tecno",
    }
    return mapping.get(raw.upper(), raw.title())


def normalize_model(brand: str, raw_model: str) -> str:
    m = raw_model.strip()
    for prefix in [brand.upper(), brand.lower(), brand]:
        if m.upper().startswith(prefix.upper()):
            m = m[len(prefix) :].strip()
    return m.strip()


def option_to_canon_repair(option_type: str, option_desc: str) -> str:
    base = OPTION_TYPE_TO_CANON.get(option_type.upper())
    if base:
        return base
    canon, matched = normalize_repair_type(option_desc or option_type)
    return canon if matched and canon else (option_desc or option_type)


def scrape(
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Point d'entrée standard E-commerce AI Scraping pour monsavmobile."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"{SOURCE_NAME}_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    db_json_path = out_dir / f"db_{SOURCE_NAME}.json"
    db_jsonl_path = out_dir / f"db_{SOURCE_NAME}.jsonl"

    print(f"[{SOURCE_NAME}] Initialisation session sur {SOURCE_DOMAIN}...", flush=True)
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    if not init_session():
        fetch_errors += 1
        issues.append(fetch_issue(reason=REASON_FETCH_ERROR, source_site=SOURCE_DOMAIN, url=f"{BASE_URL}/session/init", message="Échec initSession"))
        print(f"[{SOURCE_NAME}] ❌ Impossible d'initialiser la session API.", flush=True)
        return []

    try:
        manufacturers = get_all_manufacturers()
    except Exception as e:
        fetch_errors += 1
        issues.append(fetch_issue(reason=REASON_FETCH_ERROR, source_site=SOURCE_DOMAIN, url=f"{BASE_URL}/reference/listManufacturers", message=str(e)))
        return []

    print(f"[{SOURCE_NAME}] {len(manufacturers)} marques identifiées.", flush=True)

    all_rows: list[dict[str, Any]] = []
    processed_models = 0

    for mfr in manufacturers:
        mid = mfr.get("manufacturer_id")
        mcode = mfr.get("manufacturer_code", "")
        mname = mfr.get("manufacturer_name", "")
        brand_norm = normalize_brand(mcode)

        try:
            models = get_models_for_manufacturer(mid)
        except Exception as e:
            fetch_errors += 1
            issues.append(fetch_issue(reason=REASON_FETCH_ERROR, source_site=SOURCE_DOMAIN, url=f"{BASE_URL}/reference/listModels", message=f"Mfr {mid}: {e}"))
            continue

        print(f"[{SOURCE_NAME}] Marque {brand_norm} : {len(models)} modèles à traiter...", flush=True)

        for model in models:
            if max_models is not None and processed_models >= max_models:
                break

            model_id = model.get("model_id")
            model_name_raw = model.get("model_name", "")
            model_name = normalize_model(brand_norm, model_name_raw)
            processed_models += 1

            if processed_models % 50 == 0:
                print(f"[{SOURCE_NAME}] Progression : {processed_models} modèles explorés ({len(all_rows)} tarifs collectés)...", flush=True)

            options = get_repair_prices(model_id)
            time.sleep(DELAY)

            if not options:
                continue

            for opt in options:
                option_type = opt.get("option_type", "AUTRE")
                option_desc = opt.get("option_description", "")
                price_ht = opt.get("amount_before_tax")
                price_ttc = opt.get("amount_after_tax")

                if price_ht is None and price_ttc is None:
                    continue

                price = float(price_ttc if price_ttc is not None else price_ht)
                if price <= 0:
                    continue

                repair_type = option_to_canon_repair(option_type, option_desc)
                source_page_url = f"https://monsavmobile.fr/#!/fr/price/{mname.lower()}"

                all_rows.append({
                    "source_site": SOURCE_DOMAIN,
                    "source_url": source_page_url,
                    "source": source_page_url,
                    "timestamp": scrape_started_at,
                    "data_type": "repair_price",
                    "device_family": "Smartphone",
                    "brand": brand_norm,
                    "model": model_name,
                    "item_name": f"{repair_type} {brand_norm} {model_name}".strip(),
                    "repair_type_raw": option_desc or option_type,
                    "repair_type": repair_type,
                    "price": price,
                    "price_raw": f"{price:.2f} €",
                    "price_ht": round(float(price_ht), 2) if price_ht is not None else None,
                    "price_ttc": round(float(price_ttc), 2) if price_ttc is not None else None,
                    "currency": opt.get("currency", "EUR"),
                    "currency_symbol": "€",
                    "shop_name": PROVIDER_NAME,
                    "address": "Centre National SBE Global Care",
                    "city": "Boulogne-sur-Mer",
                    "postal_code": "62200",
                    "delay": "SAV Atelier national Agréé",
                    "warranty": "Garantie constructeur préservée",
                    "model_sku": str(model_id),
                    "repair_category": "Réparation SAV Agréé",
                    "scraped_at": scrape_started_at,
                })

        if max_models is not None and processed_models >= max_models:
            break

    # Déduplication standard
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

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

    for row in deduped_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # 1. Export des artefacts canoniques horodatés (.jsonl, .csv, .json)
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Miroir au format db_monsavmobile (.jsonl, .csv, .json)
    db_csv_path = out_dir / f"db_{SOURCE_NAME}.csv"
    write_jsonl_and_csv(deduped_rows, jsonl_path=db_jsonl_path, csv_path=db_csv_path)
    db_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] Terminé avec succès. {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

    # 2. Finalisation des artefacts de rapport (_run.json, _issues.jsonl)
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
        # Miroir des rapports pour db_monsavmobile_run.json et db_monsavmobile_issues.jsonl
        db_run_path = out_dir / f"db_{SOURCE_NAME}_run.json"
        db_issues_path = out_dir / f"db_{SOURCE_NAME}_issues.jsonl"
        db_run_path.write_text(json.dumps(payload_report.to_json_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        if not issues:
            db_issues_path.write_text("", encoding="utf-8")
        else:
            db_issues_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in issues) + "\n", encoding="utf-8")

        print(summarize_run_line(payload_report), flush=True)

    return deduped_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Mon SAV Mobile Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles à scraper")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
