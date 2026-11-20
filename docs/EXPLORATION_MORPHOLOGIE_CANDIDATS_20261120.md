# Hypothèses morphologiques pour récupérer les candidats V4 — calibration du 20 novembre 2026

## Statut

**Recherche exploratoire, non déployée.** Les chiffres décrivent l'association entre candidats et annotations manuelles, **pas** une amélioration validée de la détection complète. Le jeu `test` figé n'a pas été consulté.

Détecteur et règles d'appariement inchangés ; seuls des candidats de la branche originale `adaptive_v4` sont examinés et combinés en lecture seule.

## Provenance et reproductibilité

- Laboratoire : `7ec740313af65a2c10f4926bc1a233c4aaa4ed70`.
- Traitement externe : `db919efb8db0bca3692385383762b50e9cd3ae16`.
- Manifeste de calibration : SHA-256 `de326bd6f9aeb25d7792968db6ad46b5f0a7eeb8c7ae4dbad87e046810a03e31`.
- Base 240 WAV : 37 avec références positives, 203 explicitement `review_status=no_chirp`; 493 références manuelles validées.
- [Morphologie des candidats #44677763582](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44677763582).
- [Criblage de 30 règles fixes #44678115107](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44678115107).
- [Rejeu hybride des associations #44678506631](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44678506631).

Les sorties détaillées restent sur le PC Windows sous :

`Python-data/lab-geometry-comparisons/run-44668877700/`

- `candidate-morphology-v1/candidate_morphology_individual.csv` : candidats nouveaux sur négatifs et candidats associés à des références récupérées, avec caractéristiques et provenance ;
- `morphology-gates-v1/morphology_gate_screen.csv` : conservation des candidats sous 30 règles par variante ;
- `candidate-hybrid-screen-v1/candidate_hybrid_changed_references.csv` : références manuelles dont l'association change sous les quatre hypothèses ;
- les trois dossiers contiennent aussi `summary.json` et les sorties auxiliaires.

Aucun WAV ni JSON d'annotation n'a été modifié. Les identités et chemins individuels ne sont pas publiés dans les logs GitHub.

## 1. Comparaison des distributions

Deux variantes ont été analysées individuellement : largeur V4 à 1,0 ms avec NMS 20 ms et largeur à 1,33 ms avec NMS 6 ms.

Les « nouveaux négatifs » ci-dessous sont les candidats *absents de la liste de référence* dans les WAV humains étiquetés `no_chirp`. Ce ne sont pas automatiquement des faux chirps reconstruits.

| Variante | Cohorte | Nombre | Médiane étendue fréquentielle | Médiane taille du blob | Médiane fréquence du pic |
|---|---|---:|---:|---:|---:|
| Largeur 1,0 ms | Références récupérées | 19 | 11,25 kHz | 24 px | 113,25 kHz |
| Largeur 1,0 ms | Candidats négatifs nouveaux | 137 | 7,50 kHz | 18 px | 45,75 kHz |
| NMS 6 ms | Références récupérées | 11 | 24,75 kHz | 80 px | 55,50 kHz |
| NMS 6 ms | Candidats négatifs nouveaux | 145 | 6,75 kHz | 26 px | 51,00 kHz |

Pour NMS 6 ms, la médiane de position relative du pic dans le blob est 0,12 sur les références récupérées, contre 0,42 sur les candidats négatifs nouveaux. Les distributions se recouvrent ; les médianes ne prouvent pas une frontière de décision universelle.

La variante largeur 1,0 ms introduit **137 candidats supplémentaires et en retire 14** parmi les négatifs, soit +123 net ; la variante NMS 6 ms en introduit **145 et en retire 15**, soit +130 net. Tous les nouveaux appariements positifs de ces deux variantes portent sur des candidats absents de la baseline.

## 2. Règles morphologiques exploratoires

Trente règles prédéfinies dans le script ont été appliquées aux seules cohortes de candidats supplémentaires, sans recalcul de l'audio. Deux constats ont motivé le test combiné :

| Condition sur candidat nouveau | Récupérations conservées | Candidats négatifs nouveaux conservés |
|---|---:|---:|
| Largeur 1,0 ms ; fréquence du pic ≥ 100 kHz | 13/19 (tous SNR moyen) | 7/137 |
| NMS 6 ms ; étendue fréquentielle ≥ 20 kHz | 9/11 (dont 6 fort SNR) | 6/145 |

**Limite :** ces règles ont été sélectionnées à partir de résultats sur la même calibration utilisée pour les évaluer. Leurs apparent bon rapport de sélection est potentiellement optimiste, y compris vis-à-vis des sessions enregistrées dans le même contexte acoustique.

## 3. Rejeu exact du matcher : ajout uniquement

Une expérience diagnostique a conservé **tous les candidats de référence** et ajouté uniquement les candidats supplémentaires des variantes satisfaisant les règles morphologiques. L'appariement un-à-un canonique a ensuite été exécuté de nouveau sur les 240 WAV. C'est un assemblage expérimental de candidats, **non** une nouvelle version déployée du détecteur.

| Hypothèse | Références associées / 493 | Récupérées (dont fort SNR) | Références perdues | Candidats négatifs | WAV négatifs avec candidat |
|---|---:|---:|---:|---:|---:|
| Baseline V4 | 401 | — | — | 677 | 108 |
| NMS6 + hauteur ≥ 20 kHz | 410 | +9 (6) | 0 | 683 | 108 |
| NMS6 + hauteur ≥ 15 kHz + pic relatif ≤ 0,25 | 409 | +8 (6) | 0 | 682 | 108 |
| Largeur1 + fréquence du pic ≥ 100 kHz | 414 | +13 (0) | 0 | 684 | 108 |
| **Combinaison des règles hauteur20 + fréquence100** | **423** | **+22 (6)** | **0** | **690** | **108** |

La variante combinée fait passer l'association de 401/493 (81,34 %) à 423/493 (85,80 %) : **+4,46 points de pourcentage**. Sur les WAV positifs partiellement annotés, le total de candidats passe de 1 154 à 1 265, mais les candidats non associés ne peuvent pas être classés comme faux positifs.

En revanche, même cette variante augmente le nombre de candidats sur les WAV `no_chirp` de 677 à 690 (+13, soit +1,92 %). Toutes les variantes hybrides **échouent donc au garde-fou strict** de non-augmentation du volume de candidats négatifs.

## 4. Décision

- **Ne pas modifier le détecteur de référence** et ne pas promouvoir le filtre combiné.
- Inspecter acoustiquement les **22 nouvelles associations**, en distinguant les 6 références à fort SNR et les 16 à SNR moyen. Une association au candidat ne garantit ni une trajectoire géométrique correcte ni un vrai chirp.
- Vérifier visuellement les **13 candidats supplémentaires** sur les WAV explicitement `no_chirp`, pour identifier les structures acoustiques et la fiabilité de l'étiquette.
- Avant de retenir un réglage, effectuer le suivi complet des candidats nouvellement associés et contrôler la géométrie, la durée et les extrémités. Déterminer aussi le coût en traitement et la reproductibilité par session.
- Les seuils issus de cette exploration ne constituent pas une validation indépendante. Garder strictement la partition `test` pour la phase finale après gel de l'algorithme.

**Attention aux biais :** la règle de fréquence ≥ 100 kHz risque de sélectionner une partie seulement des types de chirps présents ; le `peak_db` mesuré dans les blobs est un niveau spectrogramme, pas une mesure SNR directement comparable d'un WAV à l'autre. Il faut éviter d'apprendre le détecteur à partir de l'emplacement temporel d'annotations manuelles, qui n'est pas disponible en utilisation réelle.


## 5. File courte pour la revue humaine

Le [run #44679107858](https://github.com/dbrouste/Bat-Lab-Control/actions/runs/44679107858) a exporté, depuis les résultats du criblage sans nouvelle analyse des WAV, une file de **35 cas distincts**, avec contrôles de provenance et de dédoublonnage :

- **6 associations récupérées à SNR ≥ 35 dB** (priorité 1) ;
- **16 associations récupérées à SNR moyen** (priorité 2) ;
- **13 candidats supplémentaires sur 11 WAV `no_chirp`** (priorité 3).

Fichier sur le PC du runner :

`Python-data/lab-geometry-comparisons/run-44668877700/hybrid-review-queue-v1/hybrid_cases_for_review.csv`

Les colonnes contiennent le fichier WAV/JSON original, le chirp de référence lorsqu'il existe, les coordonnées temporelles/fréquentielles du pic, la morphologie du blob et des champs initialement vides pour la décision humaine. Les originaux et leurs annotations ne sont pas modifiés.

**Ce CSV est une file de diagnostic, pas un projet `.analyser.csv` importable** dans le panneau de revue scientifique de Bat Acoustic Analyser. Cet import exige un export complet avec manifeste, empreintes et copies de travail vérifiées. Ne pas importer ce CSV comme projet de revue natif tant que ce format n'a pas été généré et testé séparément.
