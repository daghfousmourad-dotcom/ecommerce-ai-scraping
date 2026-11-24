"""WeFix scraper: discover model URLs via HTTP, scrape prices with HTTP.

Le site WeFix a été refondu et utilise maintenant de simples liens HTML
statiques pour la navigation device/marque/modèle — plus besoin de Playwright.

Flux de découverte :
  1. GET /reparation/smartphone  → liens vers chaque marque
  2. GET /reparation/smartphone/{brand}?tag=1  → liens vers chaque modèle
  3. Scraper chaque page modèle pour en extraire les prix de réparation
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

import csv
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tqdm import tqdm

from scraper.core.filtering import clean_dataset
from scraper.core.http_client import create_session, get_html, polite_sleep
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
    parse_html,
    parse_price_and_currency,
    to_slug,
)

BASE_URL = "https://www.wefix.net"
TARGET_CATEGORIES = ["smartphone", "tablette", "ordinateur-portable", "console"]


def discover_model_urls(session=None) -> list[str]:
    """
    Découverte des URLs de modèles WeFix via HTTP pur sur l'ensemble des catégories
    cibles (smartphone, tablette, ordinateur-portable, console).
    """
    print(f"[WeFix] Discovering model URLs across {TARGET_CATEGORIES} (HTTP)...", flush=True)
    if session is None:
        session = create_session()

    discovered: set[str] = set()

    # ── Étape 1 : récupérer la liste des marques par catégorie ─────────────────
    brand_urls: list[str] = []
    for cat in TARGET_CATEGORIES:
        cat_url = f"{BASE_URL}/reparation/{cat}"
        try:
            html_root = get_html(session, cat_url, retries=3, timeout_seconds=30)
            soup_root = parse_html(html_root)
            for a in soup_root.find_all("a", href=True):
                href: str = a["href"]
                parts = [p for p in href.rstrip("?").split("/") if p]
                if (
                    len(parts) == 3
                    and parts[0] == "reparation"
                    and parts[1] == cat
                ):
                    full_url = BASE_URL + href if href.startswith("/") else href
                    if "?" not in full_url:
                        full_url += "?tag=1"
                    brand_urls.append(full_url)
        except Exception as exc:
            print(f"[WeFix] Erreur chargement catégorie {cat} : {exc}", flush=True)

    # Dédupliquer les URLs de marque
    brand_urls = list(dict.fromkeys(brand_urls))
    print(f"[WeFix] {len(brand_urls)} marque(s) détectée(s) au total", flush=True)

    # ── Étape 2 : pour chaque marque, récupérer les modèles ───────────────────
    with tqdm(
        total=len(brand_urls),
        desc="[WeFix] Découverte marques",
        unit="marque",
        dynamic_ncols=True,
    ) as brand_pbar:
        for brand_url in brand_urls:
            url_parts = [p for p in urlparse(brand_url).path.split("/") if p]
            if len(url_parts) < 3:
                brand_pbar.update(1)
                continue
            cat_slug = url_parts[1]
            brand_slug = url_parts[2]
            try:
                html_brand = get_html(session, brand_url, retries=3, timeout_seconds=30)
                soup_brand = parse_html(html_brand)
                model_found = 0
                for a in soup_brand.find_all("a", href=True):
                    href = a["href"]
                    parts = [p for p in href.split("?")[0].rstrip("/").split("/") if p]
                    # Lien modèle : /reparation/{cat}/{brand}/{model}
                    if (
                        len(parts) == 4
                        and parts[0] == "reparation"
                        and parts[1] == cat_slug
                        and parts[2] == brand_slug
                    ):
                        full_url = BASE_URL + href if href.startswith("/") else href
                        if "?" not in full_url:
                            full_url += "?tag=1"
                        discovered.add(full_url)
                        model_found += 1

                # Si aucun modèle trouvé, garder l'URL de marque elle-même
                if model_found == 0:
                    discovered.add(brand_url)
            except Exception as exc:
                tqdm.write(f"[WeFix] Erreur marque {brand_slug}: {exc}")
            finally:
                brand_pbar.update(1)
            polite_sleep(0.3, 0.8)

    urls = sorted(discovered)
    print(f"[WeFix] Découverte terminée : {len(urls)} URL(s) collectée(s)", flush=True)
    return urls


# Alias de rétrocompatibilité pour tout code qui appellerait l'ancien nom
def discover_model_urls_with_playwright(headless: bool = True) -> list[str]:  # noqa: ARG001
    """Obsolète — redirige vers discover_model_urls() (HTTP pur)."""
    return discover_model_urls()


def _parse_repairs_from_model_html(
    model_url: str,
    html: str,
    *,
    scrape_started_at: str | None = None,
) -> list[dict[str, Any]]:
    soup = parse_html(html)
    records: list[dict[str, Any]] = []
    timestamp = now_iso_utc()

    path_parts = [p for p in urlparse(model_url).path.split("/") if p]
    device_type = path_parts[1] if len(path_parts) > 1 else ""
    brand_slug = path_parts[2] if len(path_parts) > 2 else ""
    model_slug = path_parts[3] if len(path_parts) > 3 else ""

    for input_el in soup.select(
        "label.select-pannes input, label.select-services input, label.select-pc-pannes input"
    ):
        repair_name = (
            input_el.get("data-name")
            or input_el.get("data-titre")
            or input_el.get("title")
            or ""
        ).strip()
        raw_price = (
            (input_el.get("data-final-price") or "").strip()
            or (input_el.get("data-original-price") or "").strip()
        )

        if not repair_name:
            continue

        # data-final-price usually has plain numbers; normalize with euro symbol for parser.
        parse_input = raw_price + "€" if raw_price and "€" not in raw_price else raw_price
        price, currency = parse_price_and_currency(parse_input)

        repair_norm, repair_matched = normalize_repair_type(repair_name)
        dev = normalize_device_name(
            device_type,
            brand=brand_slug.replace("-", " ").title(),
            model=model_slug.replace("-", " ").upper(),
        )

        model_str = dev.get("model") or model_slug.replace("-", " ").upper()
        model_norm = (model_str or "").lower().strip()

        records.append(
            {
                "source_site": "wefix",
                "source_url": model_url,
                "timestamp": timestamp,
                "scrape_started_at": scrape_started_at,
                "data_type": "repair_price",
                "device_type": device_type,
                "device_family": dev["device_family"],
                "brand": dev["brand"],
                "model": model_str,
                "model_normalized": model_norm,
                "product_or_part_name": repair_name,
                "repair_type_raw": repair_name,
                "repair_type": repair_norm,
                "repair_type_matched": repair_matched,
                "price": price,
                "currency": currency or "EUR",
                "price_raw": raw_price,
                "warranty": "1 an",
                "delay": "20 min",
                "shop_name": "WeFix Point de Vente / Fnac-Darty",
                "city": None,
                "postal_code": None,
            }
        )

    if records:
        return dedupe_dict_rows(records, ("source_url", "repair_type_raw", "price_raw"))

    # Fallback parser if structure changes and only text prices exist.
    full_text = soup.get_text(" ", strip=True)
    simple_matches = re.findall(r"([A-Za-zÀ-ÿ0-9'’\-\s]{3,50})\s+(\d+[.,]?\d*)\s*€", full_text)
    fallback_rows: list[dict[str, Any]] = []
    for repair_name, raw_value in simple_matches:
        price, currency = parse_price_and_currency(raw_value + "€")
        rname = repair_name.strip()
        repair_norm, repair_matched = normalize_repair_type(rname)
        dev = normalize_device_name(
            device_type,
            brand=brand_slug.replace("-", " ").title(),
            model=model_slug.replace("-", " ").upper(),
        )
        model_str = dev.get("model") or model_slug.replace("-", " ").upper()
        model_norm = (model_str or "").lower().strip()
        fallback_rows.append(
            {
                "source_site": "wefix",
                "source_url": model_url,
                "timestamp": timestamp,
                "scrape_started_at": scrape_started_at,
                "data_type": "repair_price",
                "device_type": device_type,
                "device_family": dev["device_family"],
                "brand": dev["brand"],
                "model": model_str,
                "model_normalized": model_norm,
                "product_or_part_name": rname,
                "repair_type_raw": rname,
                "repair_type": repair_norm,
                "repair_type_matched": repair_matched,
                "price": price,
                "currency": currency or "EUR",
                "price_raw": raw_value,
                "warranty": "1 an",
                "delay": "20 min",
                "shop_name": "WeFix Point de Vente / Fnac-Darty",
                "city": None,
                "postal_code": None,
            }
        )
    return dedupe_dict_rows(fallback_rows, ("source_url", "repair_type_raw", "price_raw"))


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    headless: bool = True,  # conservé pour compatibilité, non utilisé
    model_urls: list[str] | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Run WeFix scrape end-to-end.

    - discovers model URLs via HTTP (plus besoin de Playwright) sauf si model_urls fourni
    - scrapes chaque page modèle via HTTP
    - exports JSON + CSV + JSONL (fichiers finaux après dédupe / nettoyage optionnel)

    Les noms de fichiers incluent l'heure de début du scrape. Les lignes sont streamées
    pendant le run ; les fichiers sidecar sont réécrits à la fin.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scrape_started_at = now_iso_utc()
    ts_suffix = (
        scrape_started_at.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("T", "_")
    )

    session = create_session()
    urls = model_urls or discover_model_urls(session=session)
    if not urls:
        raise RuntimeError("No WeFix model URLs discovered. Check site flow/selectors.")

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"wefix_repair_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"wefix_repair_prices_{ts_suffix}.json"
    csv_path = out_dir / f"wefix_repair_prices_{ts_suffix}.csv"

    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None

    def _stream_row(row: dict[str, Any]) -> None:
        nonlocal csv_writer

        row_with_meta = dict(row)
        row_with_meta.setdefault("scrape_started_at", scrape_started_at)
        all_rows.append(row_with_meta)

        jsonl_file.write(json.dumps(row_with_meta, ensure_ascii=False) + "\n")
        jsonl_file.flush()

        if csv_writer is None:
            fieldnames = list(row_with_meta.keys())
            csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            csv_writer.writeheader()
        csv_writer.writerow(row_with_meta)
        csv_file.flush()

    with tqdm(
        total=len(urls),
        desc="[WeFix] Scraping",
        unit="page",
        dynamic_ncols=True,
    ) as page_pbar:
        for index, model_url in enumerate(urls, start=1):
            try:
                html = get_html(session, model_url, retries=3, timeout_seconds=30)
                rows = _parse_repairs_from_model_html(
                    model_url, html, scrape_started_at=scrape_started_at
                )
                for r in rows:
                    _stream_row(r)
                page_pbar.set_postfix(rows=len(rows), refresh=False)
            except Exception as exc:
                tqdm.write(f"[WeFix] Page {index}/{len(urls)} failed: {exc}")
                fetch_errors += 1
                if emit_run_report:
                    issues.append(
                        fetch_issue(
                            reason=REASON_FETCH_ERROR,
                            source_site="wefix",
                            source_url=model_url,
                            page_index=index,
                            detail=str(exc),
                        )
                    )
            page_pbar.update(1)
            polite_sleep(0.5, 1.3)

    jsonl_file.close()
    csv_file.close()

    all_rows = dedupe_dict_rows(all_rows, ("source_url", "repair_type_raw", "price_raw"))
    clean_report = None
    if apply_cleaning:
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            cleaning_issues=issues if emit_run_report else None,
        )
        print(clean_report.summary_line(), flush=True)
    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": "wefix",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[WeFix] Done. Exported {len(all_rows)} rows", flush=True)
    print(f"[WeFix] JSON : {json_path}", flush=True)
    print(f"[WeFix] JSONL: {jsonl_path}", flush=True)
    print(f"[WeFix] CSV  : {csv_path}", flush=True)
    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site="wefix",
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(all_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload), flush=True)
        if payload.paths:
            print(f"[WeFix] Run   : {payload.paths.run_json}", flush=True)
            print(f"[WeFix] Issues: {payload.paths.issues_jsonl}", flush=True)
    return all_rows


if __name__ == "__main__":
    scrape()

