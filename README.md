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

**Phase 2 expérimentale :** score géométrique indépendant à 20 normales locales,
appariement un-à-un, SNR de référence calculé sur les annotations humaines,
pénalités de couverture et de prolongement. Calibration supervisée des seuils
avec jeux développement/validation/test séparés par nuit d'enregistrement.
Les seuils par défaut ne sont **pas encore calibrés sur des chirps réels**.

**Non implémentés :** recherche automatique Optuna, agent IA et interface de
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

### Score géométrique (expérimental)

Ajouter `--scoring geometry` à la commande précédente. Ce mode fait tourner
le détecteur **sans l'aider avec les annotations manuelles**, suit toutes ses
propositions, puis compare les courbes aux 20 normales de chaque chirp manuel.
Le rapport HTML donne un score de 0 à 100, le rappel pondéré selon le SNR et
les trajectoires non retrouvées. Le mode historique reste `--scoring snr`.

Le score utilise : pente principale du chirp manuel normalisée à −45° ;
tolérance 5 kHz à SNR ≤15 dB, 3 kHz à SNR ≥35 dB ; poids SNR de 1 à 3 ;
prolongement final seulement accepté s'il suit la tangente de fin.

Pour calibrer les seuils d'acceptation, constituer un fichier JSON d'exemples
étiquetés `expected_good=true/false`, avec `id`, `group` (nuit/session),
`split=development|validation|test`, `snr_max_db`, `manual_points` et
`automatic_points` (coordonnées `[temps_ms, fréquence_kHz]`). Voir
[`docs/CALIBRATION_GEOMETRIQUE.md`](docs/CALIBRATION_GEOMETRIQUE.md).

```powershell
.\.venv\Scripts\python.exe -m laboratory.calibration `
  --examples "D:\BatLaboratory\geometry_examples.json" `
  --output "D:\BatLaboratory\geometry_calibration.json"

.\.venv\Scripts\python.exe -m laboratory `
  --processing-root "D:\Python\bat_full_spectrum_processing" `
  --annotations "D:\Bat\annotations" --wav-dir "D:\Bat\WAV" `
  --out "D:\BatLaboratory\geometry-results" --workers 4 `
  --scoring geometry `
  --geometry-config "D:\BatLaboratory\geometry_calibration.json"
```

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

**Important :** aucun score géométrique final ni aucune amélioration
algorithmique sur le corpus réel ne sont encore revendiqués. Les faux positifs
ne sont valablement comptés que si les annotations sont explicitement exhaustives.
