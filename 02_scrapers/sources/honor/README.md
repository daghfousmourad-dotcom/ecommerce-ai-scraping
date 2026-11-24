# Scraper : HONOR

## 📋 Présentation
HONOR France Scraper (https://www.honor.com/fr/support/sparepart-price/).

Tarifs officiels des pièces détachées et réparations constructeur HONOR France :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe (REST/JSON Gateway Cloud CCPC) :
     - Liste des modèles (lv4) : GET https://selfservice-eu.honor.com/ccpcmd/services/dispatch/secured/CCPC/EN/ccpc/queryNewCommodityList/1000?productLevel=lv4
     - Variantes & SKUs (lv5/lv6) : GET .../queryNewCommodityList/1000?productLevel=lv5&productId=... / lv6
     - Grille tarifaire officielle par SKU : POST https://selfservice-eu.honor.com/ccpcmd/services/dispatch/secured/CCPC/EN/cspm/servicePrice/1000
     Fournit 100% des tarifs officiels constructeur HONOR en France en ~5 secondes.

- **Nom de la source** : `honor`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://www.honor.com/fr/support/sparepart-price/).
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/honor
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/honor/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_honor.csv` : Fichier tabulaire complet
2. `db_honor.json` : Export JSON structuré
3. `db_honor.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_honor_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_honor_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
HONOR_API_KEY=
HONOR_PROXY=
```
