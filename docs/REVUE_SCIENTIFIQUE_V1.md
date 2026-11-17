# Revue scientifique des erreurs du pilote de développement

## Sauvegarde et portée

L'étape 4 est sauvegardée au commit local `71e8250`, tag
`checkpoint/detection-diagnostics-v1`. L'étape 5 est préparée sur
`feat/scientific-error-review`, sans modification de Bat-Acoustic-Analyser.
Le dossier préexistant `bat_chirp_geometry.egg-info/` est préservé.

L'exporteur `tools/prepare_scientific_review.py` n'appelle aucun détecteur,
suivi, appariement ou scoring. Il lit le rapport instrumenté existant, vérifie
son SHA-256 contre la base de campagne, réutilise les contrôles d'empreintes
du runner et vérifie à nouveau l'environnement et les entrées avant de terminer.
La sélection V1 est vérifiée avant toute lecture de WAV/annotation du dataset.
Seuls les WAV sélectionnés de développement sont ouverts pour empreinte/copie ;
aucun WAV ni annotation de validation/test n'est ouvert. Les manifestes et
assignations de cohortes ne sont lus que comme métadonnées.

L'exporteur reste dans `tools/` : il ne change aucun module scientifique ni le
code du runner. L'empreinte scientifique existante reste valide ; l'exporteur
possède sa propre empreinte SHA-256 dans `review_manifest.json`. Une empreinte
obsolète entraîne un refus, jamais un recalcul automatique. Un dossier de sortie
existant est toujours refusé pour préserver les décisions déjà saisies.

## Créer les exports sous PowerShell

Depuis Bat-Chirp-Laboratory, avec l'environnement Windows existant :

```powershell
$Data = "$env:USERPROFILE\Documents\Python\Python-data"
$Review = "$Data\development-review-v1"
.\.venv\Scripts\python.exe .\tools\prepare_scientific_review.py `
  --campaign "$Data\development-diagnostic-v1" `
  --output $Review --max-copy-gb 1 --min-free-gb 2
if ($LASTEXITCODE -ne 0) { throw 'Export de revue refuse ou incomplet' }
```

Pour refaire un export, choisir un **nouveau dossier**. Il n'y a pas de `--force`.
Les copies WAV sont plafonnées à 1 Gio par défaut, avec réserve libre de 2 Gio
avant copie ; il s'agit d'un contrôle d'espace libre, pas d'un quota disque dur.
Les copies sont séquentielles et physiques, sans lien symbolique ni lien dur.
Un export interrompu reste incomplet tant que `review_manifest.json` n'existe
pas ; conserver ce dossier et choisir un nouveau chemin pour recommencer.

## Contenu et identités

| File détaillée | Cas attendus | CSV pour navigation dans l'analyseur |
|---|---:|---|
| `no_blob.csv` | 10 | `no_blob.analyser.csv` |
| `nms.csv` | 13 | `nms.analyser.csv` |
| `end_error_top20.csv` | 20 | `end_error_top20.analyser.csv` |
| `negative_final.csv` | 57 | `negative_final.analyser.csv` |

Le classement des erreurs de fin porte sur les 153 appariés, par erreur absolue
décroissante, puis chemin WAV et identifiant de chirp en cas d'égalité. Le signe
est conservé : automatique moins manuel. Aucune erreur n'est recalculée à partir
des signaux audio.

Chaque `case_id` est déterministe et indépendant du dossier d'export : baseline,
file, chemin relatif du WAV, empreinte audio, référence et éventuelle sortie
automatique. Le même chirp présent dans deux files a deux cas distincts. Aucun
rapprochement par simple nom de fichier n'est utilisé.

`review_cases.json` et `cases/<case_id>.json` contiennent les chemins originaux,
l'identifiant manuel éventuel, tous les points manuels disponibles, les métriques
existantes, le motif et la preuve. Les points de revue sont en ms/kHz ; les traces
du pipeline conservent leurs champs natifs en secondes/Hz. Les négatifs gardent
aussi le tableau natif complet [secondes, Hz, amplitude normalisée]. Les traces
par WAV sont dans `traces/`, avec listes de candidats, paramètres, graines,
retours de suivi et empreintes sources déjà capturés à l'étape 4.

Pour NMS : les candidats admissibles présents avant NMS et absents après NMS
sont exportés, avec ancre temporelle/fréquentielle et listes avant/après. Le
suppresseur exact n'avait pas été tracé : il n'est pas inventé à partir d'une
simple proximité. Une ancre et une bbox ne sont pas une trajectoire suivie.

Pour les positifs appariés : l'étape 4 a conservé les extrémités et l'empreinte,
mais pas tous les points de la trajectoire automatique. L'export fournit les
deux extrémités réelles, explicitement **sans courbe de connexion**. Il ne crée
pas de ligne droite artificielle et ne relance pas le suivi pour combler ce
manque. Les points manuels restent entièrement disponibles.

`review_manifest.json` contient les empreintes de provenance et les SHA-256
initiaux des fichiers exportés. Les JSON de travail et `human_decisions.csv`
peuvent ensuite changer au cours de la revue ; leurs empreintes initiales
servent à retrouver l'état de départ, pas à nier ces modifications humaines.

## Import compatible et protection des originaux

Les sources auditées dans Bat-Acoustic-Analyser sont :

- `bat_analyser/annotation_quality.py` : `QualityReviewSession.from_acoustic_csv`,
  jointure WAV/JSON par manifeste, vérification des empreintes, résolution des IDs ;
- `bat_analyser/annotation_quality_dialog.py` : ouverture par identifiant entier,
  filtre à zéro en l'absence de score, enregistrement QA limité au manuel validé ;
- `bat_analyser/window.py` : menu `Analysis`, chargement avec annotation explicite,
  zoom sur le chirp, restauration des courbes et arrêt de la détection initiale
  quand des chirps existent ou que le WAV porte `no_chirp` ;
- `bat_analyser/chirp_session.py` : schéma `files/<nom WAV>/chirps/points`,
  points `t_ms`/`f_khz`, brouillons `source=automatic`, `status=draft`.

Les `.analyser.csv` réemploient ce lecteur, sans nouveau système de navigation.
Ils contiennent `wav`, `annotation`, `partition`, `session`, `chirp_id`,
`review_priority`, `reason` et des colonnes supplémentaires ignorées par le
lecteur. Aucun score de suspicion n'est inventé.

Le manifeste **à choisir est celui de `analyser_workspace`**, pas celui du
corpus `dataset-v1`. Ce projet dérivé contient uniquement les WAV nécessaires
aux files de revue, copiés depuis le dataset gelé. Chaque WAV a son propre
dossier pour empêcher l'analyse/préchargement d'un voisin hors revue. Les
chemins `source_*` de ce manifeste pointent tous vers les copies de travail.
Les chemins originaux restent dans les dossiers de cas et CSV détaillés.

Le snapshot JSON de revue et le JSON éditable sont deux fichiers distincts.
Les négatifs ont des courbes automatiques dérivées ajoutées uniquement dans
ce projet de visualisation, sans changer leur étiquette `no_chirp`. Ce projet
porte `review_only_not_ground_truth` : **ce n'est pas un nouveau corpus de
benchmark**. Les identifiants entiers de navigation des négatifs sont dérivés
du `case_id`, distincts de tout identifiant de référence manuelle.

## Ouvrir les quatre files dans Bat-Acoustic-Analyser

1. Démarrer l'analyseur déjà installé. Si son environnement de développement
   existe, depuis son dépôt : `.\.venv\Scripts\python.exe .\main.py`.
   L'exporteur n'installe rien et ne lance pas la GUI.
2. Avant de charger les cas, décocher **Analyse automatique du dossier**.
   Désactiver **Auto notch** et **Filtered preview** pour examiner le signal
   original. Les commandes de détection, comparaison et validation ne sont
   pas nécessaires pour cette revue.
3. Ouvrir **Analysis → Contrôle qualité des annotations manuelles...**.
   Dans le sélecteur, choisir le filtre CSV et un fichier `.analyser.csv`.
4. À la demande du manifeste, sélectionner
   `$Review\analyser_workspace\manifest.csv`. Ne pas changer la racine vers
   les originaux. Les chemins « originaux » affichés par ce module désignent
   ici volontairement les **copies** du projet de revue.
5. Garder **Score minimum = 0** et **Décisions = Toutes**. Double-cliquer une
   ligne ou cliquer **Ouvrir le chirp** : le module charge le WAV copié, son JSON
   de travail et zoome sur l'identifiant demandé. Les chirps déjà présents et
   l'étiquette `no_chirp` empêchent le lancement automatique initial du détecteur.
6. Pour NMS, examiner le spectrogramme à l'ancre du candidat supprimé indiquée
   dans `cases/<case_id>.json`, en regard de la courbe manuelle. Pour les erreurs
   de fin, comparer la fin manuelle avec les coordonnées automatiques conservées.
   L'index `index.html` donne accès aux annexes, sans lancer de calcul.
7. Ouvrir **séparément** `negative_final.analyser.csv` pour les 57 courbes.
   Le lecteur sait naviguer par ID même si la courbe est automatique. Le nom
   du menu reste « annotations manuelles », mais ces lignes n'en sont pas :
   `manual_chirp_id` est vide et la courbe reste un brouillon automatique.
   **Enregistrer le statut dans le JSON original** refuse ces courbes non
   manuelles ; enregistrer les conclusions dans `human_decisions.csv`.
8. Pour toute file, consigner les décisions dans ce CSV séparé. Ne pas utiliser
   **Synchroniser le corpus…** pour ce projet. Aucune modification des copies
   de travail n'est réimportée automatiquement dans les annotations sources.

Le lecteur et la résolution des quatre imports sont testés avec son véritable
module Python sur des données synthétiques. L'affichage Qt et l'écoute restent
à vérifier humainement ; aucune validation graphique automatique n'est revendiquée.

## Décisions humaines à enregistrer

Deux axes indépendants évitent de confondre identité du signal et annotation :

| Champ | Valeurs proposées |
|---|---|
| `signal_classification` | `true_chirp`, `artifact`, `echo`, `ambiguous`, `other` |
| `annotation_assessment` | `correct`, `annotation_error`, `ambiguous`, `not_applicable` |
| `confidence` | `low`, `medium`, `high` |

Ajouter `reviewer`, `reviewed_utc`, `evidence`, `notes` ; remplir `other_cause`
si une autre cause est retenue. Expliquer l'erreur d'annotation éventuelle
(identité, point de début/fin, fréquence, doublon, autre), avec coordonnées
observées et motifs, sans écraser les points de référence.

Un vrai chirp peut avoir une annotation erronée : les deux décisions sont donc
compatibles. Sur un négatif sans référence manuelle, choisir `not_applicable`
pour l'annotation ; un vrai chirp observé mettrait l'étiquette `no_chirp` en
question pour une décision ultérieure, sans changement automatique. Un écho
nécessite une preuve documentée et, si possible, un lien vers le signal parent.
Si la preuve manque, conserver `ambiguous`. Toutes les décisions sont initialement
vides : l'export n'effectue aucune revue scientifique à la place d'un humain.

## Limites et évolution éventuelle de l'analyseur

L'import existant affiche les courbes manuelles et les brouillons négatifs,
mais pas les bboxes/candidats NMS ou les extrémités isolées comme couches de
diagnostic. L'accès aux coordonnées par annexe permet la revue dès maintenant.
Pour superposer ces éléments sans ambiguïté, une évolution ultérieure pourrait
ajouter une couche **diagnostic en lecture seule**, liée au `case_id`, et un
mode bloquant sauvegarde, analyse automatique et synchronisation pendant la
revue. Elle réutiliserait `QualityReviewSession` et le rendu existant. Aucun
changement n'est réalisé dans Bat-Acoustic-Analyser à cette étape.

Le protocole reste exploratoire. Le pilote ne contient aucune référence de
SNR ≤15 dB ; la validation historique reste limitée à une session distincte,
qui n'est pas ouverte ici. Aucune catégorie n'explique à elle seule la cause
acoustique des dix absences de blob.

## Vérification locale

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1
.\.venv\Scripts\python.exe -m pytest -q tests/test_scientific_review.py -m integration
git diff --check
```

Les tests rapides ne nécessitent aucun corpus privé. L'intégration utilise
uniquement le module de résolution du dépôt voisin et des fichiers synthétiques.

## Export exécuté et vérifié

L'export `..\..\Python-data\development-review-v1` contient les quatre files
attendues : **10 / 13 / 20 / 57 cas**, soit 100 identifiants uniques. Il copie
**16 WAV**, exactement **58 782 246 octets (56,059 Mio)**, dans le projet isolé.
Les 13 cas NMS contiennent 13 candidats supprimés distincts. Les vingt erreurs
absolues de fin vont de **2,1738515243 à 5,9869936200 ms** ; elles sont toutes
signées positives, donc tardives selon la convention conservée.

Les 177 empreintes initiales recensées dans le manifeste d'export sont vérifiées.
Les cent lignes sont également vérifiées avec le vrai `QualityReviewSession` :
WAV conformes, correspondances JSON non modifiées, identifiants présents et
chemins de travail intégralement internes à l'export. Les 57 courbes négatives
restent `source=automatic` et `status=draft`, sans identifiant manuel.
`human_decisions.csv` contient cent lignes sans conclusion préremplie.

Résultats Windows : `pip check` passe ; **259 tests rapides réussis, quatre
intégrations exclues** ; tests ciblés de l'exporteur, intégration comprise :
**13 réussis**. `git diff --check` passe. Aucun calcul scientifique sur le corpus
réel n'est relancé et aucun résultat de campagne n'est régénéré.

Le dépôt voisin de traitement scientifique est propre. Des modifications
locales sont présentes dans le dépôt de l'analyseur (`adaptive_notch.py`,
`adaptive_notch.py.bak`, `spectral_floor.py`) ; elles ne sont ni éditées, ni
ajoutées à Git, ni déplacées par cette étape. Aucune GUI n'est lancée par l'agent.
