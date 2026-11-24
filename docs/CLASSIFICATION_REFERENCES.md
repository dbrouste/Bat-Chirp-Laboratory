# Classement des références manuelles suspectes

L'audit acoustique V2 calcule maintenant un score de **priorité de revue**
entre 0 et 100 pour chaque chirp manuel validé de **calibration**.
Le jeu de test est exclu par construction. Ce score est **heuristique,
non calibré**, et ne représente ni une probabilité d'erreur ni une décision
d'exclusion.

## Lancer un classement complet

Depuis la racine de Bat-Chirp-Laboratory (avec Bat-Acoustic-Analyser installé
à côté, et les dépendances Python disponibles) :

```powershell
python tools/audit_acoustic_v2.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\reference-validation-v1" `
  --stft-hop 32 `
  --reassignment-hop 16 `
  --plot-top 20
```

**Ne pas renseigner `--max-chirps`** pour évaluer toute la calibration.
Ce mode n'analyse pas les chirps non annotés.

Le nouveau fichier `reference_review_ranked.csv` comporte **toutes** les
références analysées, ordonnées du score le plus élevé au plus faible.
Les erreurs d'analyse sans score sont conservées à la fin avec leur diagnostic
et doivent être examinées séparément. Le fichier historique
`annotation_review_queue.csv` reste produit pour la compatibilité.
`acoustic_chirps_v2.csv` comporte également les nouvelles colonnes.

## Interprétation

| Composante | Maximum | Calcul provisoire |
|---|---:|---|
| acoustique | 40 | médiane, P90 et offset normal, soutien à ±3 kHz_eq |
| local | 25 | écart maximal sur fenêtre glissante, déviation signée locale |
| géométrie | 20 | flags déterministes de durée, fréquence, ordre des points, etc. |
| ambiguïté | 15 | manque de contraste spectral |

Les contributions acoustiques et locales sont atténuées si le contraste
est faible : un ridge difficile à mesurer ne suffit pas à condamner une
annotation humaine. `suspicion_reliability` vaut `élevée`, `moyenne`,
`faible` ou `indisponible`; c'est une appréciation du diagnostic,
pas une confiance probabiliste. Le détail est disponible dans
`suspicion_breakdown_json`.

Les colonnes `suspicion_reason` et `quality_flags` expliquent la priorité
et les signaux déclencheurs. Un chirp sans alerte reste présent pour une
validation d'échantillons négatifs. En cas d'échec acoustique,
`suspicion_score` reste vide : ne pas confondre une panne d'analyse et
une annotation erronée. `suspicion_score_version` identifie la formule.

## Revue dans Bat Acoustic Analyser

Ouvrir **Analysis > Contrôle qualité des annotations manuelles** et sélectionner
`reference_review_ranked.csv`. Le fichier `acoustic_summary_v2.json` créé
à côté du CSV contient le chemin du dataset et permet de découvrir automatiquement
`manifest.csv`, ou de choisir ce manifeste si le corpus a été déplacé.

L'interface propose un tri numérique par score, les raisons et leurs
composantes, un seuil de score et des filtres de décision. La revue ouvre
les **WAV/JSON originaux** via le manifeste. Ne pas modifier les copies
du dataset. Après corrections, utiliser **Synchroniser le corpus…** puis
relancer l'audit dans un nouveau répertoire de résultats. Seules les
décisions de revue/corrections sont écrites dans les JSON originaux,
pas les scores.

## Calibration future

Mesurer la précision du classement par annotation vérifiée et les taux
de corrections dans les top 10/20/50 de la calibration; échantillonner aussi
des scores bas. Revoir les seuils et les pondérations sans jamais les
optimiser sur le split de test.
