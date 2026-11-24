"""Corner Mobile scraper: discover model URLs via HTML sitemap & scrape repair prices.

Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe : Aucune API JSON exposée par le serveur (site statique HTML).
  2. Content/HTML Scraping : Parsing HTML direct ultra-rapide (BeautifulSoup + ThreadPoolExecutor).
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
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
    parse_html,
    parse_price_and_currency,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

BASE_URL = "https://cornermobile.fr"

_thread_local = threading.local()


def _get_thread_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = create_session()
    return _thread_local.session


def _safe_get_html(session, url: str, timeout_seconds: int = 15) -> str | None:
    try:
        resp = session.get(url, timeout=timeout_seconds)
        if resp.status_code == 200:
            return resp.content.decode("utf-8", errors="replace")
        return None
    except Exception:
        return None


def discover_model_urls(session=None) -> list[str]:
    """Découvre toutes les pages de réparation smartphone de Corner Mobile."""
    if session is None:
        session = create_session()

    print("[CornerMobile] Découverte des fiches modèles via la page d'accueil...", flush=True)
    resp = session.get(BASE_URL, timeout=15)
    resp.raise_for_status()
    html = resp.content.decode("utf-8", errors="replace")

    raw_links = re.findall(r'href=[\"\'](/reparation-[^\"\']+\.html)[\"\']', html)
    model_urls = []

    # Exclusions des pages de villes génériques et des catégories hors smartphone
    exclude_prefixes = (
        "/reparation-telephone-",
        "/reparation-ordinateur-",
        "/reparation-trottinette-",
        "/reparation-tablette-",
        "/autres-reparations-",
    )

    for l in sorted(set(raw_links)):
        if any(l.startswith(p) for p in exclude_prefixes):
            continue
        full_url = urljoin(BASE_URL, l)
        model_urls.append(full_url)

    print(f"[CornerMobile] {len(model_urls)} fiches modèles smartphones identifiées.", flush=True)
    return model_urls


def _extract_brand_model(h1_text: str, url: str) -> tuple[str, str]:
    """Détermine la marque et le modèle canoniques à partir du slug d'URL et du H1."""
    slug = url.split("/")[-1].replace(".html", "")
    slug = re.sub(r"(?i)^reparation-", "", slug)
    slug = re.sub(r"(?i)-lille(-\d+)?$", "", slug)
    parts = slug.split("-")
    first = parts[0].lower()

    if first == "iphone":
        brand = "Apple"
        model = "iPhone " + " ".join(parts[1:]).title()
    elif first == "google" and len(parts) > 1 and parts[1].lower() == "pixel":
        brand = "Google"
        model = "Pixel " + " ".join(parts[2:]).title()
    elif first == "samsung":
        brand = "Samsung"
        model = " ".join(parts[1:]).title()
    elif first in ("xiaomi", "redmi", "poco"):
        brand = "Xiaomi"
        model = " ".join(parts).title()
    elif first == "huawei":
        brand = "Huawei"
        model = " ".join(parts[1:]).title()
    elif first == "honor":
        brand = "Honor"
        model = " ".join(parts[1:]).title()
    elif first == "oppo":
        brand = "Oppo"
        model = " ".join(parts[1:]).title()
    else:
        brand = first.title()
        model = " ".join(parts[1:]).title()

    dev = normalize_device_name("smartphone", brand=brand, model=model)
    final_brand = dev.get("brand") or brand
    final_model = dev.get("model") or model
    return final_brand, final_model


def _parse_repairs_from_html(url: str, html: str, scrape_started_at: str) -> list[dict[str, Any]]:
    soup = parse_html(html)
    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else ""
    brand, model = _extract_brand_model(h1_text, url)

    rows: list[dict[str, Any]] = []

    for h3 in soup.find_all("h3"):
        title = h3.get_text(strip=True)
        # Filtre sur les vraies cartes d'intervention
        if not any(kw in title.lower() for kw in [
            "remplacement", "réparation", "reparation", "ecran", "écran",
            "batterie", "vitre", "connecteur", "caméra", "camera", "désoxydation", "desoxydation"
        ]):
            continue

        card = h3.parent
        card_text = card.get_text(separator=" | ", strip=True)

        # Extraction du prix (ex: "dès | 199 €" ou "199 €")
        p_match = re.search(r"(\d+[\.,]?\d*)\s*€", card_text)
        if not p_match:
            continue

        price_raw = p_match.group(0)
        price_val, currency = parse_price_and_currency(price_raw)
        if price_val is None or price_val <= 0:
            continue

        # Extraction garantie
        warranty = "Garantie 1 an pièce + main d'œuvre"
        if "3 mois" in card_text.lower():
            warranty = "Garantie 3 mois pièce + main d'œuvre"

        canon_repair, _ = normalize_repair_type(title)

        row = {
            "source_site": "cornermobile.fr",
            "source_url": url,
            "source": url,
            "timestamp": now_iso_utc(),
            "data_type": "repair_price",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model,
            "item_name": title,
            "repair_type_raw": title,
            "repair_type": canon_repair or title,
            "repair_type_matched": bool(canon_repair),
            "price": price_val,
            "currency": currency or "EUR",
            "price_raw": price_raw,
            "delay": "30 minutes en boutique",
            "warranty": warranty,
            "shop_name": "Corner Mobile Lille",
            "address": "34 rue des Sarrazins",
            "city": "Lille",
            "postal_code": "59000",
            "scrape_started_at": scrape_started_at,
        }
        rows.append(row)

    return rows


def scrape(
    output_dir: str | Path = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_workers: int = 8,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Exécute le scraping intégral des tarifs de réparation de Corner Mobile."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scrape_started_at = now_iso_utc()
    ts_compact = re.sub(r"[^0-9]", "", scrape_started_at)
    stem = f"corner_mobile_repair_prices_{ts_compact}"

    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"
    json_path = out_dir / f"{stem}.json"

    session = create_session()
    model_urls = discover_model_urls(session=session)

    if max_models and max_models > 0:
        model_urls = model_urls[:max_models]
        print(f"[CornerMobile] Limite active : {max_models} modèles à scraper.")

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0
    file_lock = threading.Lock()

    jsonl_file = open(jsonl_path, "w", encoding="utf-8")
    csv_file = open(csv_path, "w", encoding="utf-8", newline="")
    csv_writer = None

    def _stream_rows(rows: list[dict[str, Any]]) -> None:
        nonlocal csv_writer
        if not rows:
            return
        with file_lock:
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
        html = _safe_get_html(s, url)
        if not html:
            return idx, url, [], "Page non accessible ou erreur HTTP"
        try:
            rows = _parse_repairs_from_html(url, html, scrape_started_at=scrape_started_at)
            return idx, url, rows, None
        except Exception as exc:
            return idx, url, [], str(exc)

    items = list(enumerate(model_urls, start=1))

    with tqdm(
        total=len(items),
        desc="[CornerMobile] Scraping",
        unit="page",
        dynamic_ncols=True,
    ) as pbar:
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
                                source_site="corner_mobile",
                                source_url=url,
                                page_index=idx,
                                detail=err,
                            )
                        )
                else:
                    _stream_rows(rows)

                pbar.set_postfix(tarifs=len(all_rows), refresh=False)
                pbar.update(1)

    jsonl_file.close()
    csv_file.close()

    # Déduplication
    all_rows = dedupe_dict_rows(all_rows, ("source_url", "repair_type_raw", "price_raw"))

    clean_report = None
    if apply_cleaning:
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": "corner_mobile",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[CornerMobile] Terminé avec succès. {len(all_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site="corner_mobile",
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
