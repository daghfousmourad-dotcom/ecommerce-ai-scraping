"""
Applied after each source scrape (before final JSON bundle). Use ``--no-clean`` on the CLI
to skip this step (raw rows as collected, still source-deduped where the scraper does it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DataType = Literal["repair_price", "part_cost", "product_sale"]


@dataclass(frozen=True)
class CleaningConfig:
    """Tunable thresholds; defaults are safe for phone repair / parts in EUR."""

    price_min: float = 0.01
    price_max: float = 50_000.0
    dedupe_keys_repair_price: tuple[str, ...] = (
        "source_url",
        "repair_type_raw",
        "price_raw",
    )
    dedupe_keys_part_cost: tuple[str, ...] = (
        "source_url",
        "product_name",
        "price_raw",
    )
    dedupe_keys_product_sale: tuple[str, ...] = (
        "source_url",
        "product_id",
        "price_raw",
    )
    # Cap duplicate rows written to issues JSONL (full count remains in CleaningReport).
    max_duplicate_issues: int = 2000


@dataclass(frozen=True)
class CleaningReport:
    """Counts for logging and QA."""

    data_type: str
    input_rows: int
    dropped_missing: int
    dropped_outliers: int
    removed_duplicate_rows: int
    output_rows: int

    def summary_line(self) -> str:
        parts = [
            f"{self.input_rows} → {self.output_rows} rows",
            f"missing −{self.dropped_missing}",
            f"outliers −{self.dropped_outliers}",
            f"dupes −{self.removed_duplicate_rows}",
        ]
        return "[Data clean] " + self.data_type + ": " + ", ".join(parts)


def _price_value(row: dict[str, Any], data_type: DataType) -> float | None:
    key = "part_cost" if data_type == "part_cost" else "price"
    raw = row.get(key)
    if raw is None and data_type == "part_cost":
        raw = row.get("price")
    elif raw is None and data_type == "repair_price":
        raw = row.get("part_cost")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _source_url_ok(row: dict[str, Any]) -> bool:
    u = row.get("source_url")
    return isinstance(u, str) and bool(u.strip())


def _cleaning_row_snapshot(row: dict[str, Any], data_type: DataType) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "source_site": row.get("source_site"),
        "source_url": row.get("source_url"),
        "brand": row.get("brand"),
        "model": row.get("model"),
    }
    if data_type == "repair_price":
        snap["repair_type_raw"] = row.get("repair_type_raw")
    elif data_type == "product_sale":
        snap["product_id"] = row.get("product_id")
        snap["item_title"] = row.get("item_title")
    else:
        snap["product_name"] = row.get("product_or_part_name") or row.get("product_name")
    return snap


def _append_cleaning_issue(
    issues: list[dict[str, Any]],
    *,
    reason: str,
    data_type: DataType,
    row: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "phase": "cleaning",
        "reason": reason,
        "data_type": data_type,
        **_cleaning_row_snapshot(row, data_type),
    }
    if extra:
        payload.update(extra)
    issues.append(payload)


def _row_val_for_dedupe(row: dict[str, Any], k: str) -> Any:
    val = row.get(k)
    if val is not None:
        return val
    if k == "product_name":
        return row.get("product_or_part_name")
    if k == "product_or_part_name":
        return row.get("product_name")
    if k == "part_cost":
        return row.get("price")
    if k == "price":
        return row.get("part_cost")
    return None


def remove_duplicates(
    rows: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
    data_type: DataType,
    cleaning_issues: list[dict[str, Any]] | None = None,
    max_duplicate_issues: int = 2000,
) -> tuple[list[dict[str, Any]], int]:
    """
    Collapse rows that share the same fingerprint on ``keys``.
    Returns ``(deduped_rows, number_of_rows_removed)``.
    When ``cleaning_issues`` is set, appends up to ``max_duplicate_issues`` duplicate samples.
    """
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    removed = 0
    dup_issue_count = 0
    for row in rows:
        fingerprint = tuple(_row_val_for_dedupe(row, k) for k in keys)
        if fingerprint in seen:
            removed += 1
            if (
                cleaning_issues is not None
                and dup_issue_count < max_duplicate_issues
            ):
                fp_dict = {k: _row_val_for_dedupe(row, k) for k in keys}
                _append_cleaning_issue(
                    cleaning_issues,
                    reason="duplicate_row",
                    data_type=data_type,
                    row=row,
                    extra={"fingerprint": fp_dict},
                )
                dup_issue_count += 1
            continue
        seen.add(fingerprint)
        result.append(row)
    return result, removed


def remove_outliers(
    rows: list[dict[str, Any]],
    *,
    data_type: DataType,
    price_min: float,
    price_max: float,
    cleaning_issues: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Drop rows whose numeric price is outside ``[price_min, price_max]``."""
    out: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        p = _price_value(row, data_type)
        if p is None:
            dropped += 1
            if cleaning_issues is not None:
                _append_cleaning_issue(
                    cleaning_issues,
                    reason="invalid_price",
                    data_type=data_type,
                    row=row,
                    extra={"detail": "non_numeric_price_after_missing_pass"},
                )
            continue
        if p < price_min or p > price_max:
            dropped += 1
            if cleaning_issues is not None:
                _append_cleaning_issue(
                    cleaning_issues,
                    reason="price_out_of_range",
                    data_type=data_type,
                    row=row,
                    extra={
                        "price": p,
                        "price_min": price_min,
                        "price_max": price_max,
                    },
                )
            continue
        out.append(row)
    return out, dropped


def handle_missing(
    rows: list[dict[str, Any]],
    *,
    data_type: DataType,
    cleaning_issues: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Drop rows that cannot be used for analysis (no URL or no usable price)."""
    out: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        if not _source_url_ok(row):
            dropped += 1
            if cleaning_issues is not None:
                _append_cleaning_issue(
                    cleaning_issues,
                    reason="missing_source_url",
                    data_type=data_type,
                    row=row,
                )
            continue
        p = _price_value(row, data_type)
        if p is None:
            dropped += 1
            if cleaning_issues is not None:
                _append_cleaning_issue(
                    cleaning_issues,
                    reason="invalid_price",
                    data_type=data_type,
                    row=row,
                    extra={"detail": "missing_or_unparseable_price"},
                )
            continue
        out.append(row)
    return out, dropped


def clean_dataset(
    rows: list[dict[str, Any]],
    *,
    data_type: DataType,
    config: CleaningConfig | None = None,
    cleaning_issues: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], CleaningReport]:
    """
    Run handle_missing → remove_outliers → remove_duplicates.

    Duplicate keys match each source’s export fingerprint (WeFix / Save / Utopya).

    If ``cleaning_issues`` is a list, each dropped/skipped row is appended with a
    ``reason`` (up to ``config.max_duplicate_issues`` samples for duplicates).
    """
    cfg = config or CleaningConfig()
    n0 = len(rows)

    step1, drop_m = handle_missing(
        rows, data_type=data_type, cleaning_issues=cleaning_issues
    )
    step2, drop_o = remove_outliers(
        step1,
        data_type=data_type,
        price_min=cfg.price_min,
        price_max=cfg.price_max,
        cleaning_issues=cleaning_issues,
    )
    if data_type == "repair_price":
        keys = cfg.dedupe_keys_repair_price
    elif data_type == "product_sale":
        keys = cfg.dedupe_keys_product_sale
    else:
        keys = cfg.dedupe_keys_part_cost
    step3, drop_d = remove_duplicates(
        step2,
        keys=keys,
        data_type=data_type,
        cleaning_issues=cleaning_issues,
        max_duplicate_issues=cfg.max_duplicate_issues,
    )

    report = CleaningReport(
        data_type=data_type,
        input_rows=n0,
        dropped_missing=drop_m,
        dropped_outliers=drop_o,
        removed_duplicate_rows=drop_d,
        output_rows=len(step3),
    )
    return step3, report
