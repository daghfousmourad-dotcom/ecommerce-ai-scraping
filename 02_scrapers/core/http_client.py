"""Shared HTTP client helpers."""

from __future__ import annotations

import random
import time
from typing import Any, Optional

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}


def create_session(headers: Optional[dict[str, str]] = None) -> requests.Session:
    session = requests.Session()
    final_headers = dict(DEFAULT_HEADERS)
    if headers:
        final_headers.update(headers)
    session.headers.update(final_headers)
    return session


def polite_sleep(min_seconds: float = 0.7, max_seconds: float = 1.8) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout_seconds: int = 30,
    retries: int = 3,
    backoff_seconds: float = 1.2,
    **kwargs: Any,
) -> requests.Response:
    error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.request(method=method, url=url, timeout=timeout_seconds, **kwargs)
            if is_retryable_status(response.status_code):
                if attempt == retries:
                    response.raise_for_status()
                polite_sleep(backoff_seconds * attempt, backoff_seconds * attempt + 0.8)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt == retries:
                break
            polite_sleep(backoff_seconds * attempt, backoff_seconds * attempt + 0.8)
    if error:
        raise error
    raise RuntimeError("Request failed unexpectedly")


def get_html(
    session: requests.Session,
    url: str,
    *,
    timeout_seconds: int = 30,
    retries: int = 3,
) -> str:
    response = request_with_retry(
        session,
        "GET",
        url,
        timeout_seconds=timeout_seconds,
        retries=retries,
    )
    return response.text

