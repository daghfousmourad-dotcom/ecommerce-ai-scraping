"""
Each scrape can emit:
- ``<stem>_run.json`` — timing, counts, output paths, optional cleaning stats
- ``<stem>_issues.jsonl`` — one JSON object per line (fetch + cleaning phases)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from scraper.core.filtering import CleaningReport
from scraper.core.parser_utils import now_iso_utc

Phase = Literal["fetch", "cleaning"]

# Issue reason codes (stable strings for dashboards / filters)
REASON_FETCH_ERROR = "fetch_error"
REASON_NO_REPAIR_CATEGORIES = "no_repair_categories"
REASON_MISSING_SOURCE_URL = "missing_source_url"
REASON_INVALID_PRICE = "invalid_price"
REASON_PRICE_OUT_OF_RANGE = "price_out_of_range"
REASON_DUPLICATE_ROW = "duplicate_row"

SCHEMA_VERSION = 1


@dataclass
class RunReportPaths:
    json_bundle: str
    jsonl: str
    csv: str
    run_json: str
    issues_jsonl: str


@dataclass
class RunReportPayload:
    """Serializable run summary (written to ``*_run.json``)."""

    source_site: str
    scrape_started_at: str
    scrape_finished_at: str
    schema_version: int = SCHEMA_VERSION
    status: Literal["completed", "failed"] = "completed"
    apply_cleaning: bool = True
    row_count: int = 0
    paths: RunReportPaths | None = None
    cleaning: dict[str, Any] | None = None
    counts: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.paths is not None:
            d["paths"] = asdict(self.paths)
        return d


def write_issues_jsonl(path: Path, issues: list[dict[str, Any]]) -> None:
    """Write one JSON object per line (UTF-8). Empty list → empty file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not issues:
        path.write_text("", encoding="utf-8")
        return
    path.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in issues) + "\n",
        encoding="utf-8",
    )


def write_run_json(path: Path, payload: RunReportPayload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload.to_json_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cleaning_report_to_dict(report: CleaningReport) -> dict[str, Any]:
    return {
        "data_type": report.data_type,
        "input_rows": report.input_rows,
        "dropped_missing": report.dropped_missing,
        "dropped_outliers": report.dropped_outliers,
        "removed_duplicate_rows": report.removed_duplicate_rows,
        "output_rows": report.output_rows,
    }


def summarize_run_line(payload: RunReportPayload) -> str:
    """One-line console summary for operators."""
    c = payload.counts
    parts = [
        f"rows={payload.row_count}",
    ]
    if "fetch_errors" in c:
        parts.append(f"fetch_errors={c['fetch_errors']}")
    if "issues_total" in c:
        parts.append(f"issues={c['issues_total']}")
    if payload.cleaning:
        cr = payload.cleaning
        parts.append(
            f"clean={cr['input_rows']}→{cr['output_rows']} "
            f"(−{cr['dropped_missing']} miss, −{cr['dropped_outliers']} out, −{cr['removed_duplicate_rows']} dup)"
        )
    return "[Run] " + payload.source_site + ": " + ", ".join(parts)


def build_paths(
    *,
    json_path: Path,
    jsonl_path: Path,
    csv_path: Path,
    run_json_path: Path,
    issues_jsonl_path: Path,
) -> RunReportPaths:
    return RunReportPaths(
        json_bundle=str(json_path),
        jsonl=str(jsonl_path),
        csv=str(csv_path),
        run_json=str(run_json_path),
        issues_jsonl=str(issues_jsonl_path),
    )


def cleaning_issue(
    *,
    reason: str,
    data_type: str,
    source_site: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """One cleaning-phase issue row for ``*_issues.jsonl``."""
    row: dict[str, Any] = {
        "phase": "cleaning",
        "reason": reason,
        "data_type": data_type,
    }
    if source_site is not None:
        row["source_site"] = source_site
    row.update(fields)
    return row


def fetch_issue(
    *,
    reason: str,
    source_site: str,
    **fields: Any,
) -> dict[str, Any]:
    """One fetch-phase issue (HTTP, parse, empty categories, etc.)."""
    return {"phase": "fetch", "reason": reason, "source_site": source_site, **fields}


def sidecar_paths_from_bundle(json_path: Path) -> tuple[Path, Path]:
    """``save_repair_prices_<ts>.json`` → ``*_run.json`` and ``*_issues.jsonl``."""
    stem = json_path.stem
    parent = json_path.parent
    return parent / f"{stem}_run.json", parent / f"{stem}_issues.jsonl"


def finalize_run_artifacts(
    *,
    source_site: str,
    scrape_started_at: str,
    json_path: Path,
    jsonl_path: Path,
    csv_path: Path,
    row_count: int,
    apply_cleaning: bool,
    clean_report: CleaningReport | None,
    fetch_errors: int,
    issues: list[dict[str, Any]],
    status: str = "completed",
) -> RunReportPayload:
    """Write ``*_run.json`` + ``*_issues.jsonl`` and return the payload (for testing / logs)."""
    scrape_finished_at = now_iso_utc()
    run_json_path, issues_jsonl_path = sidecar_paths_from_bundle(json_path)
    paths = build_paths(
        json_path=json_path,
        jsonl_path=jsonl_path,
        csv_path=csv_path,
        run_json_path=run_json_path,
        issues_jsonl_path=issues_jsonl_path,
    )
    cleaning_dict = cleaning_report_to_dict(clean_report) if clean_report else None
    payload = RunReportPayload(
        source_site=source_site,
        scrape_started_at=scrape_started_at,
        scrape_finished_at=scrape_finished_at,
        status=status,
        apply_cleaning=apply_cleaning,
        row_count=row_count,
        paths=paths,
        cleaning=cleaning_dict,
        counts={
            "fetch_errors": fetch_errors,
            "issues_total": len(issues),
        },
    )
    write_run_json(run_json_path, payload)
    write_issues_jsonl(issues_jsonl_path, issues)
    return payload
