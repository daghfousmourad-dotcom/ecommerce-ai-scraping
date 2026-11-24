# Scraper : SMART_REPAIR_63

## 📋 Présentation
Smart Repair 63 Scraper (https://smartrepair63.fr/tarifs.html).

Grille tarifaire complète de réparation smartphones et tablettes à Clermont-Ferrand :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  2. Parsing HTML Direct (BeautifulSoup / Requests) :
     - Découverte des 500+ modèles sur les 19 pages de séries et marques
       (iPhone, iPad, Samsung Séries S/A/J/M/Note/Z, Xiaomi, Oppo, Huawei, Honor, Google Pixel, etc.)
     - Extraction conjointe des cartes modèles et de leurs pages détails dédiées (.price-row)
     - Normalisation canonique des pannes (Écran, Batterie, Connecteur, Vitre Arrière, Caméra)
     - Export standard E-commerce AI Scraping (5 fichiers)

- **Nom de la source** : `smart_repair_63`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://smartrepair63.fr/tarifs.html).
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/smart_repair_63
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/smart_repair_63/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_smart_repair_63.csv` : Fichier tabulaire complet
2. `db_smart_repair_63.json` : Export JSON structuré
3. `db_smart_repair_63.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_smart_repair_63_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_smart_repair_63_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
SMART_REPAIR_63_API_KEY=
SMART_REPAIR_63_PROXY=
```
