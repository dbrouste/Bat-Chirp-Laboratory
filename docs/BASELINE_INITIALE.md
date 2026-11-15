# Benchmark initial de l'algorithme — baseline v1

## Objectif

Établir **une première mesure reproductible de référence** du détecteur
actuellement raccordé au laboratoire (le connecteur transitoire
`bat_full_spectrum_processing/benchmark/reassignment_detector_seeds.py`).

**Attention :** ce détecteur externe n'est pas nécessairement identique à
l'algorithme actuellement chargé dans Bat-Acoustic-Analyser. Son commit Git
est enregistré avec le rapport. Pour évaluer précisément celui de
l'analyseur, il faudra ultérieurement lui raccorder un adaptateur dédié.

L'entrée est le **corpus figé `dataset-v1`**. Le mode ci-dessous ne lit que
la partition **calibration** de `manifest.csv` : aucune annotation du jeu
test n'est chargée dans le benchmark. Un futur test final indépendant aura
un mode distinct.

## Préparer les dépôts voisins (Windows)

Ouvrir PowerShell dans le dossier de travail contenant
`Bat-Chirp-Laboratory`. Installer le traitement externe à côté si absent :

```powershell
git clone https://github.com/dbrouste/bat_full_spectrum_processing.git
```

Les répertoires doivent être voisins :

```text
Python-Toolbox/
  Bat-Chirp-Laboratory/
  bat_full_spectrum_processing/
```

Dans `Bat-Chirp-Laboratory` :

```powershell
git pull origin develop
python -m pip install -r requirements.txt
python -m pytest -q tests/test_frozen_dataset.py tests/test_laboratory.py
```

## 1. Exécution de validation : trois WAV

```powershell
python -m laboratory `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --out "C:\Users\damien\Documents\Python\Python-data\baseline-v1" `
  --workers 4 `
  --max-files 3
```

Le moteur utilise par défaut le dépôt `bat_full_spectrum_processing` situé
à côté de `Bat-Chirp-Laboratory`. S'il se trouve ailleurs, ajouter
`--processing-root "CHEMIN_VERS_LE_DEPOT"`.

Inspecter dans `baseline-v1/report.json` :
- `metadata.status == "complete"`, faute de quoi la campagne n'est pas valide ;
- `metadata.frozen_dataset.partition == "calibration"` ;
- `metadata.frozen_dataset.hashes_verified == true` ;
- `metadata.algorithm_git.git_commit` : version du détecteur ;
- `metadata.geometric_tolerances_calibrated == false` :
  la normalisation géométrique est présente mais ses tolérances finales
  ne sont pas encore calibrées.

## 2. Campagne initiale complète

Après le smoke test, lancer **dans un répertoire distinct**, pour éviter
de confondre les rapports portant sur des nombres de WAV différents :

```powershell
python -m laboratory `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --out "C:\Users\damien\Documents\Python\Python-data\baseline-v1-complet" `
  --workers 4
```

Ne pas ajouter `--max-files`. Ne pas utiliser `--no-cache` sauf pour une
mesure volontaire sans cache. Les rapports sont dans :
- `report.json`, `report.html` : récapitulatif du dernier run ;
- `runs/<run_id>/` : copies immuables des rapports de chaque exécution ;
- `manifest.csv` : inventaire des WAV effectivement mesurés ;
- `experiments.sqlite` : historique des expériences ;
- `cache/` : résultats réutilisables par empreinte.

## Mesures et limites

Les premières métriques disponibles sont le rappel de détection
(`detector_recall` et version pondérée), le suivi, les erreurs de début/fin
et les distances normales géométriques en `kHz_eq` avec couverture.
Les détails sont aussi ventilés par SNR (≤ 15, 15–35, ≥ 35 dB).

- **Pas de précision ni de taux de faux positifs** sans zones dont
  l'annotation est attestée exhaustive. De nombreux chirps restent non annotés.
- Les limites géométriques à 3–5 kHz historiques sont exploratoires
  en `kHz_eq` : comparer les versions sans prétendre à une calibration absolue.
- Ne jamais optimiser sur la partition test.
- Le rapport n'est pas valide si `status=incomplete` ou si les empreintes
  d'entrée ne correspondent pas au manifeste.

Cette baseline constituera le point zéro pour les futures comparaisons
à code et données constants.
