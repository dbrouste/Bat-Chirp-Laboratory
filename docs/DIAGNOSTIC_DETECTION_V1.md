# Diagnostic des échecs de détection — V1

## Sauvegarde et portée

Le pilote est sauvegardé au commit local `9f23d7d`, tag
`checkpoint/development-real-pilot-v1`. Le diagnostic est développé sur
`feat/detection-failure-diagnostics`. Aucun push ni modification des sources
scientifiques du dépôt de traitement n'est effectué.

Le diagnostic utilise exclusivement le fichier de sélection du pilote : huit
WAV positifs de développement, vingt WAV `no_chirp`, 214 références. Il ne
charge aucun WAV ni annotation de validation/test. Les originaux ne sont jamais
modifiés ; leurs chemins servent à la revue. Le protocole demeure exploratoire,
avec absence de faibles SNR et validation historique limitée à une session.

## Audit du pipeline exécuté

| Étape réelle | Source et comportement vérifiés |
|---|---|
| Référence acoustique | `benchmark/snr_metrics.py` : STFT Flattop 512/hop 64, bruit médian par fréquence sur la trajectoire manuelle ; distincte du SNR du détecteur |
| Carte spectrale du détecteur | `bfsp_clean_patched.compute_snr_map` : mono float32, Hann 512/hop 128, 20–150 kHz, puissance dB, bruit au percentile 96 |
| Composantes | `_get_filtered_blobs_info` : étiquetage des composantes du masque seuillé ; `_extract_candidates` récupère bbox, taille, hauteur, pente et pic |
| Branche générale V4 | seuil 10 dB, taille >10, hauteur >5000 Hz, pente ≤−500 Hz/ms, largeur ≥1,33 ms, aux réglages actuels |
| Branche basse fréquence V4 | masque à 9 dB, borne haute ≤45 kHz, taille >10, hauteur ≥2000 Hz, largeur ≥3 ms, pente dans [−1500,0[ Hz/ms |
| NMS | `_time_freq_nms` : fenêtre V4 de 20 ms (distance au pic ≤10 ms), proximité fréquentielle ≤25 kHz ; priorité au pic le plus fort |
| Appariement | `benchmark.match_candidates` : contrôle temporel, distance normale carrée ≤18 kHz_eq au pic et affectation univoque par `linear_sum_assignment` |
| Graines de suivi | **Après appariement seulement dans ce benchmark** ; pic du candidat ou graine grossière large-FM si disponible avec son temps ; bornes du candidat conservées |
| Suivi | `track_reassigned_ridge` : FFT/réassignation locale, preuve spectrale mixte, graine, programmation dynamique de second ordre ; retours `None` possibles |
| Extrémités | échantillonnage sous-bin, complétion, coupe de queue faible, raffinement d'extrémité et trimming de rebond d'écho, sans changement des paramètres |
| Mesure positive | `compare_trajectory` : erreur, couverture, tolérances SNR et erreurs absolues début/fin ; agrégats recalculés par le laboratoire |
| Sorties négatives supplémentaires | `track_and_group_candidates`, puis `chirp_union.group_overlapping_chirps` : validation de courbes finies/ordonnées et regroupement/stitching existants |

La branche `low` intermédiaire calculée dans le détecteur n'est pas la liste
consommée par V4 : la véritable branche `low_v4` repart de `low_raw`. Le diagnostic
respecte cette distinction au lieu d'inventer une étape de rejet intermédiaire.

La GUI a une chaîne supplémentaire : `ridge_worker._load_modelling` préfère
`fast_modelling.process_reassigned_fast`, puis `_model_one` contrôle les bornes,
gère le repli plat, simplifie les points et appelle `ridge_stitching`. Ses
tolérances de stitching diffèrent des défauts de l'API de regroupement utilisée
ici. `annotation_validation.accepted_candidate` relève ensuite d'une acceptation
canonique/humaine. Ces étapes GUI ne sont pas exécutées ni remplacées par ce
diagnostic. Une trajectoire négative acceptée par la validation géométrique de
l'API n'est pas déclarée « validée par la GUI » ou biologiquement confirmée.

## Instrumentation optionnelle sans mutation

`prepare --diagnostic-probe` active une observation Python des appels/retours des
fonctions réellement chargées. Elle lit leurs variables locales au retour,
sans remplacer les fonctions, modifier leurs arguments, leurs listes ou leurs
tableaux. Aucun patch du détecteur ou du suivi n'est nécessaire. Elle refuse
d'écraser un traceur/debugger déjà installé et restaure le traceur même en erreur.

Les captures comprennent les listes brutes/générales/V4/NMS, les affectations,
les paramètres effectivement employés, les graines, les empreintes des tableaux,
les diagnostics de suivi déjà disponibles et les lignes/conditions source des
retours `None`. Les fichiers sources portent leur SHA-256 ; l'empreinte générale
du runner couvre aussi les modules scientifiques dépendants.

Pour chaque WAV, l'exécution vérifie sans tolérance numérique :

- le rapport scientifique natif sans observation contre celui avec observation ;
- tous les champs/tableaux des candidats natifs contre ceux observés ;
- les tableaux complets des trajectoires appariées, recalculés sans observation ;
- sur les négatifs, les tableaux/diagnostics du suivi et du regroupement complets
  sans/avec observation, y compris les erreurs si une sortie finale est invalide.

Seul le champ de profilage `timings_ms` est exclu : il ne constitue pas une
décision ou une mesure acoustique. Toute différence scientifique fait échouer
la tentative. La reconstruction signée des extrémités doit aussi reproduire
exactement les erreurs absolues déjà calculées.

Le mode figure dans le plan immuable, la clé du cache et les métadonnées du
rapport. Un cache non instrumenté ne peut servir de trace complète ; un rapport
diagnostique doit couvrir chaque WAV et contenir toutes les preuves d'équivalence.
Les campagnes précédentes restent intactes. Leur code historique est récupérable
par les checkpoints Git, sans rendre leurs rapports artificiellement actuels.

## Catégories fondées sur les observations

Les 61 références ne sont pas appariées **avant le suivi** dans le benchmark.
Aucune ne peut donc être classée « arrêt de suivi » sur la base de ce run.

| Catégorie | Preuve et limite |
|---|---|
| `matching_failure` | Au moins un candidat final est admissible selon la fonction d'appariement existante, mais la référence n'est pas retenue par l'affectation globale |
| `known_rejection` | Un candidat admissible existe à un stade observé et ne survit pas aux filtres de branche, à la largeur V4 ou à la NMS ; stage, UID, mesures et conditions échouées sont conservés |
| `no_candidate_observed` | Aucun blob brut observé n'a une bbox recouvrant le rectangle manuel, et aucun n'est admissible à l'appariement ; cela ne prouve pas l'absence de signal acoustique |
| `indeterminate` | Des bboxes brutes recouvrent la référence mais aucun pic ne satisfait l'admissibilité ; la trace ne permet pas d'attribuer une cause biologique certaine |

L'admissibilité est testée en réutilisant `match_candidates`, pas une nouvelle
distance. Le rapprochement d'un blob et d'une référence demeure une preuve
géométrique limitée : un blob voisin admissible n'est pas nécessairement le même
chirp biologique. Plusieurs rejets de branches peuvent être consignés pour une
même référence ; la catégorie principale correspond au dernier stade démontré.
Les expressions explicatives des filtres ne pilotent aucune décision.

Pour les trajectoires effectivement suivies, les arrêts sont décrits par les
diagnostics natifs (`weak_seed`, `too_few_points`, `too_short`, etc.) ou la condition
source réellement exécutée. Une erreur inconnue reste une erreur/indétermination,
jamais une cause acoustique inventée. Une sortie finale négative invalide devient
`pipeline_error` et un nombre final inconnu, pas zéro.

## Extrémités et revue dans l'analyseur

Convention : `automatique − manuel`. Au début, négatif = trop tôt ; à la fin,
positif = trop tard. Les CSV conservent signe, absolu, SNR, durée et fréquences
manuelles. Les distributions comprennent moyenne, médiane, percentiles, étendue
et comptes négatifs/nuls/positifs. Pearson et Spearman ne sont que descriptifs,
sans causalité, p-value ni indépendance présumée des chirps d'une même session.

`annotation_review_queue.csv` réutilise les colonnes vérifiées de
`bat_analyser.annotation_quality` : `wav`, `annotation`, `partition`, `session`,
`chirp_id`, `review_priority`, plus motifs et chemins sources. Dans la fenêtre de
contrôle qualité, sélectionner explicitement le manifeste du dataset si nécessaire.
L'agent n'ouvre pas la GUI et ne corrige aucune annotation.

Les négatifs n'ont pas d'identifiant manuel de chirp. Leurs trajectoires dérivées
figurent dans `negative_trajectory_review.csv` avec des identifiants diagnostiques,
les points complets et les indices des candidats sources. Elles doivent être
inspectées au niveau WAV ; elles ne sont pas injectées comme références manuelles
dans le lecteur de contrôle qualité. L'import automatique de ces trajectoires
dans la GUI n'est pas implémenté ici.

## Exécution bornée et rapports

Le diagnostic réemploie l'orchestrateur, le Job Object global de 4 Gio, quatre
workers, une seule expérience de baseline, 480 s par tentative et 600 s cumulées.
Il effectue des répétitions de non-régression sur les mêmes 28 WAV, pas une
exploration de variantes. Aucun seuil n'est modifié.

```powershell
$Python = '.\.venv\Scripts\python.exe'
$Data = "$env:USERPROFILE\Documents\Python\Python-data"
$Campaign = "$Data\development-diagnostic-v1"
& $Python -m laboratory.local_campaign prepare --campaign $Campaign --dataset "$Data\dataset-v1" --selection "$Data\development-pilot-v1-selection\selection.json" --global-memory --diagnostic-probe --workers 4 --memory-gb 4 --experiment-seconds 480 --campaign-seconds 600 --min-free-gb 2 --max-experiments 1 --max-attempts 2
if ($LASTEXITCODE -ne 0) { throw 'Preparation refusee' }
& $Python -m laboratory.local_campaign start --campaign $Campaign --max-new 1
```

Reprise : `resume --campaign $Campaign --max-new 1`, avec `--retry-failed` seulement
pour un échec, tant que les budgets le permettent. Arrêt : `stop --campaign $Campaign`.
Choisir un dossier neuf pour une nouvelle expérience, sans écraser un historique.

```powershell
$Pilot = Get-Content -Raw -Encoding utf8 "$Data\development-pilot-v1\campaign_report.json" | ConvertFrom-Json
$PilotReport = "$Data\development-pilot-v1\jobs\$($Pilot.jobs[0].job_id)\benchmark\report.json"
& $Python -m laboratory.failure_report --campaign $Campaign --pilot-report $PilotReport
```

Le rapport exige les mêmes empreintes d'entrées, paramètres et version du dépôt
scientifique que le pilote, puis une égalité exacte des agrégats et détails
scientifiques hors temps de profilage. Sorties locales privées :

- `failure_report.json` et `failure_report.html` ;
- `detection_failures.csv`, une ligne par référence non appariée ;
- `endpoint_errors.csv`, distributions et coordonnées de revue ;
- `negative_final_outputs.csv`, temps audio et dénombrements distincts ;
- les deux files de revue citées ci-dessus ;
- les rapports et caches du runner, avec les traces par WAV et preuves d'équivalence.

Validation rapide et intégration synthétique explicite :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1
.\.venv\Scripts\python.exe -m pytest -q -m integration tests/test_detection_probe.py
git diff --check
```

## Résultats du pilote diagnostique du 16 novembre 2026

La campagne `development-diagnostic-v1` est terminée sans incident : 22,026 s
de supervision globale, quatre workers, pic de mémoire engagée de
1 687 052 288 octets (1,571 Gio) pour un plafond global vérifié de 4 Gio.
L'identifiant du plan immuable est
`2fed04daac474859b79c0b4f65774efe8cb944cd74d42011a3d07ba4da0438de`.
Les empreintes d'entrées, paramètres et version du traitement correspondent
au pilote. Les résultats scientifiques détaillés sont exactement inchangés :
214 références, 153 appariées, 61 manquées. Les 28 WAV passent les comparaisons
avec/sans observation, dont les sorties suivies et regroupées des 20 négatifs.
Seuls les temps de profilage sont exclus de cette égalité.

| Dernier stade observé des manqués | 15 < SNR < 35 dB | SNR ≥ 35 dB | Total |
|---|---:|---:|---:|
| Filtres de branche | 18 | 7 | 25 |
| Largeur de la branche générale V4 | 10 | 3 | 13 |
| Suppression NMS | 6 | 7 | 13 |
| Aucun blob brut recouvrant le rectangle manuel | 9 | 1 | 10 |
| Total | 43 | 18 | 61 |

Les trois premières lignes correspondent à `known_rejection` (51 références).
Aucun échec d'affectation globale ou arrêt de suivi n'explique ces 61 manqués
dans ce benchmark. Aucun cas ne tombe dans la catégorie `indeterminate` de
cette nomenclature, mais les dix absences de blob n'ont **pas** d'explication
acoustique démontrée : la cause en amont du seuillage reste inconnue. Les
conditions de rejet peuvent être multiples pour une référence ; leurs comptes
par blob ne doivent pas être additionnés comme des comptes de chirps.

Les rappels restent 16/59 et 137/155 respectivement. Aucune référence de SNR
≤15 dB n'est disponible dans ce pilote. Les positifs sélectionnés couvrent
quatre sessions de développement (104, 66, 7 et 37 références), comme les
négatifs. La validation historique du protocole reste limitée à une session
distincte, qui n'a pas été ouverte pour ce diagnostic.

Sur 100,219333 s d'audio explicitement `no_chirp` (20 WAV), les 66 candidats
donnent 58 suivis, puis 57 trajectoires finales après validation géométrique et
regroupement de l'API native, présentes dans dix WAV. Huit suivis sont rejetés :
quatre `too_few_points`, trois `too_short`, un `weak_seed`. Aucune erreur de
regroupement n'est observée. L'acceptation finale de la GUI demeure non mesurée.

Sur les 153 chirps appariés, convention automatique moins manuel, en ms :

| Mesure | Moyenne signée | Médiane signée | P05 signé | P95 signé | Moyenne absolue | P95 absolu |
|---|---:|---:|---:|---:|---:|---:|
| Début | −0,049894 | −0,036995 | −0,250544 | 0,186436 | 0,102445 | 0,333271 |
| Fin | 0,902905 | 0,308025 | −0,129571 | 4,079815 | 0,939800 | 4,079815 |

Les débuts sont précoces dans 92 cas, exacts dans 22 et tardifs dans 39. Les
fins sont précoces dans 17 cas et tardives dans 136. Les étendues signées sont
[−1,103286 ; 0,393229] ms au début et [−0,543512 ; 5,986994] ms à la fin.
Les erreurs absolues moyennes début/fin sont 0,105324/1,030220 ms pour les
16 appariés de SNR intermédiaire, contre 0,102109/0,929240 ms pour les
137 appariés de SNR élevé. Ces chiffres décrivent uniquement les appariés,
sans renseigner les extrémités des 61 manqués.

Relations descriptives de Spearman avec les erreurs absolues :

| Caractéristique manuelle | Début | Fin |
|---|---:|---:|
| SNR maximal | −0,097 | 0,066 |
| Durée | −0,115 | 0,163 |
| Fréquence minimale | 0,239 | 0,083 |
| Fréquence maximale | 0,299 | −0,077 |

Aucune causalité ou généralisation à d'autres sessions n'est revendiquée.
Les tableaux complets, Pearson, quantiles et données individuelles sont dans
`failure_report.json` et `endpoint_errors.csv`. Les 61 références à inspecter
et leurs identifiants manuels sont dans `annotation_review_queue.csv` ; les
57 trajectoires négatives dérivées sont dans `negative_trajectory_review.csv`.
Les neuf absences de blob du WAV `20250615_175727.wav` et les fins les plus
tardives constituent des cas de revue utiles, sans conclure à une erreur
d'annotation ni modifier les seuils.

Validation exécutée sous Windows : **247 tests rapides réussis, trois exclus**
par les marqueurs ; intégration synthétique explicite : **un test réussi,
treize exclus**. `pip check` et `git diff --check` passent. Les sorties privées
restent hors du dépôt, dans `..\..\Python-data\development-diagnostic-v1`.
