# Scraper : CDISCOUNT

## 📋 Présentation
scraper_cdiscount.py
====================
Scraper haute performance pour Cdiscount (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°25 (Priorité 1) de la cartographie E-commerce AI Scraping.
Leader français du e-commerce avec livraison express 24-48h sur Marseille.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Catalogue complet Téléphonie / Smartphones :
    https://www.cdiscount.com/telephonie/telephone-mobile/l-14404.html (Pages 1 à N)
  - Différenciation stricte Neuf vs Reconditionné conformément au cahier des charges

Génère 5 fichiers d'artefacts standardisés dans ./outputs/ :
  1. db_cdiscount.json
  2. db_cdiscount.jsonl
  3. db_cdiscount.csv
  4. db_cdiscount_run.json
  5. db_cdiscount_issues.jsonl

- **Nom de la source** : `cdiscount`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.cdiscount.com/telephonie/telephone-mobile/l-14404.html
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/cdiscount
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/cdiscount/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_cdiscount.csv` : Fichier tabulaire complet
2. `db_cdiscount.json` : Export JSON structuré
3. `db_cdiscount.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_cdiscount_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_cdiscount_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
CDISCOUNT_API_KEY=
CDISCOUNT_PROXY=
```
