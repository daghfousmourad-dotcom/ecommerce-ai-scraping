# Scraper : EASY_CASH

## 📋 Présentation
scraper_easy_cash.py
====================
Scraper haute performance et résilient pour Easy Cash (Smartphones Reconditionnés & Occasion Garantie).
Cible : Enseigne N°13 (Priorité 1) de la cartographie E-commerce AI Scraping.
Réseau national leader de l'achat-vente d'occasion avec garantie 2 ans et desserte région Marseille.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Catalogue complet Téléphonie / Smartphones :
    https://bons-plans.easycash.fr/telephonie/smartphones?filterType=searchResults

Génère 5 fichiers d'artefacts standardisés dans ./outputs/ :
  1. db_easy_cash.json
  2. db_easy_cash.jsonl
  3. db_easy_cash.csv
  4. db_easy_cash_run.json
  5. db_easy_cash_issues.jsonl

- **Nom de la source** : `easy_cash`
- **Domaine de données** : `product_sales`
- **URL cible** : https://bons-plans.easycash.fr/telephonie/smartphones?filterType=searchResults
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/easy_cash
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/easy_cash/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_easy_cash.csv` : Fichier tabulaire complet
2. `db_easy_cash.json` : Export JSON structuré
3. `db_easy_cash.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_easy_cash_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_easy_cash_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
EASY_CASH_API_KEY=
EASY_CASH_PROXY=
```
