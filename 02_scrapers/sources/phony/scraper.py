"""PHONY Scraper (https://phony.fr).

Atelier de réparation smartphones à Tours / Saint-Avertin (37) :
- Univers : repair_price (Tarifs de réparation smartphones 9 marques : Apple, Samsung, Google Pixel, Xiaomi, Huawei, OPPO, Vivo, Realme, Motorola)
- Source API officielle : https://phony.fr/get_data.php?action=catalogue_grid
- Données : 327 modèles avec prix fermes par panne et par qualité (Premium / Originale)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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

SOURCE_NAME = "phony"
SOURCE_DOMAIN = "phony.fr"
PAGE_URL = "https://phony.fr/tarifs-reparation-telephone-tours"
API_BASE = "https://phony.fr/get_data.php?action=catalogue_grid"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CANONICAL_REPAIR_MAP = {
    "écran": "Écran",
    "ecran": "Écran",
    "batterie": "Batterie",
    "connecteur": "Connecteur de charge",
    "charge": "Connecteur de charge",
    "caméra arrière": "Caméra arrière",
    "caméra avant": "Caméra avant",
    "vitre arrière": "Façade arrière",
    "lentille": "Lentille caméra",
    "microphone": "Audio / Haut-parleur",
    "bouton": "Boutons & Nappes",
    "désoxydation": "Désoxydation",
    "diagnostic": "Diagnostic",
}


def get_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(pool_connections=15, pool_maxsize=15, max_retries=retries))
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": PAGE_URL,
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def map_repair_category(service_name: str) -> str:
    low = service_name.lower()
    for kw, cat in CANONICAL_REPAIR_MAP.items():
        if kw in low:
            return cat
    return "Autre réparation"


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

    print(f"[{SOURCE_NAME}] Initialisation session et découverte des services ({PAGE_URL})...")
    pairs: list[tuple[str, str]] = []
    try:
        r_page = session.get(PAGE_URL, timeout=12)
        if r_page.status_code == 200:
            soup = BeautifulSoup(r_page.text, "html.parser")
            panels = soup.select(".t-svc-panel")
            for p in panels:
                m = p.get("data-marque")
                s = p.get("data-service")
                if m and s:
                    pairs.append((m.strip(), s.strip()))
        else:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=PAGE_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {r_page.status_code}"))
    except Exception as e:
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=PAGE_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    pairs = list(dict.fromkeys(pairs))
    print(f"[{SOURCE_NAME}] Trouvé {len(pairs)} combinaisons (Marque, Service).")

    if max_models:
        pairs = pairs[:max_models]

    def fetch_panel(m: str, s: str):
        url = f"{API_BASE}&marque={requests.utils.quote(m)}&service={requests.utils.quote(s)}"
        try:
            r = session.get(url, timeout=10)
            if r.status_code == 200:
                return m, s, r.json(), None
            return m, s, None, f"HTTP {r.status_code}"
        except Exception as exc:
            return m, s, None, str(exc)

    print(f"[{SOURCE_NAME}] Interception concurrente des flux API pour {len(pairs)} panneaux...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_panel, m, s): (m, s) for m, s in pairs}
        for future in as_completed(futures):
            m, s, data, err = future.result()
            if err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=PAGE_URL, reason=REASON_FETCH_ERROR, message=f"{m} / {s}: {err}"))
            elif data and data.get("ok"):
                is_quality = bool(data.get("quality"))
                items = data.get("items", [])
                for item in items:
                    modele = str(item.get("modele", "")).strip()
                    if not modele:
                        continue

                    brand = m
                    canonical_cat = map_repair_category(s)

                    # Si grille multi-qualités (Premium vs Original)
                    if is_quality:
                        b_val = float(item.get("prix_basic", 0) or 0)
                        o_val = float(item.get("prix_original", 0) or 0)
                        n_val = float(item.get("prix", 0) or 0)

                        variants_to_add = []
                        if b_val > 0:
                            variants_to_add.append((f"{s} (Premium)", b_val))
                        if o_val > 0:
                            variants_to_add.append((f"{s} (Originale)", o_val))
                        if not variants_to_add and n_val > 0:
                            variants_to_add.append((s, n_val))

                        for rep_name, p_val in variants_to_add:
                            item_name = f"{rep_name} {brand} {modele}".strip()
                            rows.append({
                                "source_site": SOURCE_DOMAIN,
                                "source_url": PAGE_URL,
                                "source": PAGE_URL,
                                "timestamp": scrape_started_at,
                                "scrape_started_at": scrape_started_at,
                                "data_type": "repair_price",
                                "device_type": "smartphone",
                                "device_family": "Smartphone",
                                "brand": brand,
                                "model": modele,
                                "model_normalized": f"{brand} {modele}".strip(),
                                "item_name": item_name,
                                "repair_category": canonical_cat,
                                "repair_type": canonical_cat,
                                "repair_type_raw": rep_name,
                                "price": p_val,
                                "price_raw": f"{p_val:.2f} €".replace(".", ","),
                                "currency": "EUR",
                                "currency_symbol": "€",
                                "workshop_name": "PHONY Tours",
                                "seller_name": "PHONY",
                                "delay": "Réparation en 30 minutes en boutique",
                                "warranty": "Garantie 3 mois pièces et main-d'œuvre",
                                "scraped_at": scrape_started_at,
                            })
                    else:
                        price_val = float(item.get("prix", 0) or 0)
                        if price_val > 0:
                            item_name = f"{s} {brand} {modele}".strip()
                            rows.append({
                                "source_site": SOURCE_DOMAIN,
                                "source_url": PAGE_URL,
                                "source": PAGE_URL,
                                "timestamp": scrape_started_at,
                                "scrape_started_at": scrape_started_at,
                                "data_type": "repair_price",
                                "device_type": "smartphone",
                                "device_family": "Smartphone",
                                "brand": brand,
                                "model": modele,
                                "model_normalized": f"{brand} {modele}".strip(),
                                "item_name": item_name,
                                "repair_category": canonical_cat,
                                "repair_type": canonical_cat,
                                "repair_type_raw": s,
                                "price": price_val,
                                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                                "currency": "EUR",
                                "currency_symbol": "€",
                                "workshop_name": "PHONY Tours",
                                "seller_name": "PHONY",
                                "delay": "Réparation en 30 minutes en boutique",
                                "warranty": "Garantie 3 mois pièces et main-d'œuvre",
                                "scraped_at": scrape_started_at,
                            })

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
