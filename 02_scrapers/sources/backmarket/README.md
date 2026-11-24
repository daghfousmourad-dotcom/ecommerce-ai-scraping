# Scraper : BACKMARKET

## 📋 Présentation
Back Market scraper — refurbished smartphones, all brands.

Two scraping modes:

  1. API mode (--bm-api-token TOKEN)
     Uses the official Back Market Merchant REST API.

  2. HTTP mode (default)
     Fetches listing pages with requests and parses __NEXT_DATA__,
     falling back to plain HTML card parsing.

Diagnostic version:
  - Detailed HTTP 403 diagnostics
  - Saves blocked response body to disk
  - Records HTTP errors in the run issues

- **Nom de la source** : `backmarket`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.backmarket.fr
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/backmarket
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/backmarket/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_backmarket.csv` : Fichier tabulaire complet
2. `db_backmarket.json` : Export JSON structuré
3. `db_backmarket.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_backmarket_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_backmarket_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
BACKMARKET_API_KEY=
BACKMARKET_PROXY=
```
