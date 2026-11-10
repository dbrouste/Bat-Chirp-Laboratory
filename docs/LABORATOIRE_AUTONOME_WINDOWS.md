# Laboratoire local de chirps — première phase

**État :** exécution reproductible du benchmark existant, sans agent IA et sans
optimisation Optuna pour le moment. Le score géométrique à 20 perpendiculaires
reste la proposition du document
[`SPEC_Benchmark_Chirps_Geometrie_v0.1.md`](SPEC_Benchmark_Chirps_Geometrie_v0.1.md),
pas encore un indicateur du laboratoire.

## PC cible

- Windows, Ryzen 7 8845HS (8 cœurs/16 threads), 64 Go de RAM ;
- `workers=4` au départ, à comparer ensuite à 2 et 6 ;
- calcul multiprocessus `spawn` compatible Windows, un WAV par tâche ;
- threads BLAS/OMP limités à 1 **dans les workers** pour éviter la
  sur-souscription CPU ;
- WAV/JSON et résultats sur SSD local, hors du dépôt Git.

## Préparation (PowerShell)

Cloner **deux dépôts séparés** et voisins :

```text
D:\Python\Bat-Chirp-Laboratory\
D:\Python\bat_full_spectrum_processing\
```

Dans `D:\Python\Bat-Chirp-Laboratory` :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Avec `uv` installé, on peut alternativement créer l'environnement puis installer
les dépendances via `uv venv` et `uv pip install -r requirements.txt`.

Vérifier rapidement l'aide :

```powershell
.\.venv\Scripts\python.exe -m laboratory --help
```

## Premier essai : 3 WAV

```powershell
.\.venv\Scripts\python.exe -m laboratory `
  --processing-root "D:\Python\bat_full_spectrum_processing" `
  --annotations "D:\Bat\annotations" `
  --wav-dir "D:\Bat\WAV" `
  --out "D:\BatLaboratory\results" `
  --workers 4 `
  --max-files 3
```

`--annotations` accepte un JSON unique avec une entrée `files`, ou un dossier
contenant des JSON d'annotations (parcours récursif). Dans le second cas, les
noms de WAV sont résolus par rapport au dossier des WAV, sinon au dossier du
JSON lorsque nécessaire. Les conflits de noms et les chemins hors du corpus
provoquent une erreur explicite.

Le répertoire `--out` contient :

- `report.json` et `report.html` : la dernière exécution ;
- `manifest.csv` : index du corpus (durée, Fs, chirps manuels validés) ;
- `runs/<id>/` : les rapports et manifestes immuables de chaque run ;
- `experiments.sqlite` : index des exécutions, métriques et paramètres ;
- `cache/` : sorties par WAV pour reprendre et ne pas refaire un même calcul.

Chaque clé de cache prend en compte les **octets du WAV**, les données
d'annotation, les sources Python de l'algorithme et du benchmark, les versions
de dépendances pertinentes et les paramètres. Une modification d'un WAV ou
du suivi invalide donc la sortie correspondante. `--no-cache` force le recalcul.
La version Git exacte du dépôt de traitement et celle du laboratoire sont
enregistrées séparément dans chaque rapport ; `--processing-root` peut viser
un clone Git sur une branche expérimentale spécifique.

## Première campagne complète

Retirer `--max-files` ; conserver `--workers 4`. Puis comparer prudemment
`--workers 2` et `--workers 6` avec `--no-cache` pour mesurer la vitesse
réelle du PC (y compris limites thermiques et mémoire).

Les paramètres déjà exposés peuvent être modifiés avec `--n-fft`, `--hop`,
`--general-min-width-ms`, `--search-margin-ms`, `--low-snr-weight` et
`--no-echo-trim`.

## Règles du benchmark

1. Seuls les chirps portant **`source=manual` ET `status=validated`** sont
   comptés comme vérité de référence. Les annotations automatiques ou
   brouillons sont ignorées, sans suppression ni modification du JSON source.
2. Le détecteur n'utilise pas les trajectoires manuelles comme graines.
3. Les fichiers sans annotation manuelle peuvent servir à décrire les
   candidats détectés, mais les faux positifs ne sont pas scorés tant que
   l'exhaustivité de l'annotation n'a pas été garantie.
4. Un rapport avec WAV manquant ou worker en échec porte `status=incomplete` :
   il ne doit **pas** être promu comme amélioration.
5. Le temps `duration_s` mesure toute l'exécution (chargement, analyse et
   benchmark) et n'est pas encore un profil détaillé par étape.

## Prochaines étapes

1. Construire un corpus figé avec séparation par session/nuit, dont un jeu de
   test final jamais consulté pendant l'optimisation.
2. Implémenter et valider la spécification géométrique avec ses cas tests :
   queues suivant un écho, chirps tronqués, harmoniques simultanées, knees.
3. Ajouter les chronomètres des étapes spectrogramme / détection / modelling
   / simplification, puis les caches **intermédiaires** seulement lorsque leur
   dépendance aux paramètres est prouvée.
4. Ajouter Optuna : budget limité, sous-ensembles d'essais, contraintes de
   rappel haut SNR et de vitesse, validation indépendante.
5. Connecter un agent de code dans des branches/espaces Git isolés ; jamais
   de mutation des références manuelles ni de fusion automatique vers `main`.
