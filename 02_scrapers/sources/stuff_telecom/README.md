# Scraper : STUFF_TELECOM

## 📋 Présentation
Module de collecte automatisée pour stuff_telecom.

- **Nom de la source** : `stuff_telecom`
- **Domaine de données** : `product_sales`
- **URL cible** : Voir la configuration interne
- **Technologie principale** : `HTTP Client (Requests / Httpx / BeautifulSoup)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/stuff_telecom
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/stuff_telecom/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_stuff_telecom.csv` : Fichier tabulaire complet
2. `db_stuff_telecom.json` : Export JSON structuré
3. `db_stuff_telecom.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_stuff_telecom_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_stuff_telecom_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
STUFF_TELECOM_API_KEY=
STUFF_TELECOM_PROXY=
```
