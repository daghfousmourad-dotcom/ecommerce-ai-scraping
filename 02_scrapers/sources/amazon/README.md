# Scraper : AMAZON

## 📋 Présentation
Amazon Marketplace scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main basé sur amazon_scraper_unified.py.
Collecte multi-marques avec élimination IQR des valeurs aberrantes.

- **Nom de la source** : `amazon`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.amazon.fr
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/amazon
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/amazon/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_amazon.csv` : Fichier tabulaire complet
2. `db_amazon.json` : Export JSON structuré
3. `db_amazon.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_amazon_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_amazon_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
AMAZON_API_KEY=
AMAZON_PROXY=
```
