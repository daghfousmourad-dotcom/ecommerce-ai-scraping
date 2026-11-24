from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ebay_api import EbayAPIError, EbayClient
from .normalize import normalize_item


# eBay documente 10 000 annonces comme maximum récupérable dans une recherche.
MAX_SEARCH_RESULTS = 10_000

# Neuf / neuf autre + quatre états reconditionnés du programme eBay
CONDITION_PARTITIONS = [
    ("NEW", ("1000", "1500")),
    ("REFURBISHED", ("2000", "2010", "2020", "2030", "2500")),
]


@dataclass(frozen=True)
class Partition:
    label: str
    condition_ids: tuple[str, ...]
    price_min: float | None = None
    price_max: float | None = None


class Collector:
    def __init__(self, client: EbayClient, config, storage, logger) -> None:
        self.client = client
        self.config = config
        self.storage = storage
        self.logger = logger

        self.seen_ids: set[str] = set()
        self.pages = 0
        self.items_seen = 0
        self.items_saved = 0
        self.errors = 0

    def _search_count(self, partition: Partition) -> int:
        data = self.client.search(
            query=self.config.query,
            category_id=self.config.category_id,
            offset=0,
            limit=1,
            condition_ids=partition.condition_ids,
            price_min=partition.price_min,
            price_max=partition.price_max,
        )
        return int(data.get("total", 0))

    def _split_price(self, partition: Partition, total: int) -> list[Partition]:
        if partition.price_min is None:
            low = 0.0
        else:
            low = partition.price_min

        if partition.price_max is None:
            # Plafond large mais réaliste pour une première subdivision.
            high = 100_000.0
        else:
            high = partition.price_max

        if high <= low:
            return []

        mid = round((low + high) / 2, 2)
        if mid <= low or mid >= high:
            return []

        return [
            Partition(partition.label + f":{low}-{mid}", partition.condition_ids, low, mid),
            Partition(partition.label + f":{mid}-{high}", partition.condition_ids, mid, high),
        ]

    def _collect_partition(self, partition: Partition) -> None:
        total = self._search_count(partition)
        self.logger.info(
            "Partition %s: total=%s, prix=%s..%s",
            partition.label,
            total,
            partition.price_min,
            partition.price_max,
        )

        if total > MAX_SEARCH_RESULTS:
            children = self._split_price(partition, total)
            if not children:
                raise RuntimeError(
                    f"Partition {partition.label} > {MAX_SEARCH_RESULTS} résultats "
                    "et impossible à subdiviser automatiquement. "
                    "Réduis la requête ou ajoute une partition métier."
                )

            for child in children:
                self._collect_partition(child)
            return

        offset = 0
        while offset < total:
            if self.pages >= self.config.max_pages:
                self.logger.info(
                    "Limite de pages atteinte (%s/%s). Arrêt de la collecte.",
                    self.pages,
                    self.config.max_pages,
                )
                return
            if self.config.max_items and self.items_saved >= self.config.max_items:
                return

            data = self.client.search(
                query=self.config.query,
                category_id=self.config.category_id,
                offset=offset,
                limit=self.config.page_size,
                condition_ids=partition.condition_ids,
                price_min=partition.price_min,
                price_max=partition.price_max,
            )

            items = data.get("itemSummaries", []) or []
            self.pages += 1
            self.items_seen += len(items)

            if not items:
                self.logger.warning(
                    "Page vide pour %s à offset=%s ; arrêt de cette partition.",
                    partition.label,
                    offset,
                )
                break

            for summary in items:
                item_id = summary.get("itemId")
                if not item_id or item_id in self.seen_ids:
                    continue

                self.seen_ids.add(item_id)

                try:
                    item = (
                        self.client.get_item(item_id)
                        if self.config.detail_every_item
                        else summary
                    )
                    normalized = normalize_item(item)
                    if normalized is None:
                        continue

                    self.storage.upsert(normalized)
                    self.items_saved += 1

                except (EbayAPIError, KeyError, ValueError, TypeError) as exc:
                    self.errors += 1
                    self.logger.exception(
                        "Erreur item %s: %s", item_id, exc
                    )

                if self.config.max_items and self.items_saved >= self.config.max_items:
                    return

            offset += self.config.page_size

            self.logger.info(
                "Partition=%s offset=%s/%s | pages=%s | saved=%s | errors=%s",
                partition.label,
                offset,
                total,
                self.pages,
                self.items_saved,
                self.errors,
            )

            # Sécurité si l'API renvoie moins que la taille demandée.
            if len(items) < self.config.page_size:
                break

    def run(self) -> None:
        for label, condition_ids in CONDITION_PARTITIONS:
            self._collect_partition(
                Partition(label=label, condition_ids=condition_ids)
            )

        self.storage.export_csv()

        self.logger.info(
            "Terminé: pages=%s, summaries=%s, sauvegardés=%s, erreurs=%s, uniques=%s",
            self.pages,
            self.items_seen,
            self.items_saved,
            self.errors,
            self.storage.count(),
        )
