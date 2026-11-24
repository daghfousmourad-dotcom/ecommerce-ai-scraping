"""Shared parsing utilities for source scrapers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_price_and_currency(raw_price: str) -> tuple[float | None, str | None]:
    text = clean_text(raw_price)
    currency = "EUR" if "€" in text else None
    match = re.search(r"(\d[\d\s.,]*)", text)
    if not match:
        return None, currency
    number = match.group(1).replace(" ", "").replace(",", ".")
    try:
        return float(number), currency
    except ValueError:
        return None, currency


def to_slug(value: str) -> str:
    value = clean_text(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def dedupe_dict_rows(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        fingerprint = tuple(
            row.get(k)
            if row.get(k) is not None
            else (
                row.get("product_or_part_name")
                if k == "product_name"
                else (row.get("product_name") if k == "product_or_part_name" else None)
            )
            for k in keys
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)
    return result

