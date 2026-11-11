# Audit qualité des annotations manuelles

Cet outil lit le corpus construit par tools/build_dataset.py. Il ne modifie
ni les WAV ni les annotations. Par défaut, il examine **seulement calibration** :
la partition test reste réservée à l'évaluation indépendante.

Depuis la racine du dépôt sur Windows (Python 3.13 convient) :

~~~powershell
python tools/audit_annotations.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --output "C:\Users\damien\Documents\Python\Python-data\quality-v1"
~~~

Les fichiers produits sont :
- chirp_quality.csv : une ligne par trajectoire manuelle validée, mesures
  de durée, fréquences, pente, largeur fréquentielle, nombre de points,
  retournements et alertes éventuelles ;
- quality_summary.json : total de chirps, nombre de trajectoires à revoir,
  comptes des alertes et résumé par session ;
- audit_warnings.csv : fichiers illisibles ou structures incohérentes.

Les flags incluent notamment invalid_points, nonincreasing_time,
zero_duration, unusual_duration (hors 0,5–20 ms),
frequency_outside_review_band (hors 10–180 kHz),
above_nyquist, outside_wav_duration, steep_local_segment,
large_frequency_step et multiple_direction_changes.

Les seuils sont des **heuristiques de tri pour inspection humaine**,
pas un score de précision ou des motifs d'exclusion automatique. Une
trajectoire biologiquement plausible peut déclencher un flag. Les WAV
non PCM peuvent ne pas être lus par la bibliothèque wave standard :
dans ce cas les contrôles dépendant de la durée et de la fréquence
d'échantillonnage sont sautés et signalés dans audit_warnings.csv.

Option --include-test : audit descriptif également du test, à éviter
pendant la calibration des seuils et de l'algorithme. Ne jamais ajuster
les paramètres d'après les 182 chirps tenus à l'écart.

Vérification :

~~~powershell
python -m pytest -q tests/test_quality_audit.py
~~~

La précision réelle des annotations ne peut pas être inférée de leur
seule géométrie : une revue visuelle sur spectrogramme reste nécessaire.
