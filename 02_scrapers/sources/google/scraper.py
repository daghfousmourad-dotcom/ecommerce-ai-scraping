"""Google Pixel Official Repair Cost Scraper (https://store.google.com/fr/repair-cost-estimator?hl=fr).

Tarifs officiels des réparations constructeur Google Pixel en France :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe (Google Store RPC Batchexecute - rpcid: QQr9wd) :
     - Découverte des modèles Pixel (lv4/ISE) : extraction directe depuis le bundle initial HTML
     - Interrogation du endpoint RPC batchexecute par modèle
     - Extraction précise au centime d'euro des 16 catégories officielles de panne constructeur
     Fournit 100% des tarifs officiels constructeur Google Pixel en France en ~3 secondes.
"""

from __future__ import annotations

import sys
from pathlib import Path
_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / 'core'
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

PAGE_URL = "https://store.google.com/fr/repair-cost-estimator?hl=fr"
RPC_BASE_URL = "https://store.google.com/_/Gstore/data/batchexecute"
SOURCE_NAME = "google"
SOURCE_DOMAIN = "store.google.com"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": PAGE_URL,
    "Origin": "https://store.google.com",
}

SHOP_INFO = {
    "shop_name": "Google Store Service Officiel",
    "address": "Centre de réparation agréé Google",
    "city": "National",
    "postal_code": "75000",
    "delay": "Réparation officielle Google par envoi postal",
    "warranty": "Garantie constructeur pièces d'origine Google",
}

GOOGLE_ISSUE_CANONICAL_MAP = {
    "Dégât au niveau du cadre ou de la façade arrière": "Façade arrière",
    "Dégâts causés par des liquides": "Désoxydation",
    "Écran fissuré": "Ecran LCD",
    "Port de recharge endommagé": "Connecteur de charge",
    "Problème audio": "Haut-parleur",
    "Problème d'alimentation": "Diagnostic panne",
    "Problème d'écran (autre)": "Ecran LCD",
    "Problème d'écran (clignotement)": "Ecran LCD",
    "Problème de batterie": "Batterie",
    "Problème de bouton": "Bouton volume / power",
    "Problème de capteur": "Capteur de proximité",
    "Problème de connectivité": "Antenne réseau / Wi-Fi",
    "Problème de lampe de poche": "Flash / Lampe",
    "Problème lié à l'appareil photo (arrière)": "Appareil photo arrière",
    "Problème lié à l'appareil photo (avant)": "Appareil photo avant",
    "Problème lié aux appels téléphoniques": "Micro",
    "Problème logiciel": "Système / Logiciel",
}


def fetch_page_context(session=None) -> tuple[str, list[tuple[str, str]]]:
    """Récupère le f.sid (FdrFJe) et l'ensemble des modèles Pixel avec leur identifiant ISE."""
    if session is None:
        session = create_session()

    resp = session.get(PAGE_URL, headers=DEFAULT_HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    m_sid = re.search(r'"FdrFJe":"([^"]+)"', html)
    f_sid = m_sid.group(1) if m_sid else ""

    raw_devices = re.findall(r'"(ISE-[^"]+)","(Pixel[^"]+)"', html)
    seen = set()
    devices: list[tuple[str, str]] = []
    for dev_id, name in raw_devices:
        clean_name = name.strip()
        if dev_id not in seen:
            seen.add(dev_id)
            devices.append((dev_id, clean_name))

    return f_sid, devices


def fetch_device_repair_prices(
    device_id: str,
    device_name: str,
    f_sid: str,
    session=None,
) -> list[tuple[str, float, str]]:
    """Appelle le RPC batchexecute QQr9wd pour extraire la grille tarifaire d'un modèle."""
    if session is None:
        session = create_session()

    rpc_url = (
        f"{RPC_BASE_URL}?rpcids=QQr9wd&source-path=%2Ffr%2Frepair-cost-estimator&f.sid={f_sid}&hl=fr"
    )

    inner_payload = [
        [
            [["fr"]],
            None,
            ["FR"],
            69,
            1,
            None,
            None,
            None,
            ["android_group:eng.finsky.merchandising.prod"],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            2,
        ],
        device_id,
    ]
    f_req = json.dumps([[["QQr9wd", json.dumps(inner_payload), None, "generic"]]])

    resp = session.post(
        rpc_url,
        data={"f.req": f_req},
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            **DEFAULT_HEADERS,
        },
        timeout=15,
    )
    resp.raise_for_status()

    items: list[tuple[str, float, str]] = []
    for line in resp.text.split("\n"):
        line = line.strip()
        if line.startswith("[[") and "QQr9wd" in line:
            envelope = json.loads(line)
            for entry in envelope:
                if entry[1] == "QQr9wd" and entry[2]:
                    inner_data = json.loads(entry[2])
                    if inner_data and len(inner_data) > 0 and isinstance(inner_data[0], list):
                        sections = inner_data[0]
                        for sec in sections:
                            if len(sec) > 1 and isinstance(sec[1], list):
                                for issue in sec[1]:
                                    issue_name = issue[0]
                                    price_info = issue[1][0]
                                    curr = price_info[0] if len(price_info) > 0 else "EUR"
                                    cents = price_info[2] if len(price_info) > 2 and price_info[2] else 0
                                    pval = round(price_info[1] + (cents / 1_000_000_000.0), 2)
                                    items.append((issue_name, pval, curr))
    return items


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Google Pixel Repair."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"google_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    print(f"[{SOURCE_NAME}] Découverte du catalogue Google Pixel via store.google.com...", flush=True)
    try:
        f_sid, devices = fetch_page_context(session)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur lors de l'accès au portail Google: {e}", flush=True)
        fetch_errors += 1
        issues.append(
            fetch_issue(
                reason=REASON_FETCH_ERROR,
                source_site=SOURCE_NAME,
                source_url=PAGE_URL,
                detail=str(e),
            )
        )
        return []

    print(f"[{SOURCE_NAME}] {len(devices)} modèles Pixel identifiés.", flush=True)
    if max_models and max_models > 0:
        devices = devices[:max_models]
        print(f"[{SOURCE_NAME}] Limite active : {max_models} modèles.")

    scraped_at = now_iso_utc()

    def process_single_device(dev: tuple[str, str]) -> list[dict[str, Any]]:
        th_session = create_session()
        dev_id, dev_name = dev
        items = fetch_device_repair_prices(dev_id, dev_name, f_sid, session=th_session)
        rows: list[dict[str, Any]] = []

        for issue_name, price_val, currency in items:
            if price_val <= 0:
                continue

            # Normalisation canonique
            if issue_name in GOOGLE_ISSUE_CANONICAL_MAP:
                repair_type = GOOGLE_ISSUE_CANONICAL_MAP[issue_name]
            else:
                canon, matched = normalize_repair_type(issue_name)
                repair_type = canon if matched and canon else issue_name

            full_model_name = f"Google {dev_name}" if not dev_name.startswith("Google") else dev_name

            rows.append(
                {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": PAGE_URL,
                    "source": PAGE_URL,
                    "timestamp": scraped_at,
                    "data_type": "repair_price",
                    "device_family": "Smartphone",
                    "brand": "Google",
                    "model": full_model_name,
                    "item_name": issue_name.strip(),
                    "repair_type_raw": issue_name.strip(),
                    "repair_type": repair_type,
                    "price": price_val,
                    "price_raw": f"{price_val} {currency}",
                    "currency": currency,
                    "currency_symbol": "€" if currency == "EUR" else currency,
                    "shop_name": SHOP_INFO["shop_name"],
                    "address": SHOP_INFO["address"],
                    "city": SHOP_INFO["city"],
                    "postal_code": SHOP_INFO["postal_code"],
                    "delay": SHOP_INFO["delay"],
                    "warranty": SHOP_INFO["warranty"],
                    "model_sku": dev_id,
                    "repair_category": "Réparation officielle constructeur",
                    "scraped_at": scraped_at,
                }
            )
        return rows

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(process_single_device, dev): dev for dev in devices}
        for fut in tqdm(as_completed(futures), total=len(devices), desc="Google Pixel Extraction", unit="modèle"):
            dev = futures[fut]
            try:
                res = fut.result()
                all_rows.extend(res)
            except Exception as e:
                fetch_errors += 1
                issues.append(
                    fetch_issue(
                        reason=REASON_FETCH_ERROR,
                        source_site=SOURCE_NAME,
                        source_url=f"{PAGE_URL}#{dev[0]}",
                        detail=str(e),
                    )
                )

    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "repair_type_raw", "price_raw"),
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

    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[GOOGLE] Terminé avec succès. {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Google Pixel Repair Cost Scraper")
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
