"""Docteur IT scraper: discover model URLs via HTTP, scrape prices with HTTP.

Flux de découverte :
  1. GET https://www.docteur-it.com/tarbes-meridien-sub/index/ (Magasin de référence)
  2. Parse de l'arborescence du méga-menu :
     - li.dropdown (Catégorie : Téléphone, Tablette, Console...)
     - li.dropdown-submenu (Marque : Apple, Samsung, Huawei...)
     - ul.dropdown-menu a[href*='-produit-'] (Modèle : iPhone 11, Galaxy S20...)
  3. Scraper chaque page modèle en parallèle (multithreadé) pour extraire les tarifs de réparation.
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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from tqdm import tqdm

from scraper.core.filtering import clean_dataset
from scraper.core.http_client import create_session, polite_sleep
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
)

BASE_URL = "https://www.docteur-it.com"
REFERENCE_STORE_PATH = "/tarbes-meridien-sub/index/"

_thread_local = threading.local()


def _get_thread_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = create_session()
    return _thread_local.session


def _safe_get_html(session, url: str, timeout_seconds: int = 20) -> str | None:
    """Récupère le HTML décodé en UTF-8 en évitant les boucles de redirection (404.html)."""
    try:
        resp = session.get(url, timeout=timeout_seconds, allow_redirects=False)
        if resp.is_redirect:
            loc = resp.headers.get("Location", "")
            if "404" in loc:
                return None
            session.max_redirects = 5
            resp = session.get(url, timeout=timeout_seconds)
        if resp.status_code == 200:
            return resp.content.decode("utf-8", errors="replace")
        return None
    except Exception:
        return None


def discover_model_catalog(session=None) -> dict[str, dict[str, str]]:
    """
    Découvre l'ensemble des modèles Docteur IT et leurs métadonnées
    (catégorie, marque, modèle) via l'arborescence du méga-menu
    du magasin de référence (Tarbes Méridien).
    """
    print(f"[DocteurIT] Discovering catalog and model URLs via {REFERENCE_STORE_PATH}...", flush=True)
    if session is None:
        session = create_session()

    catalog: dict[str, dict[str, str]] = {}
    start_url = urljoin(BASE_URL, REFERENCE_STORE_PATH)

    try:
        resp = session.get(start_url, timeout=30)
        resp.raise_for_status()
        html_root = resp.content.decode("utf-8", errors="replace")
        soup_root = parse_html(html_root)

        for nav in soup_root.select(".mega-menu ul.navbar-nav"):
            for cat_li in nav.find_all("li", class_="dropdown", recursive=False):
                cat_a = cat_li.find("a")
                cat_name = cat_a.get_text(strip=True) if cat_a else ""

                submenus = cat_li.find_all("li", class_="dropdown-submenu")
                if submenus:
                    for brand_li in submenus:
                        brand_a = brand_li.find("a")
                        brand_name = brand_a.get_text(strip=True) if brand_a else ""
                        for model_a in brand_li.select("ul.dropdown-menu a[href*='-produit-']"):
                            href = model_a.get("href")
                            full_url = urljoin(start_url, href)
                            model_name = model_a.get_text(strip=True)
                            catalog[full_url] = {
                                "category": cat_name,
                                "brand": brand_name,
                                "model": model_name,
                                "url": full_url,
                            }
                else:
                    # Liens directs sans sous-menu de marque (ex: iPod)
                    for model_a in cat_li.select("ul.dropdown-menu a[href*='-produit-']"):
                        href = model_a.get("href")
                        full_url = urljoin(start_url, href)
                        model_name = model_a.get_text(strip=True)
                        brand_name = "Apple" if "ipod" in cat_name.lower() else cat_name
                        catalog[full_url] = {
                            "category": cat_name,
                            "brand": brand_name,
                            "model": model_name,
                            "url": full_url,
                        }

    except Exception as exc:
        print(f"[DocteurIT] Erreur lors de la découverte: {exc}", flush=True)

    print(f"[DocteurIT] Découverte terminée : {len(catalog)} modèle(s) avec marque et modèle qualifiés", flush=True)
    return catalog


def discover_model_urls(session=None) -> list[str]:
    """Retourne la liste triée des URLs de produits."""
    catalog = discover_model_catalog(session=session)
    return sorted(catalog.keys())


def _parse_repairs_from_model_html(
    model_url: str,
    html: str,
    *,
    model_info: dict[str, str] | None = None,
    scrape_started_at: str | None = None,
) -> list[dict[str, Any]]:
    soup = parse_html(html)
    records: list[dict[str, Any]] = []
    timestamp = now_iso_utc()

    category_raw = ""
    brand_raw = ""
    model_raw = ""

    if model_info:
        category_raw = model_info.get("category", "")
        brand_raw = model_info.get("brand", "")
        model_raw = model_info.get("model", "")

    # Fallback to H1 tag if metadata was missing or incomplete
    if not brand_raw or not model_raw:
        h1 = soup.find("h1")
        if h1:
            h1_text = h1.get_text(strip=True)
            match = re.match(r"^Les r[ée]parations\s+(.+)$", h1_text, re.IGNORECASE)
            if match:
                rest = match.group(1).strip()
                tokens = rest.split(" ", 1)
                if len(tokens) == 2:
                    if not brand_raw:
                        brand_raw = tokens[0]
                    if not model_raw:
                        model_raw = tokens[1]
                elif len(tokens) == 1:
                    if not model_raw:
                        model_raw = tokens[0]

    # Map device type
    cat_lower = (category_raw or "").lower()
    device_type = "smartphone"
    if "tablette" in cat_lower or "ipad" in (model_raw or "").lower():
        device_type = "tablette"
    elif "ordinateur" in cat_lower or "pc" in cat_lower or "mac" in cat_lower:
        device_type = "ordinateur-portable"
    elif "console" in cat_lower:
        device_type = "console"
    elif "ipod" in cat_lower:
        device_type = "baladeur"

    dev = normalize_device_name(
        device_type,
        brand=brand_raw,
        model=model_raw,
    )
    final_brand = dev.get("brand") or brand_raw
    model_str = dev.get("model") or model_raw
    model_norm = (model_str or "").lower().strip()

    # Find repairs blocks
    repair_blocks = soup.select(".pricing-table-v1 .pricing-v1")
    for block in repair_blocks:
        title_el = block.select_one("h2.title-font")
        price_el = block.select_one("h1 span.text-font")

        if not title_el or not price_el:
            continue

        repair_name = title_el.get_text(strip=True)
        raw_price = price_el.get_text(strip=True)

        if not repair_name:
            continue

        price, currency = parse_price_and_currency(raw_price)
        repair_norm, repair_matched = normalize_repair_type(repair_name)

        if model_str and model_str.lower() in repair_name.lower():
            product_name = repair_name
        else:
            product_name = f"{repair_name} - {model_str}" if model_str else repair_name

        records.append(
            {
                "source_site": "docteur_it",
                "source_url": model_url,
                "timestamp": timestamp,
                "scrape_started_at": scrape_started_at,
                "data_type": "repair_price",
                "device_type": device_type,
                "device_family": dev.get("device_family"),
                "brand": final_brand,
                "model": model_str,
                "model_normalized": model_norm,
                "product_or_part_name": product_name,
                "repair_type_raw": repair_name,
                "repair_type": repair_norm,
                "repair_type_matched": repair_matched,
                "price": price,
                "currency": currency or "EUR",
                "price_raw": raw_price,
                "warranty": "1 an",
                "delay": None,
                "shop_name": "Docteur IT (Tarbes Méridien - Ref)",
                "city": "Tarbes",
                "postal_code": "65000",
            }
        )

    return dedupe_dict_rows(records, ("source_url", "repair_type_raw", "price_raw"))


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    model_urls: list[str] | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_workers: int = 8,
) -> list[dict[str, Any]]:
    """
    Run Docteur IT scrape end-to-end concurrently.
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
    catalog = discover_model_catalog(session=session)

    if model_urls is not None:
        target_urls = [u for u in model_urls if u in catalog or u]
    else:
        target_urls = sorted(catalog.keys())

    if not target_urls:
        raise RuntimeError("No Docteur IT model URLs discovered. Check site flow/selectors.")

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"docteur_it_repair_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"docteur_it_repair_prices_{ts_suffix}.json"
    csv_path = out_dir / f"docteur_it_repair_prices_{ts_suffix}.csv"

    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None
    write_lock = threading.Lock()

    def _stream_rows(rows: list[dict[str, Any]]) -> None:
        nonlocal csv_writer
        if not rows:
            return
        with write_lock:
            for row in rows:
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

    def _worker_task(item: tuple[int, str]) -> tuple[int, str, list[dict[str, Any]], str | None]:
        idx, url = item
        s = _get_thread_session()
        meta = catalog.get(url)
        html = _safe_get_html(s, url)
        if not html:
            return idx, url, [], "Page non accessible ou inexistante"
        try:
            rows = _parse_repairs_from_model_html(
                url, html, model_info=meta, scrape_started_at=scrape_started_at
            )
            return idx, url, rows, None
        except Exception as exc:
            return idx, url, [], str(exc)

    items = list(enumerate(target_urls, start=1))

    with tqdm(
        total=len(items),
        desc="[DocteurIT] Scraping",
        unit="page",
        dynamic_ncols=True,
    ) as page_pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_worker_task, it): it for it in items}
            for fut in as_completed(futures):
                idx, url, rows, err = fut.result()
                if err:
                    fetch_errors += 1
                    if emit_run_report:
                        issues.append(
                            fetch_issue(
                                reason=REASON_FETCH_ERROR,
                                source_site="docteur_it",
                                source_url=url,
                                page_index=idx,
                                detail=err,
                            )
                        )
                else:
                    _stream_rows(rows)

                page_pbar.set_postfix(rows=len(all_rows), refresh=False)
                page_pbar.update(1)

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
        "source_site": "docteur_it",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[DocteurIT] Done. Exported {len(all_rows)} rows", flush=True)
    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site="docteur_it",
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
        print(summarize_run_line(payload_report), flush=True)

    return all_rows


if __name__ == "__main__":
    scrape()
