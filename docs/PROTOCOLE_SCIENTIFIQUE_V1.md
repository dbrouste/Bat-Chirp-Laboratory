# Protocole scientifique des cohortes V1

## Statut et périmètre

La configuration versionnée est `experiments/scientific_protocol_v1.json`.
Son statut est **draft_requires_scientific_approval**. Les rapports et snapshots
sont descriptifs, non autorisés pour sélectionner ou promouvoir un modèle.
Le programme n'exécute ni détecteur, ni suivi, ni STFT et ne modifie pas leur score.
Il ne change aucun WAV, JSON ou manifeste du corpus. Les sources et les données
doivent rester immuables pendant l'inventaire.

Le manifeste actuel distingue `calibration` et `test`, pas trois cohortes.
Le chargeur historique `collect_frozen_dataset` reste limité à calibration ;
les agents existants continuent donc à voir toute la calibration. Le nouveau
snapshot **ne change pas leurs entrées** et n'active aucun accès au test final.
Une intégration future dans ces consommateurs exige une décision explicite.

## Constitution déterministe des cohortes

1. Vérifier les colonnes, chemins contenus dans le dataset, partitions,
   identifiants de session et empreintes déclarées du manifeste.
2. Rassembler les WAV par session telle qu'elle figure dans le manifeste.
   Une session désigne actuellement le dossier de collecte du constructeur :
   son équivalence avec une nuit/site/individu indépendant doit être attestée.
3. Relier les sessions qui partagent une empreinte audio SHA-256 déclarée ou
   un même JSON. Les composantes connexes sont indivisibles. La racine de
   composante est le plus petit nom de session par ordre lexical Python.
4. Refuser une composante chevauchant calibration et test. Ne jamais réparer
   silencieusement une fuite ni déplacer les données pour la dissimuler.
5. Conserver toutes les sessions test dans `final_test_reserved`. Seul leur
   manifeste est consulté ; aucun WAV ni JSON test n'est ouvert, même pour
   vérifier les empreintes ou calculer des statistiques descriptives.
6. Classer les composantes de calibration par SHA-256 de la liste JSON
   `[seed, nom_de_composante]`, avec la sérialisation canonique de `stable_hash`.
   La graine V1 vaut `bat-chirp-cohorts-v1`. La première composante est proposée
   en validation ; les autres en développement. Au moins une composante doit
   subsister dans chaque cohorte. Sinon le programme refuse le découpage.

Le nombre d'une composante de validation est une **convention de proposition**,
pas un ratio scientifiquement approuvé. Le programme n'équilibre pas a posteriori
le SNR ou les performances pour améliorer le découpage. Une autre graine ou
`validation_groups` dans une copie de configuration produit une autre proposition.
Toute modification du manifeste ou de la configuration change le snapshot.
L'ordre des lignes ne change pas l'affectation, mais change l'empreinte du manifeste
et donc l'identifiant de snapshot. La version V1 refuse les autres politiques.

Les clones de calibration ont déjà pu servir à l'optimisation : réserver maintenant
une partie de cette calibration ne rend pas son évaluation indépendante.
Le test historique est réservé, mais son absence d'exposition passée doit être
attestée humainement. Les doublons acoustiques transformés, extraits chevauchants,
sessions d'un même individu ou sites proches ne sont pas identifiables par les
seuls noms et SHA-256. L'indépendance écologique reste à vérifier.

## Références et contrôle des étiquettes

- Positifs : chirps `source=manual`, `status=validated`, sauf
  `annotation_quality.status=excluded`. Les propositions automatiques et les
  chirps manuels en attente ne sont pas des références positives validées.
- Négatifs : WAV explicitement `review_status=no_chirp` avec une liste de chirps
  cohérente, sans chirp manuel, validé ou édité. Les propositions automatiques
  non validées peuvent subsister. Une contradiction donne la classe `ambiguous`
  et n'entre jamais dans la cohorte négative évaluée.
- Inconnus : absence d'étiquette négative et de référence positive utilisable.
  Un WAV partiellement annoté reste `positive_partial`, sans transformer ses
  régions non annotées ou ses candidats non appariés en faux positifs.

La provenance d'export, le schéma et les unités sont contrôlés par
`verify_annotation_format`. Les JSON de calibration sont vérifiés par SHA-256,
avec correspondance exacte des membres du JSON et du manifeste. Les WAV ne sont
lus que par leur en-tête PCM (`quality_audit.wav_header`), sans re-hachage audio.
Un en-tête absent, illisible ou non pris en charge reste signalé, avec durée
manquante ; il n'est pas assimilé à une durée nulle connue.

Ce contrôle vérifie la **cohérence des étiquettes**, pas la justesse acoustique
du jugement humain. Une contre-expertise des négatifs, éventuellement avec
traçabilité du relecteur, demeure une décision de protocole.

## Mesures descriptives et réutilisation des résultats

Le rapport fournit par cohorte et session : effectifs, classes de références,
durées de WAV connues/manquantes, durées et fréquences min/max des chirps,
minima/médianes/maxima et alertes géométriques issues de `inspect_chirp`.
Ces alertes de revue ne deviennent pas des exclusions scientifiques implicites.
Les CSV conservent les valeurs individuelles, les sessions et les motifs.

Le SNR n'est pas recalculé. Sans rapport compatible, sa couverture est
**manquante**, jamais estimée depuis les noms de fichiers ou un contraste d'image.
Avec `--benchmark-report`, l'import exige :

- rapport complet, protocole `square_normals_v1`, calibration et vérification
  des empreintes annoncée par le moteur d'origine ;
- SHA-256 du manifeste identique, WAV déclarés et empreintes d'annotations
  filtrées identiques aux entrées actuelles ;
- identité WAV/chirp unique, références présentes, SNR numérique fini ;
- pour les négatifs, étiquettes actuelles cohérentes et compte de candidats
  entier non négatif, sans doublon ni erreur.

Les métriques existantes sont agrégées avec `summarize_by_snr`, sans modifier
leur calcul : SNR ≤15, 15–35, ≥35 dB ; rappel, suivi, couverture et géométrie
`kHz_eq`. Le SNR de référence provient de la STFT Flattop 512/hop 64 du benchmark,
évaluée sur la trajectoire manuelle. Les valeurs acoustiques du rendu mixte
65 % STFT / 35 % réassignation et le score de suspicion de l'audit V2 ne sont pas
interchangeables avec ce SNR. Aucun recalcul de ces audits n'est lancé ici.

Les faux déclenchements importés concernent uniquement les **candidats du
détecteur** sur les WAV négatifs vérifiés. Les rapports distinguent le nombre
évalué du nombre disponible ; les taux absents sont `null`, pas zéro. Le taux
horaire n'est fourni que si toutes les durées des négatifs évalués sont connues.
Ils ne mesurent pas encore les fausses trajectoires finales, la précision globale
sur les positifs partiels, le rejet des échos ou la confusion avec les harmoniques.

## Écarts documentation / implémentation constatés

| Sujet | Spécification géométrique v0.1 | Implémentation constatée |
|---|---|---|
| Échantillonnage | 20 points uniformes en longueur de courbe | Benchmark : 50 points uniformes en temps ; audit transverse : 20 uniformes en temps |
| Intersections | Séquence globale minimisant l'erreur sous contrainte d'ordre | Géométrie partagée : choix local minimal, progression non décroissante |
| Poids SNR | Proposition de 1 à 3 | Benchmark : 0,2 à 1, interpolation entre 15 et 35 dB |
| Tolérance | Calibration demandée en `kHz_eq` | Seuils provisoires 5 à 3, selon SNR local |
| Couverture | Normales manquantes pénalisées | Implémentée ; pénalité normalisée de 2 pour les points non couverts |
| Prolongements et fragmentation | Contrôles spécifiques et score global proposés | Pas de pénalités dédiées implémentées dans le score courant |
| Statut du document ancien | Intersections/couverture encore décrites comme futures | Déjà partiellement implémentées dans `chirp_geometry` et `benchmark/snr_metrics.py` |

Fichiers vérifiés : `chirp_geometry/__init__.py`, `laboratory/transverse_ridge.py`,
`laboratory/aggregation.py` et les modules `benchmark/snr_metrics.py`,
`benchmark/reassignment_detector_seeds.py` du clone voisin. Cette table décrit
l'état observé ; elle ne modifie ni les calculs ni la spécification historique.

## Commandes PowerShell

Depuis la racine du dépôt, rapport descriptif sans aucun benchmark :

```powershell
$Dataset = "$env:USERPROFILE\Documents\Python\Python-data\dataset-v1"
$Output = "$env:USERPROFILE\Documents\Python\Python-data\scientific-protocol-v1"
.\.venv\Scripts\python.exe -m laboratory.cohort_protocol --dataset $Dataset --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Inventaire des cohortes echoue' }
```

Pour importer un benchmark déjà calculé, ajouter par exemple :

```powershell
$Benchmark = "$env:USERPROFILE\Documents\Python\Python-data\baseline-positifs-negatifs-v1\report.json"
.\.venv\Scripts\python.exe -m laboratory.cohort_protocol --dataset $Dataset --output "${Output}-with-snr" --benchmark-report $Benchmark
```

Un rapport ancien ou incomplet peut être refusé : ne pas contourner ce contrôle
en modifiant les empreintes. Produire d'abord l'inventaire sans SNR si nécessaire.
Chaque sortie doit être un dossier nouveau situé hors du dataset. Les sorties
contiennent des informations locales du corpus ; ne pas les publier.

Fichiers générés : `cohort_snapshot.json`, `cohort_assignments.csv`,
`cohort_quality.json`, `cohort_quality.md`, `wav_quality.csv`, `chirp_quality.csv`.
Le snapshot porte son identité SHA-256, le manifeste et la configuration exacte ;
le rapport ajoute l'empreinte du code/environnement et du rapport importé.

Tests et validation Windows, uniquement sur données synthétiques :

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_cohort_protocol.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\validate_local.ps1
```

## Décisions scientifiques encore requises

1. Approuver le nombre de sessions indépendantes de validation, la graine et la
   définition réelle d'une session ; décider si de nouvelles sessions non exposées
   sont nécessaires à la validation et au test final.
2. Attester l'historique d'exposition et la qualité des étiquettes `no_chirp` ;
   définir une contre-expertise et une couverture négative représentative.
3. Fixer les effectifs et couvertures minimaux par SNR, durée, fréquence et contexte
   d'enregistrement. Les descriptifs actuels ne créent pas de seuil d'acceptation.
4. Arbitrer les écarts de géométrie et la calibration des tolérances avant toute
   modification versionnée des scores, ainsi que les mesures de faux suivis finaux.
5. Définir la règle de sélection, l'incertitude par session et la procédure unique
   d'ouverture du test final. Aucun classement ni promotion n'est autorisé ici.
