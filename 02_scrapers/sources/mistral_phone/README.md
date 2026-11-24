# Scraper : MISTRAL_PHONE

## 📋 Présentation
Module de collecte automatisée pour mistral_phone.

- **Nom de la source** : `mistral_phone`
- **Domaine de données** : `product_sales`
- **URL cible** : Voir la configuration interne
- **Technologie principale** : `HTTP Client (Requests / Httpx / BeautifulSoup)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/mistral_phone
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/mistral_phone/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_mistral_phone.csv` : Fichier tabulaire complet
2. `db_mistral_phone.json` : Export JSON structuré
3. `db_mistral_phone.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_mistral_phone_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_mistral_phone_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
MISTRAL_PHONE_API_KEY=
MISTRAL_PHONE_PROXY=
```
