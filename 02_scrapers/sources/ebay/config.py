from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    api_base: str
    marketplace_id: str
    accept_language: str
    query: str
    category_id: str
    page_size: int
    timeout: int
    retry_attempts: int
    request_delay: float
    max_pages: int
    max_items: int
    detail_every_item: bool
    output_dir: Path
    log_dir: Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config() -> Config:
    client_id = os.getenv("EBAY_CLIENT_ID", "MouradDa-Mourad-PRD-6a217ea81-ab7e9006").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "PRD-a217ea8122d4-fd25-405b-b070-322b").strip()


    output_dir = Path(os.getenv("OUTPUT_DIR", "outputs"))
    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        client_id=client_id,
        client_secret=client_secret,
        api_base=os.getenv("EBAY_API_BASE", "https://api.ebay.com").rstrip("/"),
        marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_FR"),
        accept_language=os.getenv("EBAY_ACCEPT_LANGUAGE", "fr-FR"),
        query=os.getenv("EBAY_QUERY", "smartphone"),
        category_id=os.getenv("EBAY_CATEGORY_ID", "9355"),
        page_size=max(1, min(int(os.getenv("EBAY_PAGE_SIZE", "200")), 200)),
        timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
        retry_attempts=max(1, int(os.getenv("RETRY_ATTEMPTS", "5"))),
        request_delay=max(0.0, float(os.getenv("REQUEST_DELAY_SECONDS", "0.15"))),
        max_pages=max(1, int(os.getenv("MAX_PAGES", "100000"))),
        max_items=max(0, int(os.getenv("MAX_ITEMS", "0"))),
        detail_every_item=_bool("DETAIL_EVERY_ITEM", True),
        output_dir=output_dir,
        log_dir=log_dir,
    )
