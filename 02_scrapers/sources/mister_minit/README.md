# Scraper : MISTER_MINIT

## 📋 Présentation
Module de collecte automatisée pour mister_minit.

- **Nom de la source** : `mister_minit`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : Voir la configuration interne
- **Technologie principale** : `HTTP Client (Requests / Httpx / BeautifulSoup)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/mister_minit
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/mister_minit/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_mister_minit.csv` : Fichier tabulaire complet
2. `db_mister_minit.json` : Export JSON structuré
3. `db_mister_minit.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_mister_minit_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_mister_minit_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
MISTER_MINIT_API_KEY=
MISTER_MINIT_PROXY=
```
