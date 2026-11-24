# Scraper : XIAOMI

## 📋 Présentation
Xiaomi France Official Spare Parts Price Scraper (sources2).

Extrait les tarifs officiels de pièces détachées et réparations constructeur Xiaomi France :
  Portail officiel: https://www.mi.com/fr/support/spare-parts-price/
  API Catalogue: https://go.buy.mi.com/fr/v2/item/category/nav?from=tablet
  API Modèles: https://go.buy.mi.com/fr/search/product-list
  API Info Pièces: https://ams-go.buy.mi.com/fr/misc/getgoodsinformation
  API Tarifs Officiels: https://ams-api.buy.mi.com/fr/crm/api/XGetPrice (languageId=1036, country=fr)

- **Nom de la source** : `xiaomi`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://www.mi.com/fr/support/spare-parts-price/
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/xiaomi
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/xiaomi/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_xiaomi.csv` : Fichier tabulaire complet
2. `db_xiaomi.json` : Export JSON structuré
3. `db_xiaomi.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_xiaomi_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_xiaomi_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
XIAOMI_API_KEY=
XIAOMI_PROXY=
```
