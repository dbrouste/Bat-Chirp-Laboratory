# Calibration de sensibilité des annotations — expériences contrôlées

Ce module vérifie la **capacité** de l'audit à détecter des erreurs connues,
pas la justesse des annotations humaines initiales. Il ne modifie jamais les
WAV ou JSON, et n'ouvre **que la partition calibration**.

## Expériences sur les WAV réels de calibration

Le programme `tools/audit_sensitivity.py` produit 24 variations
**en mémoire seulement** pour chacun des chirps sélectionnés :

- 6 décalages globaux : −5, −2, −1, +1, +2, +5 kHz ;
- 12 erreurs locales : −5, −2, +2, +5 kHz, chacune appliquée au début,
  au milieu ou à la fin. Les points manuels sont interpolés pour obtenir
  21 points et une perturbation localisée, même si la référence originale
  ne contenait que deux points ;
- 6 troncatures : retrait de 20 % ou de 40 % de la durée, au début,
  à la fin ou réparti entre les deux bouts.

À chaque variante, l'audit utilise la même représentation mixte de
Bat-Acoustic-Analyser (65 % STFT / 35 % Reassignment, FFT 1024,
hop STFT maximum 32, hop Reassignment 16). Les métriques sont celles de
`evaluate_chirp_v2`, avec la normalisation carrée
du module partagé `chirp_geometry`.

La classification de récupération est **exploratoire** :
- décalage global : la différence d'écart normal médian signé doit avoir
  le signe opposé à la perturbation et une amplitude ≥ 0,5 kHz_eq ;
- erreur locale : signe opposé **ou** hausse du P90 ≥ 0,5 kHz_eq ;
- troncature : marquée **non détectable par la seule erreur transverse**,
  même si la courbe raccourcie est parfaitement centrée sur le signal.
  Une évaluation séparée du début/de la fin et de l'enveloppe énergétique
  sera nécessaire pour valider sa complétude.

Par défaut, seulement 10 chirps de calibration sont évalués. Cela correspond
à 240 variantes acoustiques, en plus des références non perturbées.

~~~powershell
git pull origin main
python -m pytest -q tests/test_sensitivity_audit.py
python tools/audit_sensitivity.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\sensitivity-v1" `
  --max-chirps 10 `
  --fft-size 1024 `
  --stft-hop 32 `
  --reassignment-hop 16
~~~

Résultats :
- `sensitivity_cases.csv` : une ligne par perturbation ;
- `sensitivity_summary.json` : comptes par type, erreurs,
  et avertissements d'interprétation.

## Signaux synthétiques à vérité connue

Le programme `tools/test_synthetic_ridges.py` produit
64 simulations (= 4 pentes × 4 décalages × 4 situations) :

- pentes : 0, −2, −12, −30 kHz/ms ;
- distances normales vraies : 0, −2, +2, +5 kHz_eq ;
- signal propre, crête parallèle concurrente, croisement bref et
  signal artificiellement atténué.

Ce test opère sur des **surfaces spectrales synthétiques**, et non sur des
enregistrements WAV ni sur le vrai pipeline STFT/Reassignment. Il sert à
isoler et vérifier la géométrie et la robustesse du suivi par Viterbi,
sans prétendre reproduire toutes les conditions d'enregistrement.

~~~powershell
python tools/test_synthetic_ridges.py `
  --output "C:\Users\damien\Documents\Python\Python-data\synthetic-stress-v1"
~~~

Résultats : `synthetic_stress.csv` et
`synthetic_summary.json`.

## Interprétation

**Ne jamais ajuster l'algorithme ou les seuils sur le jeu de test.**
Les comptes « détectés » représentent la réponse à une perturbation
contrôlée, pas le taux de véritables erreurs d'annotation reconnues.
L'analyse devra examiner les faux négatifs, notamment les petits
décalages et les portions courtes, avant de fixer un seuil clinique
ou un statut de validation A/B/C.

Les troncatures, l'absence de signal et les appels superposés
nécessitent des critères distincts (couverture temporelle du signal,
support dans les extensions, cohérence des harmoniques).

## Deuxième campagne sans recouvrement

Pour tester les **30 chirps suivants** après un premier essai sur 3 chirps,
utiliser `--skip-chirps 3 --max-chirps 30` : le décalage compte les
annotations manuelles validées de la partition calibration dans l'ordre du
manifeste. Les trois premiers chirps sont exclus de cette exécution.

```powershell
python tools/audit_sensitivity.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\sensitivity-batch2" `
  --skip-chirps 3 `
  --max-chirps 30
```

Chaque chirp produit 24 perturbations, soit 720 cas attendus pour 30 chirps
traités sans erreur. Les résultats restent regroupés par type d'erreur.
Pour viser d'autres sessions, il faut privilégier ultérieurement un
échantillonnage stratifié plutôt que les premiers chirps de l'ordre du manifeste.


## Score local glissant (expérimental, après campagne 30 chirps)

L'audit V2 calcule désormais, sur les **mêmes 20 écarts normaux signés**
exprimés en `kHz_eq`, une médiane sur chaque fenêtre contiguë de
5 profils par défaut (configurable entre 3 et 20 via `--local-window`).
Le score local est le maximum de la **valeur absolue de la médiane signée**
de ces fenêtres, ce qui favorise les erreurs localement cohérentes
et réduit l'influence d'un seul point aberrant. Sont également exportés :
maximum de médiane des écarts absolus, position de la fenêtre la plus
marquée, région début/milieu/fin, et médiane absolue dans chaque tiers.

Les champs CSV sont `local_max_abs_signed_median_khz_eq`,
`local_max_median_abs_khz_eq`, `local_peak_region`,
`local_start_median_abs_khz_eq`, `local_middle_median_abs_khz_eq`,
`local_end_median_abs_khz_eq`, et `local_flag_experimental`.

L'alerte exploratoire est `local_flag_experimental=True` dès que
le score atteint **1,5 kHz_eq** (`--local-threshold 1.5`).
**Ce seuil n'est pas calibré ; il ne remplace pas `review_priority`.**
Pour comparer équitablement une annotation à sa version perturbée,
`local_new_alert` n'est vrai que si le score initial était sous le
seuil et le score perturbé le franchit. Les annotations initiales déjà
signalées sont comptées séparément dans `baseline_local_flagged`.

`sensitivity_summary.json` contient maintenant pour chaque type
`legacy_new_alerts`, `local_new_alerts` et
`baseline_local_flagged`. La comparaison n'est pas un taux de faux
positifs car le corpus n'a pas été entièrement réexaminé par un expert.

### Reproduire les 30 mêmes chirps

~~~powershell
git pull origin main
python -m pytest -q tests/test_local_quality.py tests/test_sensitivity_audit.py tests/test_transverse_ridge.py
python tools/audit_sensitivity.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\sensitivity-batch2-local" `
  --skip-chirps 3 `
  --max-chirps 30 `
  --local-window 5 `
  --local-threshold 1.5
~~~

Comparer ce rapport au rapport `sensitivity-batch2` précédent,
à population et à FFT/hop identiques. Ne pas cumuler les deux cohortes :
il s'agit des **mêmes chirps** analysés avec des métriques supplémentaires.
