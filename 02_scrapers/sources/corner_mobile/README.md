# Scraper : CORNER_MOBILE

## 📋 Présentation
Corner Mobile scraper: discover model URLs via HTML sitemap & scrape repair prices.

Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe : Aucune API JSON exposée par le serveur (site statique HTML).
  2. Content/HTML Scraping : Parsing HTML direct ultra-rapide (BeautifulSoup + ThreadPoolExecutor).

- **Nom de la source** : `corner_mobile`
- **Domaine de données** : `product_sales`
- **URL cible** : https://cornermobile.fr
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/corner_mobile
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/corner_mobile/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_corner_mobile.csv` : Fichier tabulaire complet
2. `db_corner_mobile.json` : Export JSON structuré
3. `db_corner_mobile.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_corner_mobile_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_corner_mobile_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
CORNER_MOBILE_API_KEY=
CORNER_MOBILE_PROXY=
```
