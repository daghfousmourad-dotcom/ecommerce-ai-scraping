# Scraper : DARTY

## 📋 Présentation
Darty Hybrid Scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main basé sur la version darty_hybrid_scraper.py & darty_uc_scraper.py.

- **Nom de la source** : `darty`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.darty.com/nav/achat/telephonie/telephone_mobile/mobile/index.html#dartyclic=X_tele-obje-conn_smar
- **Technologie principale** : `Selenium Undetected-Chromedriver`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/darty
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/darty/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_darty.csv` : Fichier tabulaire complet
2. `db_darty.json` : Export JSON structuré
3. `db_darty.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_darty_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_darty_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
DARTY_API_KEY=
DARTY_PROXY=
```
