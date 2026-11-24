from __future__ import annotations

import base64
import time
from typing import Any, Iterable

import requests


TOKEN_PATH = "/identity/v1/oauth2/token"
SEARCH_PATH = "/buy/browse/v1/item_summary/search"
ITEM_PATH = "/buy/browse/v1/item/{item_id}"


class EbayAPIError(RuntimeError):
    pass


class EbayClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_base: str,
        marketplace_id: str,
        accept_language: str,
        timeout: int,
        retry_attempts: int,
        request_delay: float,
        logger,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = api_base
        self.marketplace_id = marketplace_id
        self.accept_language = accept_language
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.request_delay = request_delay
        self.logger = logger
        self.session = requests.Session()
        self.token: str | None = None
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: Any = None,
        auth: Any = None,
    ) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                self._throttle()
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    data=data,
                    auth=auth,
                    timeout=self.timeout,
                )
                self._last_request = time.monotonic()

                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(2 ** (attempt - 1), 30)
                    self.logger.warning(
                        "HTTP %s sur %s ; retry %s/%s dans %.1fs",
                        response.status_code,
                        url,
                        attempt,
                        self.retry_attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                return response

            except requests.RequestException as exc:
                last_error = exc
                delay = min(2 ** (attempt - 1), 30)
                self.logger.warning(
                    "Erreur réseau ; retry %s/%s dans %.1fs: %s",
                    attempt,
                    self.retry_attempts,
                    delay,
                    exc,
                )
                time.sleep(delay)

        raise EbayAPIError(f"Échec après {self.retry_attempts} tentatives: {last_error}")

    def authenticate(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        basic = base64.b64encode(raw).decode()

        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }

        response = self._request(
            "POST",
            f"{self.api_base}{TOKEN_PATH}",
            headers=headers,
            data=data,
        )

        if response.status_code != 200:
            raise EbayAPIError(
                f"OAuth eBay impossible ({response.status_code}): {response.text[:1000]}"
            )

        payload = response.json()
        self.token = payload["access_token"]
        return self.token

    def _api_headers(self) -> dict[str, str]:
        if not self.token:
            self.authenticate()

        return {
            "Authorization": f"Bearer {self.token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Accept-Language": self.accept_language,
            "Content-Type": "application/json",
        }

    def search(
        self,
        *,
        query: str,
        category_id: str,
        offset: int,
        limit: int,
        condition_ids: Iterable[str] | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        fieldgroups: str | None = None,
    ) -> dict[str, Any]:
        filters = [f"buyingOptions:{{FIXED_PRICE|AUCTION}}"]

        if condition_ids:
            ids = "|".join(str(x) for x in condition_ids)
            filters.append(f"conditionIds:{{{ids}}}")

        if price_min is not None or price_max is not None:
            lo = "" if price_min is None else str(price_min)
            hi = "" if price_max is None else str(price_max)
            filters.append(f"price:[{lo}..{hi}]")
            filters.append("priceCurrency:EUR")

        params: dict[str, Any] = {
            "q": query,
            "category_ids": category_id,
            "limit": limit,
            "offset": offset,
            "filter": ",".join(filters),
        }
        if fieldgroups:
            params["fieldgroups"] = fieldgroups

        response = self._request(
            "GET",
            f"{self.api_base}{SEARCH_PATH}",
            headers=self._api_headers(),
            params=params,
        )

        if response.status_code == 401:
            self.authenticate()
            response = self._request(
                "GET",
                f"{self.api_base}{SEARCH_PATH}",
                headers=self._api_headers(),
                params=params,
            )

        if response.status_code != 200:
            raise EbayAPIError(
                f"Browse search {response.status_code}: {response.text[:1500]}"
            )

        return response.json()

    def get_item(self, item_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"{self.api_base}{ITEM_PATH.format(item_id=item_id)}",
            headers=self._api_headers(),
        )

        if response.status_code == 401:
            self.authenticate()
            response = self._request(
                "GET",
                f"{self.api_base}{ITEM_PATH.format(item_id=item_id)}",
                headers=self._api_headers(),
            )

        if response.status_code != 200:
            raise EbayAPIError(
                f"Browse getItem {response.status_code}: {response.text[:1500]}"
            )

        return response.json()
