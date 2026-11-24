"""Generic targets scraper for mixed local shop pages listed in targets.csv."""

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

from scraper.core.http_client import create_session, get_html, polite_sleep
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.parser_utils import (
    clean_text,
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

DEFAULT_TARGETS_CSV = "scraper/targets.csv"

_PRICE_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)[\s ]*(?:€|eur(?:os?)?)", re.I)

_REPAIR_RE = re.compile(
    r"r[eé]par(?:ation|er)|remplacement|changer?|changement|diagnostic|forfait|screen|batterie|battery",
    re.I,
)
_PART_RE = re.compile(
    r"pi[eè]ces?\s*d[eé]tach[eé]es?|pi[eè]ces?|vitre|chassis|coque|connecteur|micro|haut.parleur",
    re.I,
)
_USED_RE = re.compile(
    r"occasion|recondition[n]?[eé]|refurb|seconde?\s*main|back\s*market|achat.{0,10}reprise",
    re.I,
)
_NEW_PHONE_RE = re.compile(
    r"\bneuf\b|\bnew\b|t[eé]l[eé]phone\s*neuf|smartphone\s*neuf|achat\s*neuf",
    re.I,
)

_PHONE_MODEL_RE = re.compile(
    r"\b(i[- ]?phone\s+(?:se|mini|\d{1,2}(?:\s*pro(?:\s*max)?|\s*plus|\s*mini)?)|"
    r"samsung\s+galaxy\s+(?:[samnz]\s*\d+\s*(?:fe|ultra|\+|plus)?)|"
    r"pixel\s+\d+\s*(?:a|xl)?|"
    r"huawei\s+(?:p|mate|nova)\s*\d+\s*\w*|"
    r"xiaomi\s+(?:redmi|poco|mi)\s*\w+)\b",
    re.I,
)

_SKIP_HOSTS = frozenset({"backmarket.fr"})

_MODEL_COL_WORDS = frozenset(["modèle", "model", "appareil", "téléphone", "telephone", "smartphone", "produit"])
_PRICE_COL_WORDS = frozenset(["prix", "price", "tarif", "coût", "cout", "montant"])
_REPAIR_COL_WORDS = frozenset(["réparation", "reparation", "service", "prestation", "panne", "forfait"])


def _read_targets_csv(path: str) -> list[str]:
    urls: list[str] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or not url.startswith("http"):
                continue
            urls.append(url)
    return list(dict.fromkeys(urls))


def _source_site(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


def _should_skip(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host in _SKIP_HOSTS


def _classify_data_type(url: str, context: str) -> str:
    hay = f"{url} {context}"
    if _USED_RE.search(hay):
        return "used_phone_price"
    if _NEW_PHONE_RE.search(hay):
        return "new_phone_price"
    if _PART_RE.search(hay):
        return "part_price"
    return "repair_price"


def _extract_model(text: str) -> str:
    m = _PHONE_MODEL_RE.search(text)
    return clean_text(m.group(0)) if m else ""


def _make_row(
    *,
    source_url: str,
    context: str,
    price: float,
    price_raw: str,
    currency: str,
    repair_label: str,
    model: str,
    scrape_started_at: str,
    timestamp: str,
) -> dict[str, Any]:
    repair_norm, repair_matched = normalize_repair_type(repair_label or context)
    data_type = _classify_data_type(source_url, context)
    dev = normalize_device_name("smartphone", brand="", model=model)
    return {
        "source_site": _source_site(source_url),
        "source_url": source_url,
        "source": source_url,
        "timestamp": timestamp,
        "data_type": data_type,
        "device_family": dev["device_family"],
        "brand": dev["brand"],
        "model": dev["model"] or model,
        "item_name": context[:200],
        "repair_type_raw": repair_label[:200] if repair_label else context[:80],
        "repair_type": repair_norm,
        "repair_type_matched": repair_matched,
        "price": price,
        "currency": currency or "EUR",
        "price_raw": price_raw,
        "scrape_started_at": scrape_started_at,
    }


def _parse_tables(soup, source_url: str, scrape_started_at: str) -> list[dict[str, Any]]:
    """Extract price rows from HTML <table> elements."""
    rows: list[dict[str, Any]] = []
    ts = now_iso_utc()

    for table in soup.find_all("table"):
        all_trs = table.find_all("tr")
        if not all_trs:
            continue

        header_cells = all_trs[0].find_all(["th", "td"])
        headers = [clean_text(h.get_text(" ", strip=True)) for h in header_cells]

        model_col: int | None = None
        repair_cols: list[int] = []
        price_cols: list[int] = []

        for i, h in enumerate(headers):
            h_l = h.lower()
            if any(w in h_l for w in _MODEL_COL_WORDS):
                model_col = i
            elif _REPAIR_RE.search(h) or any(w in h_l for w in _REPAIR_COL_WORDS):
                repair_cols.append(i)
            elif "€" in h or any(w in h_l for w in _PRICE_COL_WORDS):
                price_cols.append(i)

        data_trs = all_trs[1:] if headers else all_trs

        for tr in data_trs:
            cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if not cells or all(not c for c in cells):
                continue

            model_name = (
                cells[model_col]
                if model_col is not None and model_col < len(cells)
                else ""
            )

            # Pattern A: repair type names are column headers, prices are cells
            if repair_cols:
                for ri in repair_cols:
                    if ri >= len(cells):
                        continue
                    cell_text = cells[ri]
                    price, currency = parse_price_and_currency(cell_text)
                    if price is None:
                        m = _PRICE_RE.search(cell_text)
                        if not m:
                            continue
                        price, currency = parse_price_and_currency(m.group(0) + " €")
                    if price is None or price <= 0:
                        continue

                    repair_label = headers[ri] if ri < len(headers) else ""
                    model = model_name or _extract_model(" ".join(cells))
                    context = clean_text(f"{model} {repair_label} {cell_text}")
                    rows.append(
                        _make_row(
                            source_url=source_url,
                            context=context,
                            price=price,
                            price_raw=cell_text,
                            currency=currency or "EUR",
                            repair_label=repair_label,
                            model=model,
                            scrape_started_at=scrape_started_at,
                            timestamp=ts,
                        )
                    )
                continue

            # Pattern B: explicit price column(s)
            if price_cols:
                for pi in price_cols:
                    if pi >= len(cells):
                        continue
                    cell_text = cells[pi]
                    price, currency = parse_price_and_currency(cell_text)
                    if price is None:
                        m = _PRICE_RE.search(cell_text)
                        if not m:
                            continue
                        price, currency = parse_price_and_currency(m.group(0) + " €")
                    if price is None or price <= 0:
                        continue

                    other_cells = [c for j, c in enumerate(cells) if j != pi and c]
                    repair_label = " ".join(other_cells)[:120]
                    model = model_name or _extract_model(repair_label)
                    context = clean_text(" ".join(cells))
                    rows.append(
                        _make_row(
                            source_url=source_url,
                            context=context,
                            price=price,
                            price_raw=cell_text,
                            currency=currency or "EUR",
                            repair_label=repair_label,
                            model=model,
                            scrape_started_at=scrape_started_at,
                            timestamp=ts,
                        )
                    )
                continue

            # Pattern C: no recognised column structure — scan each row for prices
            row_text = " ".join(cells)
            for m in _PRICE_RE.finditer(row_text):
                price, currency = parse_price_and_currency(m.group(0) + " €")
                if price is None or price <= 0:
                    continue
                model = model_name or _extract_model(row_text)
                context = clean_text(row_text)
                rows.append(
                    _make_row(
                        source_url=source_url,
                        context=context,
                        price=price,
                        price_raw=m.group(0),
                        currency=currency or "EUR",
                        repair_label=context[:80],
                        model=model,
                        scrape_started_at=scrape_started_at,
                        timestamp=ts,
                    )
                )

    return rows


def _parse_price_cards(soup, source_url: str, scrape_started_at: str) -> list[dict[str, Any]]:
    """Extract prices from styled price-card/list elements (non-table layouts)."""
    rows: list[dict[str, Any]] = []
    ts = now_iso_utc()
    seen: set[str] = set()

    card_selectors = [
        "[class*='price-item']", "[class*='service-price']", "[class*='tarif']",
        "[class*='repair']", "[class*='service']", "[class*='forfait']",
        "[class*='panne']", "[class*='prestati']",
        "li[class*='product']", "li[class*='repair']", "li[class*='tarif']",
        "div[class*='price']", "div[class*='card']", "span[class*='price']",
        "[class*='reparation']", "[class*='intervention']",
    ]
    selector = ", ".join(card_selectors)
    containers = soup.select(selector)

    for container in containers:
        text = clean_text(container.get_text(" ", strip=True))
        if not text or len(text) > 600:
            continue
        for m in _PRICE_RE.finditer(text):
            price, currency = parse_price_and_currency(m.group(0) + " €")
            if price is None or price <= 0:
                continue
            key = f"{source_url}|{text[:80]}|{m.group(0)}"
            if key in seen:
                continue
            seen.add(key)
            model = _extract_model(text)
            context = text[:200]
            rows.append(
                _make_row(
                    source_url=source_url,
                    context=context,
                    price=price,
                    price_raw=m.group(0),
                    currency=currency or "EUR",
                    repair_label=context[:80],
                    model=model,
                    scrape_started_at=scrape_started_at,
                    timestamp=ts,
                )
            )

    return rows


def _parse_text_nodes(soup, source_url: str, scrape_started_at: str) -> list[dict[str, Any]]:
    """
    Fallback: scan every block-level container for prices.

    Uses combined element text (via get_text) rather than individual text nodes so
    that prices split across inline spans (e.g. "49" in one <span> and "€" in
    another) are still captured.
    """
    rows: list[dict[str, Any]] = []
    ts = now_iso_utc()
    seen: set[str] = set()

    _BLOCK_TAGS = {"p", "li", "td", "th", "div", "span", "h2", "h3", "h4", "dt", "dd", "section"}
    _INLINE_TAGS = {"a", "strong", "em", "b", "i", "small", "sup", "sub", "span"}

    # Walk every element whose combined text contains a price
    for el in soup.find_all(_BLOCK_TAGS):
        # Skip containers that have block-level children (they'll be visited individually)
        if any(c.name in (_BLOCK_TAGS - _INLINE_TAGS) for c in el.children if hasattr(c, "name") and c.name):
            continue
        text = clean_text(el.get_text(" ", strip=True))
        if not text or len(text) > 400:
            continue
        if not _PRICE_RE.search(text):
            continue

        for m in _PRICE_RE.finditer(text):
            price, currency = parse_price_and_currency(m.group(0) + " €")
            if price is None or price <= 0:
                continue
            key = f"{source_url}|{text[:80]}|{m.group(0)}"
            if key in seen:
                continue
            seen.add(key)
            model = _extract_model(text)
            rows.append(
                _make_row(
                    source_url=source_url,
                    context=text[:200],
                    price=price,
                    price_raw=m.group(0),
                    currency=currency or "EUR",
                    repair_label=text[:80],
                    model=model,
                    scrape_started_at=scrape_started_at,
                    timestamp=ts,
                )
            )

    return rows


def extract_rows_from_page(
    soup, source_url: str, scrape_started_at: str
) -> list[dict[str, Any]]:
    """
    Multi-strategy extraction (tables → price cards → text nodes).
    Returns deduplicated rows.
    """
    # Strategy 1: structured tables (highest quality)
    table_rows = _parse_tables(soup, source_url, scrape_started_at)
    if table_rows:
        return dedupe_dict_rows(table_rows, ("source_url", "item_name", "price_raw", "data_type"))

    # Strategy 2: styled price cards / list items
    card_rows = _parse_price_cards(soup, source_url, scrape_started_at)
    if card_rows:
        return dedupe_dict_rows(card_rows, ("source_url", "item_name", "price_raw", "data_type"))

    # Strategy 3: generic text scan (most permissive, most noise)
    text_rows = _parse_text_nodes(soup, source_url, scrape_started_at)
    return dedupe_dict_rows(text_rows, ("source_url", "item_name", "price_raw", "data_type"))


def scrape(
    *,
    targets_csv_path: str = DEFAULT_TARGETS_CSV,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    timeout_seconds: int = 30,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Scrape all URLs listed in targets.csv.

    Multi-strategy extraction: tables → price cards → text nodes.
    Skips backmarket.fr URLs (handled separately by --source backmarket).
    Outputs a single combined JSON/JSONL/CSV.
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

    targets = _read_targets_csv(targets_csv_path)
    session = create_session()

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"targets_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"targets_prices_{ts_suffix}.json"
    csv_path = out_dir / f"targets_prices_{ts_suffix}.csv"

    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None

    def _stream_row(row: dict[str, Any]) -> None:
        nonlocal csv_writer
        all_rows.append(row)
        jsonl_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        jsonl_file.flush()
        if csv_writer is None:
            csv_writer = csv.DictWriter(csv_file, fieldnames=list(row.keys()))
            csv_writer.writeheader()
        csv_writer.writerow(row)
        csv_file.flush()

    print(
        f"[Targets] Scraping {len(targets)} URL(s) from {targets_csv_path}",
        flush=True,
    )

    with tqdm(
        total=len(targets),
        desc="[Targets] Scraping",
        unit="url",
        dynamic_ncols=True,
    ) as pbar:
        for idx, url in enumerate(targets, start=1):
            if _should_skip(url):
                pbar.write(f"[Targets] Skipping {url} (use --source backmarket)")
                pbar.update(1)
                continue

            try:
                html = get_html(session, url, timeout_seconds=timeout_seconds, retries=2)
                soup = parse_html(html)
                page_rows = extract_rows_from_page(soup, url, scrape_started_at)
                for r in page_rows:
                    _stream_row(r)
                site = _source_site(url)[:22]
                pbar.set_postfix(rows=len(page_rows), site=site, refresh=False)

            except Exception as exc:
                tqdm.write(f"[Targets] {url}: {exc}")
                fetch_errors += 1
                if emit_run_report:
                    issues.append(
                        fetch_issue(
                            reason=REASON_FETCH_ERROR,
                            source_site=_source_site(url),
                            source_url=url,
                            page_index=idx,
                            detail=str(exc),
                        )
                    )

            pbar.update(1)
            if idx < len(targets):
                polite_sleep(0.5, 1.2)

    jsonl_file.close()
    csv_file.close()

    all_rows = dedupe_dict_rows(
        all_rows, ("source_url", "item_name", "price_raw", "data_type")
    )
    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    json_path.write_text(
        json.dumps(
            {
                "source_site": "targets",
                "scrape_started_at": scrape_started_at,
                "row_count": len(all_rows),
                "rows": all_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    by_type: dict[str, int] = {}
    for r in all_rows:
        by_type[r.get("data_type", "unknown")] = by_type.get(r.get("data_type", "unknown"), 0) + 1

    type_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
    print(
        f"[Targets] Done: {len(all_rows)} rows total — {type_summary}, "
        f"errors={fetch_errors}",
        flush=True,
    )
    print(f"[Targets] JSON : {json_path}", flush=True)
    print(f"[Targets] JSONL: {jsonl_path}", flush=True)
    print(f"[Targets] CSV  : {csv_path}", flush=True)

    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site="targets",
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(all_rows),
            apply_cleaning=False,
            clean_report=None,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload), flush=True)
        if payload.paths:
            print(f"[Targets] Run   : {payload.paths.run_json}", flush=True)
            print(f"[Targets] Issues: {payload.paths.issues_jsonl}", flush=True)

    return all_rows


if __name__ == "__main__":
    scrape()
