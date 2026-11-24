# Diagnostic du détecteur Adaptive V4 — chirps manqués à fort SNR

**Date :** 20 novembre 2026  
**Partition :** calibration figée uniquement, jamais le jeu de test.

## Traçabilité

- Laboratoire figé : `7ec740313af65a2c10f4926bc1a233c4aaa4ed70`.
- Détecteur externe figé : `db919efb8db0bca3692385383762b50e9cd3ae16`.
- Manifeste figé : SHA-256 `de326bd6f9aeb25d7792968db6ad46b5f0a7eeb8c7ae4dbad87e046810a03e31`.
- [Analyse des étapes du détecteur #44671618146](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44671618146).
- [Criblage largeur/NMS sur 240 WAV #44671960606](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44671960606).

Les fichiers sont écrits uniquement sous le dossier de résultats `Python-data/lab-geometry-comparisons/run-44668877700/` du PC Windows. Les WAV et JSON ont été contrôlés par empreinte et ouverts en lecture seule ; aucune annotation n'a été modifiée.

## 1. Étapes responsables des 26 chirps manqués à fort SNR

Le détecteur original a été rejoué sur les 18 WAV contenant les 92 références non associées. Les blobs bruts à seuil SNR 10 dB (branche générale) et 9 dB (branche basses fréquences), les filtres de taille/pente/largeur, puis la NMS ont été reconstruits avec les fonctions internes **réelles** du dépôt de traitement.

**Contrôle :** les candidats finaux reconstruits sont identiques, dans le même ordre, au benchmark figé (670 candidats et 261 associations sur les 18 WAV).

| Étape d'élimination d'un pic admissible au sens du matcher | Chirps fort SNR |
|---|---:|
| Pas de pic brut admissible avec les seuils SNR existants | 5 |
| Pic brut éliminé par les filtres de blobs/taille/pente | 9 |
| Pic éliminé par la largeur minimale de la branche générale V4 | 4 |
| Pic admissible avant NMS, supprimé par la NMS | 8 |
| **Total** | **26** |

Le classement utilise la première étape de disparition d'un **pic potentiellement admissible à moins de 18 kHz_eq de l'annotation** ; il ne prouve pas qu'il s'agit de la véritable émission. Les cas individuels et les chemins des fichiers originaux sont conservés uniquement en local dans `high-snr-stage-audit-v1/high_snr_stage_audit.csv`.

## 2. Criblage complet de variantes

Les sorties sont des **candidats uniquement**, sans exécution du suivi géométrique. Le contrôle de reproduction retrouve exactement les **401 associations de références positives**, **1 154 candidats sur les 37 WAV positifs** et **677 candidats sur les 203 WAV explicitement `no_chirp`**. Les annotations des WAV positifs ne sont pas exhaustives ; les candidats non associés n'y sont pas des faux positifs attestés.

Six variantes ont été comparées avec la fonction d'extraction de blobs et la NMS d'origine, à partir des mêmes données spectrales.

| Variante | Références associées | Gains SNR ≥ 35 dB | Gains 15–35 dB | Candidats sur `no_chirp` | WAV `no_chirp` avec candidat |
|---|---:|---:|---:|---:|---:|
| Référence : largeur ≥ 1,33 ms, NMS 20 ms | 401 | — | — | 677 | 108 |
| Largeur ≥ 1,0 ms, NMS 20 ms | 420 | +4 | +15 | 800 | 108 |
| Largeur ≥ 0,85 ms, NMS 20 ms | 420 | +4 | +15 | 800 | 108 |
| Largeur ≥ 1,33 ms, NMS 12 ms | 401 | 0 | 0 | 725 | 108 |
| Largeur ≥ 1,33 ms, NMS 6 ms | 412 | +7 | +4 | 807 | 108 |
| Largeur ≥ 1,0 ms, NMS 12 ms | 420 | +4 | +15 | 854 | 108 |

Aucun des six scénarios ne perd de référence précédemment associée dans cet essai de **matching**. Une association nouvelle n'est pas automatiquement une détection biologiquement correcte ; il faut confirmer la position du pic sur le spectrogramme et exécuter le suivi.

### Garde-fous

Toutes les variantes alternatives accroissent les candidats sur les WAV négatifs explicitement revus. La largeur 1,0 ms donne **+19 associations (+4 fort SNR, +15 SNR moyen)** mais **+123 candidats sur les négatifs (+18,2 %)**. La NMS 6 ms donne **+11 associations (+7 fort SNR)** mais **+130 candidats sur les négatifs (+19,2 %)**. Même une NMS 12 ms sans gain positif ajoute 48 candidats négatifs.

**Décision : ne promouvoir aucune variante**, maintenir les paramètres V4 actuels en production et en benchmark de référence. La précision des trajectoires finales et la qualité des nouveaux appariements ne sont pas évaluées à ce stade.

## 3. Suites concrètes

1. Examiner les nouvelles associations à fort SNR dans le CSV local `candidate-width-nms-screen-v1/candidate_variant_changed_references.csv` ; comparer aux 26 cas de `high-snr-stage-audit-v1/high_snr_stage_audit.csv`. Priorité aux candidats supprimés par NMS, puis à ceux éliminés par la largeur minimale.
2. Étudier les caractéristiques qui distinguent les pics supplémentaires présents dans les WAV `no_chirp` des candidats récupérant des annotations validées : durée, pente, largeur fréquentielle, structure de blob, répétition temporelle et éventuelles interférences. Ne pas employer le SNR des références manuelles comme paramètre du détecteur.
3. Proposer sur une branche isolée des filtres **basés uniquement sur le signal audio**, puis refaire ce même criblage sur calibration, avec garde-fou négatif et sans utiliser la partition test.
4. Ne lancer le benchmark complet avec suivi et score géométrique que pour une variante admissible, après revue visuelle des nouvelles associations.

**Limite de l'outillage :** la file `candidate_variant_changed_references.csv` est un CSV de diagnostic, pas un export complet `.analyser.csv` compatible avec le module de revue scientifique de Bat Acoustic Analyser ; celui-ci exige un manifeste et des copies de travail avec empreintes. Un projet de revue importable devra être produit par son exporteur dédié.
