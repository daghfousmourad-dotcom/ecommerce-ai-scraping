# Scraper : UTOPYA

## 📋 Présentation
Utopya.fr scraper: parts supplier — login, brand/model discovery, part costs.

- **Nom de la source** : `utopya`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://www.utopya.fr/customer/account/login/
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/utopya
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/utopya/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_utopya.csv` : Fichier tabulaire complet
2. `db_utopya.json` : Export JSON structuré
3. `db_utopya.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_utopya_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_utopya_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
UTOPYA_API_KEY=
UTOPYA_PROXY=
```
