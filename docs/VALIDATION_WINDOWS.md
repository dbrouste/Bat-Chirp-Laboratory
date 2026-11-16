# Reproductibilité et validation locale Windows

## Environnement de référence

La référence locale vérifiée est CPython **3.13.0 x64 sous Windows**, avec les
versions directes et transitives de `requirements-windows-py313.lock`.
Ce verrou provient de l'environnement existant, sans téléchargement. Il exclut
l'installation éditable du dépôt : le code doit provenir du checkout Git local.
`requirements.txt` conserve les dépendances directes non verrouillées ;
`pyproject.toml` déclare les mêmes dépendances d'exécution et l'extra `test`.

Les instructions historiques Python 3.12 ne constituent pas l'environnement
verrouillé de référence. La compatibilité annoncée par le package (`>=3.10`)
n'est pas une matrice de versions testées. Ne pas installer les dépendances
graphiques des dépôts voisins dans l'environnement rapide par défaut.

Pour préparer un environnement distinct, depuis la racine du dépôt :

```powershell
py -3.13 -m venv .venv-validation
if ($LASTEXITCODE -ne 0) { throw 'Creation du venv impossible' }
.\.venv-validation\Scripts\python.exe -m pip install --only-binary=:all: -r .\requirements-windows-py313.lock
if ($LASTEXITCODE -ne 0) { throw 'Installation des dependances impossible' }
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1 -Python .\.venv-validation\Scripts\python.exe
```

`py -3.13` sélectionne une version 3.13 installée ; vérifier sa version exacte
avec `.\.venv-validation\Scripts\python.exe --version`. Pour reproduire la
référence au niveau du correctif Python, installer préalablement Python 3.13.0.
Le verrou a été validé dans le venv existant ; sa réinstallation dans un venv
vierge n'a pas été exécutée. L'installation télécharge des distributions Python,
mais n'accède pas au corpus. Pour rester hors ligne, préparer un dépôt local de
wheels Windows x64, puis ajouter `--no-index --find-links CHEMIN_WHEELS` à pip.
L'installation échoue si une roue compatible manque, sans compilation implicite.

Les versions sont figées, mais les archives n'ont pas de hachages pip enregistrés.
Pour une conservation à long terme des binaires, archiver également les wheels
et leurs SHA-256. Le verrou n'est pas une garantie de reproductibilité bit à bit
entre systèmes ou bibliothèques natives différents.

## Validation rapide sans corpus

Avec l'environnement `.venv` existant :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1
if ($LASTEXITCODE -ne 0) { throw 'Validation locale echouee' }
```

Le script fonctionne aussi depuis un autre dossier avec un chemin absolu vers
le fichier. Il se place temporairement à la racine du dépôt, utilise son propre
venv par défaut, contrôle `pip check`, désactive le chargement automatique des
plugins pytest et limite les threads BLAS à un par processus. Il restaure ces
variables et le dossier courant, puis transmet le code d'échec.
Il n'installe rien, n'utilise aucun modèle et ne lance aucun benchmark du corpus.
Les tests écrivent uniquement des données synthétiques dans les dossiers
temporaires pytest, ainsi que les caches habituels de Python/pytest.

Équivalent pour sélectionner directement les tests :

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m 'not integration and not corpus'
```

## Trois catégories de tests

| Sélection | Dépendances et données |
|---|---|
| `not integration and not corpus` | Tests rapides autonomes, fichiers temporaires et simulations ; aucun clone voisin requis |
| `integration and not corpus` | Deux tests utilisant les clones voisins et de l'audio synthétique, sans corpus privé |
| `corpus` | Marqueur réservé aux futurs tests explicitement autorisés sur corpus réel ; aucun actuellement |

Pour exécuter seulement les intégrations synthétiques, après préparation des
clones voisins et de leurs dépendances nécessaires :

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m 'integration and not corpus'
```

Ces intégrations sont ignorées si leurs fichiers externes requis manquent.
Elles ne font pas partie de la validation rapide. Ajouter `@pytest.mark.corpus`
à tout futur test nécessitant des enregistrements réels et ne jamais charger
ces enregistrements au niveau des imports de module. Les marqueurs inconnus
échouent grâce à `--strict-markers`. Un simple `pytest -q` continue à sélectionner
tous les tests, y compris les intégrations ; utiliser le script pour le mode rapide.

## Identité des expériences et invalidation

`laboratory/fingerprints.py` centralise l'identité du code et de l'environnement :

- contenu de tous les fichiers Python de `laboratory/` et `chirp_geometry/` ;
- pour le benchmark, sources `bat_analysis/` et `benchmark/` du clone évalué ;
- pour les campagnes d'annotations, sources `bat_analyser/` du clone de rendu,
  y compris lorsque son chemin est implicite ;
- configurations `pyproject.toml`, `requirements*.txt`, `requirements*.lock` et
  `constraints*.txt` à la racine de chaque dépôt concerné ;
- version complète et implémentation Python, système, architecture et versions
  de toutes les distributions Python installées, y compris transitives.

Une création, suppression ou modification de source change donc l'identité.
Les empreintes sont volontairement conservatrices : une dépendance ajoutée ou
un changement de rapport peut imposer un recalcul même si les mesures numériques
resteraient identiques. Les chemins des clones font aussi partie de l'identité.

Le moteur `runner` combine cette identité avec les paramètres et les SHA-256
des WAV et annotations évalués. L'optimiseur et le pilote de code utilisent
également cette identité pour refuser une reprise devenue incompatible.

La campagne d'annotations combine les paramètres acoustiques, fenêtre, sélection
de chirps, manifeste et SHA-256 réels des WAV/JSON de calibration. Un remplacement
à taille et date identiques invalide le cache. Les WAV/JSON de test ne sont pas
ouverts ; le manifeste commun est néanmoins haché en entier. Les fichiers
partagés entre plusieurs entrées sont hachés une fois par calcul de clé.
La lecture complète des WAV pour hachage entraîne un coût d'E/S, sans analyse
acoustique ; elle est répétée pour chaque calcul de clé de fenêtre.

Les seuils de revue sont recalculés à partir des scores conservés : changer un
seuil actualise les résultats dérivés sans refaire la STFT. Modifier les fenêtres
ou l'implémentation des scores invalide les résultats acoustiques correspondants.

Ces changements rendent les anciennes clés de cache incompatibles. Conserver
les rapports historiques ; utiliser un nouveau dossier de sortie pour les agents
si la reprise signale un changement de provenance. Aucune migration ni suppression
automatique de données n'est effectuée.

Les empreintes décrivent les fichiers avant l'exécution : garder les entrées et
les sources immuables pendant les expériences. Les modifications manuelles de
binaires installés sans changement de version et les pilotes natifs ne sont pas
hachés. Le verrou et l'isolation des environnements restent nécessaires.
