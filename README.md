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

## Auditer la qualité des annotations (calibration uniquement)

Un audit descriptif et non destructif est disponible pour repérer les chirps
manuels à inspecter visuellement (durée, fréquence, points, segments abrupts,
retournements, dépassements de la durée du WAV ou de Nyquist).

```powershell
python tools/audit_annotations.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --output "C:\Users\damien\Documents\Python\Python-data\quality-v1"
```

Les résultats sont dans `chirp_quality.csv`, `quality_summary.json`
et `audit_warnings.csv`. Par défaut, le jeu de test n'est pas analysé.
Voir [Audit des annotations](docs/AUDIT_ANNOTATIONS.md).

## Mettre à jour des annotations corrigées

Le corpus existant peut être actualisé sans modifier la répartition des sessions :

```powershell
python tools/update_dataset.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --wav "20250615_175727.wav"
```

Le JSON complet de la session est synchronisé depuis le répertoire d'origine
référencé dans le manifeste, avec sauvegarde de la version précédente.
Le test indépendant reste verrouillé. Voir
[la procédure de mise à jour](docs/ACTUALISER_CORPUS.md).

## Audit acoustique des références (même affichage 65/35 que l'annotateur)

L'outil examine le soutien spectral le long des annotations manuelles sur
**calibration uniquement**. Il réutilise directement le pipeline de rendu
de `Bat-Acoustic-Analyser` et son mélange **65 % STFT / 35 % Reassignment**
en amplitudes linéaires ; ce n'est pas un second algorithme de détection.

```powershell
python tools/audit_acoustic.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --output "C:\Users\damien\Documents\Python\Python-data\acoustic-blend-v1" --reassignment-percent 35 --max-chirps 10
```

Les paramètres de STFT, de recouvrement et de soustraction spectrale
sont personnalisables pour retrouver les réglages de l'interface.
[Guide et limites de l'audit acoustique](docs/AUDIT_ACOUSTIQUE.md).

## Audit acoustique V2 — profils perpendiculaires et continuité

`tools/audit_acoustic_v2.py` réutilise la représentation mixte STFT 65 %
/ Reassignment 35 % de Bat-Acoustic-Analyser, et teste une **crête continue
sur profils perpendiculaires** aux annotations manuelles.

```powershell
python tools/audit_acoustic_v2.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --output "C:\Users\damien\Documents\Python\Python-data\acoustic-v2-normals" --max-chirps 10 --plot-top 5
```

La fusion prend maintenant une STFT à hop **32 échantillons max** et
le Reassignment à hop **16 échantillons**, configurables par
`--stft-hop` et `--reassignment-hop`. Cela corrige l'ancien hop STFT
de 256 échantillons qui produisait des spectrogrammes en gros blocs
sur les chirps courts ; relancer les audits antérieurs pour comparer.

Il exporte `acoustic_chirps_v2.csv`, `annotation_review_queue.csv`
(avec chemins sources du manifeste), un résumé JSON et les fichiers HTML
interactifs des cas prioritaires. Le jeu test n'est jamais traité et les
annotations ne sont pas modifiées. Voir
[la documentation de l'audit V2](docs/AUDIT_ACOUSTIQUE_V2.md).

### Validation de l'audit V2

Pour contrôler le suivi même lorsqu'aucune annotation n'est signalée,
utiliser `--plot-mode validation --plot-detail both --plot-top 10`.
Pour tester des erreurs artificielles de ±3/5 kHz sans modifier le corpus,
ajouter `--validate-shifts --shift-limit 10`. La sortie comporte alors
les visualisations HTML (spectrogramme + profils perpendiculaires) et
`shift_validation.csv`. Voir
[la procédure complète](docs/AUDIT_ACOUSTIQUE_V2.md#validation-du-suivi-indépendamment-des-alertes).
