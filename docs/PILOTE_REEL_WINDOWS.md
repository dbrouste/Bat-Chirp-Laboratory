# Premier pilote réel : développement uniquement

## État Git et périmètre

L'étape 3 a été revérifiée (220 tests réussis, deux intégrations exclues), puis
sauvegardée dans le commit local `61aa16b`, tag `checkpoint/local-orchestrator-v1`.
L'intégration minimale du pilote est sur `feat/development-real-pilot`, sans push.
Les autres fichiers locaux, notamment `bat_chirp_geometry.egg-info/`, restent intacts.
Le dépôt de traitement était propre au commit `db919efb8db0bca3692385383762b50e9cd3ae16`.

La CLI de l'étape 3 ne pouvait sélectionner des WAV avant le chargement de toute
la calibration. L'option `prepare --selection` ajoute un filtre contrôlé au
chargeur existant, transmis au runner et aux contrôles de reprise. Les calculs
scientifiques ne changent pas. Aucun corpus de remplacement n'est construit :
aucun WAV ni JSON d'annotation n'est copié, modifié ou déplacé.

## Sélection déterministe

`laboratory.pilot_selection` vérifie l'identité du snapshot V1 et du manifeste.
Seuls les JSON de sessions affectées à `development` sont consultés pour vérifier
les étiquettes. Les WAV et annotations de validation et du test ne sont pas ouverts.
Les métadonnées d'affectation des autres cohortes servent seulement au contrôle
d'appartenance. Les éventuels SNR historiques servent uniquement à la stratification
des WAV de développement, jamais à une nouvelle mesure ni à un choix algorithmique.

Le tirage utilise une graine fixe et un parcours déterministe des groupes
session/strates SNR. Les inconnus ne sont jamais des négatifs ; un `no_chirp`
contenant un chirp manuel, validé ou édité bloque la sélection. Le filtre est
vérifié à nouveau avant tout chargement WAV et les JSON restent intégraux, même
si seulement certains WAV de leur session sont sélectionnés.

Effectifs disponibles : **8 WAV positifs**, 184 `no_chirp`, sur quatre sessions
de développement. Les 20 positifs demandés ne sont donc pas disponibles.
Le pilote retient tous les 8 positifs et 20 négatifs : aucune compensation depuis
la validation ou le test, aucun doublon inventé. Les négatifs se répartissent
en 6/6/6/2 WAV entre les quatre sessions, selon les effectifs disponibles.

Le fichier `selection.json` contient les demandes, disponibilités, effectifs
retenus, affectations, chemins sources du manifeste et empreintes. Toute modification
du manifeste, du snapshot, du fichier de sélection ou des données sélectionnées
invalide la campagne. Aucun WAV non sélectionné n'est ouvert pour vérification
ou pour calcul. Les étiquettes sont vérifiées structurellement, sans contre-expertise
acoustique supplémentaire des décisions humaines.

## Limites globales et vérification mémoire

Le plan utilise une baseline actuelle seule, quatre workers maximum, 480 secondes
par tentative, 600 secondes cumulées, deux tentatives au maximum et un garde-fou
de 2 Gio libres sur le disque. Aucun fichier de variantes n'est fourni.

`prepare --global-memory --memory-gb 4` place le superviseur expérimental et
tous ses descendants dans un Job Object externe limité collectivement à 4 Gio
de mémoire engagée. Les Jobs par tentative restent imbriqués dans ce Job global.
Le petit lanceur extérieur, qui ne lit pas le corpus, ainsi que PowerShell et
l'OS, ne font pas partie de cette limite. Ce n'est pas une limite de RAM physique.
Les allocations/refus, l'affectation au Job et la limite relue sont vérifiés avant
le calcul. Une affectation non garantie provoque un échec, pas une exécution libre.

Les sessions globales ont leur réservation temporelle et leur journal dans
`global_runs/`. Une session achevée est facturée à sa durée mesurée ; une session
dont la durée est inconnue après crash consomme sa réservation. La reprise reste
soumise au budget total restant, sans augmentation automatique.

Un nouveau test multiprocessus a montré qu'après un refus d'allocation,
`PeakJobMemoryUsed` pouvait dépasser le plafond configuré. Il ne faut pas en
déduire seul qu'une allocation au-delà de la limite a réussi. Le test vérifie
maintenant indépendamment le refus, la mémoire privée engagée avant/après de
chaque enfant et leur somme au repos avec celle du parent. La limite demeure
active ; le test ne la désactive ni ne l'augmente. Les pics bruts Windows restent
conservés avec leur portée, sans être présentés comme un historique exact du RSS.

Références : [limite d'engagement du Job](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information),
[compteurs de pic](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information),
[discussion Microsoft sur les allocations refusées](https://learn.microsoft.com/en-us/answers/questions/6054259/windows-job-object-can-a-rejected-memory-commit-in).
Le rapport du pilote exige une preuve de limite globale relue, d'affectation
des processus et une mesure disponible. Il refuse de présenter un pilote achevé
comme vérifié si ces éléments manquent.

## Résultats observés

Campagne conservée localement dans `Python-data/development-pilot-v1` ; sélection
dans `Python-data/development-pilot-v1-selection`. Les rapports privés ne sont pas
ajoutés à Git. Seuls les chemins présents dans le manifeste sont utilisés pour
la liste de revue ; les WAV originaux n'ont pas été ouverts pour vérifier leur
emplacement actuel. Le chemin de la copie figée vérifiée est également fourni.

| Mesure | Résultat |
|---|---:|
| WAV positifs / négatifs | 8 / 20 |
| Références positives / appariées / suivies | 214 / 153 / 153 |
| Rappel brut / pondéré existant | 71,50 % / 77,09 % |
| Erreur géométrique moyenne sur les trajectoires mesurées | 0,967 kHz_eq |
| Erreur fréquentielle verticale moyenne sur les trajectoires mesurées | 0,931 kHz |
| Erreur absolue moyenne de début / fin | 0,102 / 0,940 ms |
| Couverture géométrique moyenne mesurée | 96,42 % |
| Fraction du pipeline dans la tolérance existante | 66,21 % |
| Alertes candidates sur les négatifs | 66 dans 10 des 20 WAV |
| Durée totale des négatifs | 100,219 s |
| Fausses trajectoires finales | Indisponibles, jamais assimilées aux 66 candidats |

| Classe SNR | Références | Appariées | Rappel descriptif |
|---|---:|---:|---:|
| ≤15 dB | 0 | 0 | Indisponible |
| 15–35 dB | 59 | 16 | 27,12 % |
| ≥35 dB | 155 | 137 | 88,39 % |

Le faible SNR est absent : aucune conclusion de performance dans cette classe.
Ces valeurs exploratoires ne sont pas une validation indépendante. Les tolérances
géométriques demeurent provisoires ; les WAV positifs restent partiellement annotés.
La densité horaire calculée par le benchmark provient seulement d'environ 100 s
de négatifs et ne doit pas être extrapolée à un corpus représentatif.

La tentative scientifique a duré 9,07 s, avec 10,46 s pour la première supervision
globale (vérifications comprises). Le pic brut Windows était de 1685385216 octets,
environ 1,57 Gio sous la limite collective de 4 Gio. Aucun incident dans ce pilote.
La vérification de reprise n'a ajouté aucune tentative scientifique et a conservé
le rapport baseline SHA-256 :
`537438c4d9f4d2c6a0d75768f7a13801f58c37f1927737824dd5316d1956fe89`.

## Rapports et commandes de reprise

Les rapports consolidés sont `pilot_report.json`, `pilot_metrics.csv` et
`pilot_report.html`. `manual_review_cases.csv` contient 109 cas avec les chemins
des WAV originaux, des copies figées et des JSON sources. Les critères de revue
sont les heuristiques déjà présentes dans l'optimiseur, plus les alertes candidates
sur les négatifs explicites. Ils ne constituent pas des exclusions automatiques.
Les journaux, tentatives et rapports détaillés du runner restent dans la campagne.

Reprise de la campagne effectivement exécutée, sans refaire une baseline valide :

```powershell
$Python = '.\.venv\Scripts\python.exe'
$Data = "$env:USERPROFILE\Documents\Python\Python-data"
$Campaign = "$Data\development-pilot-v1"
& $Python -m laboratory.local_campaign status --campaign $Campaign
& $Python -m laboratory.local_campaign resume --campaign $Campaign --max-new 1
& $Python -m laboratory.pilot_report --campaign $Campaign
```

Pendant un calcul, `stop --campaign $Campaign` demande un arrêt propre.
Un échec exige `resume --retry-failed --max-new 1`, seulement si du temps et des
tentatives restent disponibles. Aucun timeout n'autorise à augmenter discrètement
les budgets du plan.

## Reproduire dans de nouveaux dossiers

Les dossiers de sélection et de campagne sont immuables : choisir un nom neuf.
Les commandes suivantes reproduisent le même choix déterministe, sans modifier
les données sources ; elles lancent une nouvelle mesure de baseline bornée.

```powershell
$Python = '.\.venv\Scripts\python.exe'
$Data = "$env:USERPROFILE\Documents\Python\Python-data"
$Selection = "$Data\development-pilot-repeat-selection"
$Campaign = "$Data\development-pilot-repeat"
& $Python -m laboratory.pilot_selection --dataset "$Data\dataset-v1" --snapshot "$Data\scientific-protocol-v1-reviewed\cohort_snapshot.json" --output $Selection --snr-report "$Data\baseline-positifs-negatifs-v1\report.json" --positives 20 --negatives 20
if ($LASTEXITCODE -ne 0) { throw 'Selection refusee' }
& $Python -m laboratory.local_campaign prepare --campaign $Campaign --dataset "$Data\dataset-v1" --selection "$Selection\selection.json" --global-memory --workers 4 --memory-gb 4 --experiment-seconds 480 --campaign-seconds 600 --min-free-gb 2 --max-experiments 1 --max-attempts 2
if ($LASTEXITCODE -ne 0) { throw 'Preparation refusee' }
& $Python -m laboratory.local_campaign start --campaign $Campaign --max-new 1
& $Python -m laboratory.pilot_report --campaign $Campaign
```

Validation locale finale : **233 tests réussis, deux intégrations exclues**, en
22,67 s, avec `pip check` réussi. Les tests synthétiques démontrent notamment
le refus des fichiers de validation/test, les effectifs insuffisants, les étiquettes
contradictoires, la sélection avant lecture WAV et la mémoire collective.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1
git diff --check
```
