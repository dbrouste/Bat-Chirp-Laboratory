# Bat-Chirp-Laboratory

Laboratoire indépendant pour évaluer et, à terme, améliorer automatiquement la
détection et le suivi des chirps ultrasonores. Le code d'analyse audio reste dans
[`dbrouste/bat_full_spectrum_processing`](https://github.com/dbrouste/bat_full_spectrum_processing) ;
les annotations humaines proviennent notamment de
[`dbrouste/Bat-Acoustic-Analyser`](https://github.com/dbrouste/Bat-Acoustic-Analyser).

## État du projet

**Phase 1 opérationnelle :** index de corpus WAV/JSON, benchmark SNR existant,
agrégation indépendante, exécution multiprocessus compatible Windows, cache
par WAV, historique SQLite, et rapport HTML autonome.

**Non implémentés :** score géométrique à 20 perpendiculaires (sa spécification
est dans `docs/`), recherche automatique Optuna, agent IA et interface de
pilotage. Le laboratoire actuel n'est donc pas encore un agent autonome.

## Installation sur Windows

Prévoir deux clones voisins, par exemple :

```text
D:\Python\
  Bat-Chirp-Laboratory\
  bat_full_spectrum_processing\
```

Depuis PowerShell dans `D:\Python\Bat-Chirp-Laboratory` :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Premier essai (3 WAV)

```powershell
.\.venv\Scripts\python.exe -m laboratory `
  --processing-root "D:\Python\bat_full_spectrum_processing" `
  --annotations "D:\Bat\annotations" `
  --wav-dir "D:\Bat\WAV" `
  --out "D:\BatLaboratory\results" `
  --workers 4 `
  --max-files 3
```

`--annotations` désigne un JSON d'annotations ou un dossier les contenant
récursivement. Les WAV/JSON ne sont jamais modifiés. Seuls les chirps ayant
`source="manual"` et `status="validated"` sont utilisés comme référence.

Résultats : `report.html`, `report.json`, `manifest.csv`,
`experiments.sqlite`, et `runs/<id>/` pour l'historique. Les fichiers du cache
sont automatiquement invalidés lorsque changent le WAV, ses annotations,
l'algorithme, les métriques ou les paramètres. L'option `--no-cache` force
une nouvelle analyse.

Sans `--processing-root`, le programme cherche par défaut un clone
`../bat_full_spectrum_processing` à côté du dépôt du laboratoire.

## Vérification

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
```

Le test d'intégration multiprocessus nécessite un clone du dépôt de traitement
dans le dossier voisin. Il est ignoré sinon.

## Documentation

- [Mise en place Windows et limites actuelles](docs/LABORATOIRE_AUTONOME_WINDOWS.md)
- [Architecture et feuille de route](docs/ARCHITECTURE.md)
- [Spécification du score géométrique](docs/SPEC_Benchmark_Chirps_Geometrie_v0.1.md)

**Important :** les premières métriques mesurent les erreurs fréquentielles
normalisées par le SNR et le rappel de détection. Les faux positifs ne sont pas
validement mesurables sans annotations explicitement exhaustives. Aucun score
géométrique final ni aucune amélioration algorithmique ne sont revendiqués.

## Préparer un corpus depuis plusieurs dossiers WAV

Le générateur local `tools/build_dataset.py` parcourt récursivement un dossier
contenant les sous-dossiers de sessions, leurs WAV et les JSON d'annotations.
Il copie les JSON complets et les WAV référencés dans des jeux `calibration/`
et `test/` (80/20 cible), sans modifier les fichiers d'origine.

```powershell
py -3.12 tools/build_dataset.py --source "D:\MesEnregistrements" --output "D:\BatLaboratory\dataset-v1"
```

Le fichier `manifest.csv` assure la traçabilité. Les cas exclus sont répertoriés
dans `warnings.csv`. Le jeu de test est regroupé par session pour éviter les
fuites entre calibration et évaluation finale.

[Guide complet : construction du corpus](docs/CONSTRUIRE_CORPUS.md).
