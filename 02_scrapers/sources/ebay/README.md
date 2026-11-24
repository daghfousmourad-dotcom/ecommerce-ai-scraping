# Scraper : EBAY

## 📋 Présentation
eBay Browse API scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main.
Supporte à la fois la collecte temps réel via l'API officielle eBay (OAuth 2.0)
et le chargement sécurisé de la base certifiée de production.

- **Nom de la source** : `ebay`
- **Domaine de données** : `product_sales`
- **URL cible** : https://api.ebay.com
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/ebay
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/ebay/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_ebay.csv` : Fichier tabulaire complet
2. `db_ebay.json` : Export JSON structuré
3. `db_ebay.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_ebay_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_ebay_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
EBAY_API_KEY=
EBAY_PROXY=
```
