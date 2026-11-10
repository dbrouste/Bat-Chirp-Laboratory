# Architecture du laboratoire

## Frontières entre les projets

- **Bat-Chirp-Laboratory** : gestion du corpus, protocole d'évaluation indépendant,
  expériences, statistiques, cache, ressources, rapports, puis optimisation et IA.
- **bat_full_spectrum_processing** : détection et suivi de chirps. Les différentes
  versions doivent être exécutées depuis leur clone Git spécifique, sans modifier
  les annotations manuelles ni le protocole d'évaluation.
- **Bat-Acoustic-Analyser** : création, révision et export des références humaines ;
  visualisation ultérieure des erreurs dans l'interface graphique.

## Connecteur d'algorithme, phase transitoire

Le laboratoire appelle actuellement `benchmark.reassignment_detector_seeds.evaluate`
à l'intérieur du dépôt de traitement externe. Cette API produit des trajectoires
évaluées par la méthode SNR actuelle. **Les agrégats sont recalculés par le
laboratoire** (`laboratory/aggregation.py`) et les données de chaque expérience
sont identifiées par les empreintes WAV, annotation, sources et paramètres.

Une étape ultérieure remplacera ce connecteur transitoire par un contrat neutre :
`detect(wav, parameters) -> liste de courbes (temps, fréquence, confiance)`.
Le laboratoire pourra alors appliquer sa propre géométrie aux courbes sans que
l'algorithme testé n'ait accès à l'implémentation du score.

## Principes de validation

1. Jeux de développement, validation et test séparés par nuit/session, et non
   par tirage aléatoire de chirps voisins.
2. Pas de modification automatique des annotations manuelles validées.
3. Les faux positifs sont évalués seulement sur les fichiers/fenêtres annotés
   de manière exhaustive.
4. Une expérience incomplète ne peut pas devenir le nouveau meilleur candidat.
5. Versions du laboratoire, des données et de l'algorithme enregistrées.
6. Les calculs lourds sont locaux sur Windows (4 workers initialement pour
   Ryzen 7 8845HS / 64 Go), sans consommer de minutes GitHub Actions.
7. Aucune fusion automatique vers les branches stables de l'algorithme.

## Feuille de route

1. **Phase 1** : migrations et benchmarks SNR reproductibles ; tests sur vrais
   WAV à exécuter sur le PC de l'utilisateur.
2. **Phase 2** : calcul indépendant du score géométrique à 20 perpendiculaires,
   queues, knees et harmoniques ; chronométrage par étape.
3. **Phase 3** : Optuna, espaces de paramètres, budgets d'essais, validation
   multiobjectif et classement des variantes sans fuite des données de test.
4. **Phase 4** : agent de recherche (Codex ou équivalent) dans des espaces Git
   isolés, avec hypothèses, code, tests, benchmark et proposition de PR.
5. **Phase 5** : interface web locale et suivi des campagnes depuis le téléphone.
