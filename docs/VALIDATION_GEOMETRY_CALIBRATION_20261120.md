# Évaluation A/B des corrections géométriques — calibration du 20 novembre 2026

## Objectif et périmètre

Comparer l'effet des modifications de la calibration temporelle et de l'appariement des normales, **sans régler le détecteur ni ouvrir la partition de test**.

- Exécution : [Bat-Lab-Control / GitHub Actions #44668877700](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44668877700) (dépôt privé).
- Test pilote : [#44668674916](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44668674916).
- Avant : `cc925d2e2e1c542be78488454e6819b759101bee` (correction des workers incluse, avant correctifs géométriques).
- Après : `7ec740313af65a2c10f4926bc1a233c4aaa4ed70` (normalisation temporelle continue et appariement global).
- Détecteur externe : instantané Git **identique** `db919efb8db0bca3692385383762b50e9cd3ae16`.
- Manifeste figé : SHA-256 `de326bd6f9aeb25d7792968db6ad46b5f0a7eeb8c7ae4dbad87e046810a03e31`.
- Paramètres de traitement, entrées WAV/JSON et empreintes vérifiés et identiques entre les deux exécutions.
- Sorties détaillées conservées localement sous `Python-data/lab-geometry-comparisons/run-44668877700/` : `before/report.json`, `after/report.json`, `comparison.json`.

Le mode `laboratory.frozen_dataset` lit uniquement les annotations et WAV de **calibration**, jamais ceux du jeu `test`. Les 428 WAV du manifeste de calibration comprennent 37 WAV évalués avec références positives, 203 WAV explicitement revus `no_chirp` évalués, et 188 WAV de statut inconnu non utilisés. **240 WAV évalués** ; aucun fichier en erreur.

## Mesures agrégées

| Indicateur | Avant | Après |
|---|---:|---:|
| Références manuelles validées | 493 | 493 |
| Candidats émis (toutes cohortes) | 1 831 | 1 831 |
| Références associées à un candidat | 401 | 401 |
| Références suivies | 401 | 401 |
| Rappel pondéré | 0,853094 | 0,853094 |
| Score de suivi pondéré | 0,758604 | 0,758922 |
| Score de suivi non pondéré | 0,724097 | 0,724422 |
| Erreur géométrique moyenne (kHz_eq) | 1,167093 | 1,167683 |
| Couverture géométrique moyenne | 0,952618 | 0,953067 |
| WAV `no_chirp` avec au moins un candidat | 108 / 203 | 108 / 203 |
| Nombre de candidats sur WAV `no_chirp` | 677 | 677 |

Dans le fichier comparatif par référence, **17** valeurs d'erreur géométrique, **6** fractions de tolérance et **6** valeurs de couverture ont changé. Aucun changement d'état `detector_matched`, `tracking_success` ou d'indice de candidat.

Les scores géométriques **ne sont pas strictement comparables en qualité** : les deux versions utilisent des définitions différentes de la métrique géométrique. Une variation de score ne prouve donc pas une amélioration du détecteur.

## Analyse par SNR

| Groupe | Références | Associées | Rappel brut |
|---|---:|---:|---:|
| ≤ 15 dB | 2 | 0 | 0 % (effectif insuffisant) |
| Entre 15 et 35 dB | 131 | 67 | 51,1 % |
| ≥ 35 dB | 360 | 334 | 92,8 % |

Dans ces références, **64 chirps de SNR moyen** et **26 de fort SNR** restent non associés. Ces chiffres concernent les références validées, pas l'ensemble des appels présents dans les WAV positifs (dont l'annotation n'est pas exhaustive).

Sur les fichiers explicitement `no_chirp`, 108/203 (53,2 %) déclenchent au moins un candidat. Il s'agit de **candidats du détecteur**, non de faux chirps complets reconstruits. On ne peut pas en déduire une précision globale.

## Interprétation et décision

- **Validation technique** : pas de régression de la détection ou du suivi observée sur cette calibration.
- **Validation scientifique** : pas de gain acoustique démontré ; la correction sécurise la cohérence des distances et l'assignation des normales.
- **Priorités** : analyser les références manquées à fort et moyen SNR, puis les candidats sur la cohorte `no_chirp` ; fixer une méthode de validation et des seuils *sur calibration uniquement*.
- **Limite du corpus** : seules deux références à ≤ 15 dB ; constituer une calibration basse-SNR plus représentative avant de prétendre couvrir ce régime.
- **Jeu `test`** : conserver strictement scellé jusqu'à une évaluation finale indépendante après gel des paramètres et de l'algorithme.

Le connecteur de benchmark utilise `bat_full_spectrum_processing`, qui n'est pas garanti identique à la version actuellement utilisée par Bat Acoustic Analyser. Les conclusions s'appliquent uniquement à la chaîne évaluée.
