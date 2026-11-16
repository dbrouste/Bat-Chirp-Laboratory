# Campagnes locales Windows : supervision du runner existant

## Sauvegarde Git des étapes précédentes

Les modifications initialement non commitées ont été séparées sans modifier le
contenu des fichiers de travail :

| Étape | Commit local | Repère stable |
|---|---|---|
| Empreintes et validation Windows | `f082e40` | `checkpoint/reproducibility-v1` |
| Cohortes exploratoires et rapport de qualité | `b11b0df` | `checkpoint/scientific-protocol-v1` |

Le second commit dépend du premier. Leurs ajouts au README ont été indexés
séparément. Aucun stash, reset, suppression ou push n'a été effectué.
`bat_chirp_geometry.egg-info/`, déjà non suivi avant ces étapes, reste intact
et hors commits. Les rapports privés déjà produits dans `Python-data` sont
conservés sur disque et ne font pas partie de ces commits de code.

Vérifications sans changer le checkout :

```powershell
git show --stat checkpoint/reproducibility-v1
git show --stat checkpoint/scientific-protocol-v1
```

Pour retrouver une étape dans un nouveau dossier, sans toucher aux modifications
actuelles, choisir un dossier qui n'existe pas :

```powershell
git worktree add --detach ..\Bat-Chirp-step1 checkpoint/reproducibility-v1
git worktree add --detach ..\Bat-Chirp-step2 checkpoint/scientific-protocol-v1
```

L'implémentation de supervision reste sur `feat/local-experiment-orchestrator`.
Elle n'est pas commitée automatiquement avec les étapes précédentes.

## Intégration et limites scientifiques

Une seule entrée utilisateur : `python -m laboratory.local_campaign COMMANDE`.
Le mode historique d'audit des annotations avec `--dataset ... --output ...`
reste disponible. `campaign_control.py` ajoute la supervision des expériences
de détection à cette entrée et utilise le même fichier `campaign.sqlite` et
la même connexion SQLite (WAL et délai d'attente). Les tables `detector_*`
complètent les tables historiques ; il ne s'agit pas d'un second moteur de calcul.
Les nouveaux budgets s'appliquent aux sous-commandes de supervision ; ils ne
transforment pas le mode historique d'audit ni les anciens agents en tâches bornées.

Le calcul reste exclusivement dans `runner.run`, le connecteur de benchmark et
les modules scientifiques existants. Les conversions de métriques et les
garde-fous diagnostiques sont ceux de `autonomous_optimizer.metrics/judge`.
Le cache par WAV et les rapports détaillés restent ceux du runner. Les anciens
agents ne sont ni supprimés ni automatiquement exécutés par cette commande.

Les expériences sont **séquentielles** ; le runner emploie quatre workers par
défaut (configurable de 1 à 8). Les threads BLAS sont limités à un par processus.
Les paramètres variables sont ceux du détecteur déjà explorés par l'agent :
largeur minimale, marge de suivi, FFT, hop et trimming d'écho. Le mode géométrique,
les poids du score et la sélection des références ne peuvent pas varier.

La partition utilisée est toute la **calibration exploratoire**. Le snapshot
développement/validation V1 n'est pas présenté comme indépendant et ne modifie
pas silencieusement les entrées du runner. Les rapports rappellent le manque de
faibles SNR et la validation historique limitée à une session. Aucun gagnant
n'est promu, aucun code du détecteur n'est modifié, aucun commit ni push n'est
réalisé par la supervision.

Le chargeur refuse les sessions ou empreintes audio déclarées partagées entre
calibration et test, ainsi que les chemins de calibration résolus ailleurs que
dans ses dossiers WAV/annotations. Le manifeste test est lu pour ces contrôles,
mais **aucun fichier WAV ou JSON du test réservé n'est ouvert**.
Les négatifs sont uniquement les WAV explicitement `no_chirp`, sans contradiction
avec un chirp manuel, édité ou validé. Les autres zones restent inconnues.
Les alertes candidates négatives sont distinguées des fausses trajectoires finales,
dont la métrique reste `null` : le benchmark ne permet pas de la calculer.

## Garanties et budgets opérationnels

| Budget | Comportement |
|---|---|
| Workers | Quatre par défaut, une seule expérience à la fois |
| Temps d'une tentative | Watchdog mural, 600 s par défaut, vérification toutes les 100 ms |
| Temps cumulé | 3600 s par défaut pour les sous-processus supervisés, vérifications comprises |
| Mémoire | 16 Gio par défaut, limite Windows de mémoire **engagée** du Job Object et de ses descendants |
| Expériences | Au plus 12 jeux de paramètres distincts par défaut, baseline comprise |
| Tentatives | Au plus trois tentatives par expérience, interruptions comprises |
| Disque | Contrôle avant lancement et toutes les 100 ms ; arrêt si l'espace libre passe sous 1 Gio par défaut |

La mémoire n'est pas une mesure du RSS, ni une limite globale de RAM du PC.
La limite exclut le superviseur lui-même et la préparation du plan. Le Job Object
est configuré puis interrogé pour vérifier l'application de sa limite. Le worker
attend un signal avant tout accès au corpus/calcul, après son affectation au Job.
Ses descendants héritent de ce Job. Sa fermeture ou sa terminaison tue l'arbre.
Si Windows refuse la création, l'affectation ou l'interrogation du Job, la tentative
échoue explicitement au lieu de continuer sans confinement.

Une erreur d'allocation peut se manifester par `MemoryError` dans le journal ;
un code de sortie non nul n'est pas arbitrairement attribué à la mémoire.
Le pic de mémoire engagée mesuré par Windows est conservé par tentative.

Le watchdog est une supervision logicielle : la terminaison peut dépasser
l'échéance à cause de l'ordonnancement Windows et du délai d'arrêt. Ce n'est pas
une garantie temps réel. Le budget cumulé couvre les processus de vérification
et de calcul, pas les pauses, l'attente de l'utilisateur, la création initiale du
dataset synthétique ou les petits exports effectués par le superviseur.

Le seuil disque est un **garde-fou**, pas une réservation ni un quota strict :
un autre processus ou une écriture entre deux contrôles peut consommer l'espace.
`--max-output-gb` est volontairement refusé avec une erreur explicite, car ce
programme ne configure pas de quota système. Ne pas le présenter comme garanti.
De même, aucune limite de RAM physique globale ni de temps réel strict n'est annoncée.
Les états d'échec restent écrits tant que le volume peut accepter les écritures ;
un volume complètement plein ne permet pas de garantir l'écriture du dernier log.

Les mécanismes utilisés correspondent aux Job Objects documentés par Microsoft :
[groupes de processus](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
et [limites et mesures de mémoire](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information).

## Préparer puis lancer une campagne

Depuis la racine du dépôt, préparer un fichier JSON de variantes, par exemple :

```json
[
  {"name": "largeur-1ms", "options": {"v4_general_min_width_ms": 1.0}},
  {"name": "marge-3ms", "options": {"search_margin_ms": 3.0}}
]
```

Les commandes suivantes sont des exemples pour une campagne réelle ultérieure,
pas des opérations exécutées pendant cette implémentation. La préparation vérifie
les empreintes de calibration et peut donc effectuer beaucoup de lectures disque.

```powershell
$Python = '.\.venv\Scripts\python.exe'
$Dataset = "$env:USERPROFILE\Documents\Python\Python-data\dataset-v1"
$Campaign = "$env:USERPROFILE\Documents\Python\Python-data\campaign-example-v1"
& $Python -m laboratory.local_campaign prepare --campaign $Campaign --dataset $Dataset --variants .\variants.json --workers 4 --experiment-seconds 600 --campaign-seconds 3600 --memory-gb 16 --min-free-gb 2 --max-experiments 3
& $Python -m laboratory.local_campaign start --campaign $Campaign --max-new 1
```

La baseline est toujours le premier calcul. `--baseline-options fichier.json`
permet de figer d'autres paramètres initiaux lors de la préparation seulement.
Les options, limites, versions, empreintes du code, des données et du protocole,
ainsi que les identifiants Git sont enregistrés dans `definition.json` et SQLite.
Le plan ne peut être modifié après préparation. Changer le code, les dépendances,
les données ou le protocole impose une nouvelle campagne.

Depuis un second terminal PowerShell, suivi et arrêt :

```powershell
& $Python -m laboratory.local_campaign status --campaign $Campaign
& $Python -m laboratory.local_campaign status --campaign $Campaign --watch
& $Python -m laboratory.local_campaign stop --campaign $Campaign
```

`stop` publie une demande asynchrone ; `status` confirme l'arrêt. Ctrl+C dans le
terminal du superviseur interrompt également la tentative et son arbre de processus.
Un verrou de fichier Windows empêche deux superviseurs d'écrire simultanément.
Ce verrou est libéré par Windows à la mort du processus ; son fichier persistant
n'est pas un verrou PID périmé à supprimer manuellement.

```powershell
& $Python -m laboratory.local_campaign resume --campaign $Campaign
& $Python -m laboratory.local_campaign resume --campaign $Campaign --retry-failed
```

Seule `resume` acquitte la demande STOP. Les interruptions reprennent automatiquement ;
les échecs/timeouts exigent `--retry-failed`. Les budgets restent ceux du plan,
et ne peuvent pas être augmentés discrètement à la reprise.

## Reprise et baseline fixe

- Avant chaque lancement/reprise, un processus borné vérifie le code, le protocole
  et les empreintes de calibration, sans consulter les fichiers du test.
- Une réservation de temps est persistée avant chaque lancement. Après un crash,
  une durée inconnue est facturée à hauteur de cette réservation, jamais à zéro.
- Les tentatives inachevées sont conservées et marquées interrompues à la reprise.
  Les logs sont distincts par tentative. Le worker ne délivre son reçu qu'après
  la fin du runner et une nouvelle vérification des entrées et du code.
- Un reçu, un rapport complet, ses paramètres et ses empreintes doivent correspondre.
  Une expérience terminée valide est sautée ; ses WAV ne sont pas recalculés.
- Une tentative interrompue peut réutiliser les caches WAV atomiques valides du
  runner. Les caches en erreur/vides et les rapports incomplets ne sont pas acceptés.
- La baseline porte une identité de code/données/paramètres et le SHA-256 exact
  de son rapport. Une baseline terminée manquante ou altérée bloque la reprise ;
  elle n'est jamais remplacée silencieusement. Une variante altérée peut être
  recalculée dans la limite des tentatives autorisées.
- `status` signale les rapports altérés et les superviseurs disparus sans transformer
  un historique interrompu en expérience réussie.

Une reprise d'une campagne déjà complète ne relance que la vérification des
entrées. Elle consomme un peu du budget supervisé, sans refaire le benchmark.
Un arrêt brutal avant l'affectation initiale au Job peut laisser le petit worker
en attente du signal ; il expire après 30 s et n'accède pas au corpus.
Les fichiers sources ne doivent pas être modifiés pendant un calcul.

## Petit batch synthétique de bout en bout

Le mode `demo` génère deux WAV de 50 ms (un chirp FM, un silence explicitement
`no_chirp`) dans un dossier voisin neuf. Il utilise le vrai runner et le clone
voisin `bat_full_spectrum_processing`. Ce test vérifie la plomberie, pas une
performance scientifique représentative. Il ne nécessite aucun corpus privé.

```powershell
$Python = '.\.venv\Scripts\python.exe'
$Campaign = '.\laboratory-results\orchestrator-demo-new'
& $Python -m laboratory.local_campaign demo --campaign $Campaign --workers 4 --memory-gb 2 --max-new 1
& $Python -m laboratory.local_campaign status --campaign $Campaign
& $Python -m laboratory.local_campaign resume --campaign $Campaign
& $Python -m laboratory.local_campaign resume --campaign $Campaign
```

Attendu : pause à 1/2, puis complétion à 2/2, puis reprise sans nouveau calcul
scientifique ni modification de la baseline. Choisir un nom de dossier neuf :
ni les campagnes ni leurs entrées synthétiques ne sont écrasées.
Sans clone de traitement compatible, le worker échoue explicitement et conserve
le journal ; le programme n'invente pas de résultat de remplacement.

## Rapports et validation

Chaque campagne contient :

- `definition.json` : plan immuable et baseline identifiée ;
- `campaign.sqlite` : état, paramètres, résultats et toutes les tentatives ;
- `campaign_report.json`, `campaign_scores.csv`, `campaign_report.html` : progression,
  durées mesurées/temps facturé, erreurs, mémoire, métriques et écarts à la baseline ;
- `attempts/<id>/execution.log` et `receipt.json` pour les tentatives achevées ;
- `jobs/<id>/benchmark/` : caches, manifestes, rapports HTML/JSON et historique du runner.

Les rapports sont locaux et ne nécessitent aucun CDN. Les gates diagnostiques
existants ne constituent ni une validation indépendante ni une décision de promotion.
Une erreur renvoie un code non nul ; pause volontaire et complétion renvoient zéro.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_campaign_control.py tests/test_windows_resources.py tests/test_frozen_dataset.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1
git diff --check
```

Les tests rapides utilisent des fichiers temporaires et de petits sous-processus
Windows pour vérifier limites, interruption, descendants, intégrité et reprise.
Les intégrations scientifiques externes restent exclues par la validation rapide ;
le mode `demo` constitue leur vérification synthétique explicite pour cette étape.

## Vérification réalisée pour cette étape

Validation Windows : **220 tests réussis, 2 intégrations exclues**, en 26,02 s ;
`pip check` et `git diff --check` réussis. Les tests Windows vérifient notamment
le refus d'une allocation dépassant le plafond, le timeout, la demande d'arrêt,
la terminaison d'un descendant, l'exclusion mutuelle et le garde-fou disque.

Le batch réel du runner, exclusivement synthétique, est conservé dans
`laboratory-results/orchestrator-verified-v1/`. Avec quatre workers configurés et
une limite de 2 Gio : pause à 1/2, reprise à 2/2, puis seconde reprise sans calcul
scientifique supplémentaire. Les deux tentatives scientifiques restent au nombre
de deux et le SHA-256 de la baseline reste
`a79269f51d9e9686231c22dc702429d2188ce4e516d68c20130ff5b3f7698c99`.
Les pics de mémoire engagée mesurés sont 310112256 et 343842816 octets.
Ces mesures sur deux WAV de 50 ms ne préjugent pas du coût d'un corpus réel.
