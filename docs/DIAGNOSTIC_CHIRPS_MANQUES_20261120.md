# Diagnostic des chirps manqués — calibration uniquement (20 novembre 2026)

## Contexte et provenance

Après les correctifs géométriques, le benchmark a été figé sur :

- Laboratoire : `7ec740313af65a2c10f4926bc1a233c4aaa4ed70`.
- Détecteur externe : `db919efb8db0bca3692385383762b50e9cd3ae16`.
- Manifeste calibration : SHA-256 `de326bd6f9aeb25d7792968db6ad46b5f0a7eeb8c7ae4dbad87e046810a03e31`.
- [Benchmark complet #44668877700](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44668877700).
- [Triage du rapport #44670028440](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44670028440).
- [Rejeu exact des candidats #44670451953](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44670451953).
- [Sensibilité aux seuils d'association #44670730732](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44670730732).

**Aucun WAV ni JSON de la partition `test` n'est ouvert.** Les nouvelles analyses utilisent exclusivement la partition calibration ; les WAV et annotations sont consultés en lecture seule. Les fichiers détaillés et les chemins vers les WAV/JSON originaux restent sur le PC du runner, sans publication des identités individuelles dans les logs GitHub.

## État des références positives

- 37 WAV avec au moins une référence manuelle validée, contenant 493 chirps.
- 401 références associées à un candidat ; 401 suivies.
- **92 références non associées** sur 18 WAV ; aucun échec de suivi parmi les références associées.
- Dans les 37 WAV positifs : 1 154 candidats au total. **Ces candidats ne doivent pas être assimilés à des faux positifs**, puisque les annotations ne sont pas exhaustives.

| SNR manuel | Références | Non associées | Associées |
|---|---:|---:|---:|
| ≥ 35 dB | 360 | 26 | 334 |
| 15 < SNR < 35 dB | 131 | 64 | 67 |
| ≤ 15 dB | 2 | 2 | 0 |

## Rejeu du détecteur — cause immédiate des non-associations

Le rejeu a utilisé la **même implémentation** `detect_candidates_snr_blobs` et la même fonction `match_candidates` que le benchmark, avec le même état Git du détecteur et de la géométrie. Chaque WAV sélectionné a été validé par ses empreintes et les identifiants manuels ; 670 candidats et 261 associations ont été retrouvés à l'identique sur les 18 WAV concernés.

| Condition constatée pour un chirp non associé | ≥ 35 dB | 15–35 dB | ≤ 15 dB | Total |
|---|---:|---:|---:|---:|
| Aucun pic candidat dans la fenêtre temporelle annotée ± 1,5 ms | 17 | 16 | 0 | **33** |
| Pic dans la fenêtre, mais aucun ne satisfait le rayon géométrique de 18 kHz_eq | 9 | 48 | 2 | **59** |
| Un pic admissible existe, mais l'appariement un-à-un l'écarte | 0 | 0 | 0 | **0** |

Aucun cas ne tombe uniquement sur la condition de distance au centre temporel. **La contrainte d'appariement un-à-un n'explique donc aucun des 92 manqués** sur ce corpus.

Attention : dans les 59 cas « distance », les pics situés dans la fenêtre temporelle peuvent provenir d'autres appels, d'échos ou d'interférences. Leur présence ne prouve pas que le vrai chirp avait été détecté.

## Sensibilité exploratoire du rayon et de la marge temporelle

Le détecteur reste strictement **inchangé**. Sur les 18 WAV concernés, les associations ont été recalculées pour différentes valeurs de `max_seed_error_khz` et `time_slack_ms` :

| Marge temporelle | Rayon géométrique | Associations | Nouveaux chirps associés | Associations précédentes perdues ou réattribuées |
|---|---:|---:|---:|---:|
| 1,5 ms | 18 kHz_eq (référence) | 261 | 0 | 0 |
| 1,5 ms | 22 kHz_eq | 261 | 0 | 0 |
| 1,5 ms | 30 kHz_eq | 262 | 1 (SNR moyen) | 0 |
| 3 ms | 18 kHz_eq | 261 | 0 | 0 |
| 3 ms | 22 kHz_eq | 261 | 0 | 0 |
| 3 ms | 30 kHz_eq | 262 | 1 (SNR moyen) | 0 |

**Décision : ne pas assouplir le critère géométrique en production.** Le gain potentiel est très faible, et la véracité acoustique de la nouvelle association n'a pas été vérifiée. Les causes probables doivent être explorées dans la génération et la localisation des candidats et, pour les cas atypiques, dans la qualité des références manuelles.

## Sous-corpus explicitement négatif `no_chirp`

Sur les 203 WAV étiquetés `no_chirp`, 108 produisent au moins un candidat, pour 677 candidats en tout. Sur ces 203 WAV :

- 62 en produisent au moins 5 ;
- 20 en produisent au moins 10 ;
- 3 en produisent au moins 20 ;
- le maximum est de 35 candidats sur un WAV ;
- les 10 WAV les plus actifs concentrent 27,6 % des 677 candidats.

Ces mesures concernent **les alertes au niveau des candidats**, pas les trajectoires complètes, et ne sont pas un taux de faux positifs global.

## Fichiers de revue sur le PC Windows

Sous `Python-data/lab-geometry-comparisons/run-44668877700/` :

- `diagnostic-v1/summary.json` : effectifs et répartition des manqués.
- `diagnostic-v1/missed_reference_review.csv` : file de 92 références à inspecter.
- `diagnostic-v1/reviewed_negative_candidates.csv` : les 203 WAV négatifs classés par nombre de candidats.
- `candidate-gates-v1/candidate_gate_review.csv` : chaque référence manquée, cause selon les critères exacts, distance minimale et chemins WAV/JSON originaux.
- `candidate-gates-sensitivity-v1/matching_sensitivity.csv` : balayage exploratoire des seuils.

Ces fichiers sont destinés à organiser la **revue humaine des signaux**, pas à automatiquement corriger les annotations.

## Étapes proposées

1. **Priorité 1 : 26 références à SNR ≥ 35 dB**, dont 17 sans pic temporel proche et 9 avec un pic trop distant en géométrie. Vérifier visuellement la référence et les candidats sur spectrogramme ; classer les causes (annotation, seuil de candidat, localisation, artefact, autre).
2. **Priorité 2 : 64 références à SNR moyen**, dont 48 où un candidat existe dans la fenêtre sans être suffisamment proche de la courbe. Tester seulement ensuite, sur calibration, les changements de paramètres de génération des candidats, en gardant le rappel fort SNR comme garde-fou.
3. **Priorité 3 : WAV `no_chirp` les plus actifs** pour comprendre si les candidats sont dus à du bruit, des lignes spectrales, des échos ou des structures acoustiques suspectes.
4. **Ne pas ouvrir le jeu test** pendant ces investigations ; réserver l'évaluation finale à une version gelée du détecteur.

La file d'inspection est un diagnostic de causes **immédiates** selon les règles de correspondance du pipeline ; elle ne remplace pas une preuve acoustique de l'origine de chaque échec.
