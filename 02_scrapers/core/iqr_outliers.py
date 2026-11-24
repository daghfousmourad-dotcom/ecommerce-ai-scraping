"""
iqr_outliers.py
===============
Microservice de détection et de filtrage des valeurs aberrantes par Écart Interquartile (IQR).
Ce module fournit une fonction statistique générique réutilisable pour tous les scrapers.

Méthode mathématique :
  - Q1 : 25ème percentile (médiane de la moitié inférieure)
  - Q3 : 75ème percentile (médiane de la moitié supérieure)
  - IQR = Q3 - Q1
  - Bornes de Tukey :
      Borne inférieure = Q1 - (factor * IQR)
      Borne supérieure = Q3 + (factor * IQR)
      (Par défaut factor = 1.5 pour les outliers standards, 3.0 pour les outliers extrêmes)
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Sequence


REASON_IQR_OUTLIER = "iqr_outlier_price"


@dataclass(frozen=True)
class IQRStats:
    """Statistiques calculées pour une cohorte de données."""
    count: int
    q1: float
    median: float
    q3: float
    iqr: float
    lower_bound: float
    upper_bound: float


def compute_quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    """
    Calcule Q1 (25%), Médiane (50%) et Q3 (75%) selon la méthode standard (interpolation linéaire).
    Ne dépend d'aucune librairie tierce pour une portabilité maximale.
    """
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 0:
        return 0.0, 0.0, 0.0
    if n == 1:
        return sorted_vals[0], sorted_vals[0], sorted_vals[0]

    def _percentile(p: float) -> float:
        # p in [0, 1]
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        d0 = sorted_vals[int(f)] * (c - k)
        d1 = sorted_vals[int(c)] * (k - f)
        return d0 + d1

    return _percentile(0.25), _percentile(0.50), _percentile(0.75)


def calculate_iqr_bounds(
    values: Sequence[float],
    factor: float = 1.5,
    min_lower_bound: float = 0.01,
) -> IQRStats | None:
    """
    Calcule les bornes d'exclusion IQR pour une liste de nombres.
    Retourne None si l'échantillon est insuffisant (< 4 valeurs).
    """
    if len(values) < 4:
        return None

    q1, median, q3 = compute_quartiles(values)
    iqr = q3 - q1
    lower_bound = max(min_lower_bound, q1 - factor * iqr)
    upper_bound = q3 + factor * iqr

    return IQRStats(
        count=len(values),
        q1=round(q1, 2),
        median=round(median, 2),
        q3=round(q3, 2),
        iqr=round(iqr, 2),
        lower_bound=round(lower_bound, 2),
        upper_bound=round(upper_bound, 2),
    )


def filter_by_iqr(
    rows: list[dict[str, Any]],
    price_key: str = "price",
    group_by_keys: tuple[str, ...] = ("brand", "model"),
    factor: float = 1.5,
    min_samples: int = 4,
    source_site: str = "unknown",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, IQRStats]]:
    """
    Filtre une liste d'enregistrements en éliminant les valeurs aberrantes via la méthode IQR.
    
    Paramètres :
      - rows : liste des dictionnaires représentant chaque ligne collectée.
      - price_key : clé du prix numérique dans le dictionnaire (ex: 'price', 'price_eur', 'part_cost').
      - group_by_keys : clés utilisées pour créer des sous-groupes de comparaison homogènes
                        (ex: par modèle ('brand', 'model') ou modèle + capacité ('brand', 'model', 'storage')).
      - factor : multiplicateur de l'IQR (défaut 1.5 pour la détection classique de Tukey).
      - min_samples : nombre minimal de prix dans un sous-groupe pour appliquer l'IQR.
                      En dessous de ce seuil, les prix ne sont pas tronqués par IQR pour éviter les faux positifs.
      - source_site : nom de la source pour les métadonnées de rejets.

    Retourne un tuple :
      (kept_rows, dropped_issues, cohort_stats)
    """
    # 1. Regrouper les indices et valeurs par cohorte homogène
    groups: dict[tuple[Any, ...], list[tuple[int, float]]] = defaultdict(list)
    valid_rows: list[dict[str, Any]] = []
    dropped_issues: list[dict[str, Any]] = []
    cohort_stats: dict[str, IQRStats] = {}

    for idx, row in enumerate(rows):
        val = row.get(price_key)
        if val is None:
            continue
        try:
            num_val = float(val)
        except (ValueError, TypeError):
            continue

        group_key = tuple(str(row.get(k) or "").strip().lower() for k in group_by_keys)
        groups[group_key].append((idx, num_val))

    # 2. Calculer les bornes IQR pour chaque cohorte et identifier les outliers
    outlier_indices: set[int] = set()

    for group_key, items in groups.items():
        group_label = " / ".join(str(k) for k in group_key if k) or "global"
        prices = [p for _, p in items]
        stats = calculate_iqr_bounds(prices, factor=factor)

        if stats is not None:
            cohort_stats[group_label] = stats
            for idx, price in items:
                if price < stats.lower_bound or price > stats.upper_bound:
                    outlier_indices.add(idx)
                    row = rows[idx]
                    dropped_issues.append({
                        "phase": "cleaning",
                        "reason": REASON_IQR_OUTLIER,
                        "source_site": row.get("source_site") or source_site,
                        "source_url": row.get("source_url") or "",
                        "price": price,
                        "cohort": group_label,
                        "cohort_count": stats.count,
                        "q1": stats.q1,
                        "q3": stats.q3,
                        "iqr": stats.iqr,
                        "lower_bound": stats.lower_bound,
                        "upper_bound": stats.upper_bound,
                        "model": row.get("model") or row.get("title") or "",
                        "detail": (
                            f"Prix {price:.2f} € hors bornes IQR [{stats.lower_bound:.2f} €, "
                            f"{stats.upper_bound:.2f} €] pour la cohorte '{group_label}'"
                        ),
                    })

    # 3. Séparer les lignes conservées
    kept_rows = [row for idx, row in enumerate(rows) if idx not in outlier_indices]

    return kept_rows, dropped_issues, cohort_stats
