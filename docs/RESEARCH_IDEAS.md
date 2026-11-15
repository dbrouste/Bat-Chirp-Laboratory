# Programme de recherche — quatre hypothèses à explorer avec Codex

Version initiale de la feuille de route scientifique du Chirp Lab.
Les hypothèses ci-dessous proviennent des observations de terrain et
sont **à tester**, pas encore des lois imposées à l'algorithme.

Le catalogue structuré correspondant est :
[`experiments/ideas.yaml`](../experiments/ideas.yaml).

## Priorité 1 — Ne pas détecter les échos comme des chirps (H04)

**Question :** peut-on identifier la copie retardée d'un chirp direct
afin de l'empêcher de créer une détection indépendante, sans supprimer
une vraie vocalisation proche dans le temps ?

### Expériences envisagées

1. Constituer des exemples annotés manuellement avec au moins trois
   classes : signal direct, écho confirmé, véritable chirp suivant.
   Ajouter explicitement des cas d'harmoniques et de plusieurs individus.
2. Étudier la similarité des trajectoires, le délai, l'atténuation et la
   déformation entre signal direct et candidat ultérieur. Aucune de ces
   propriétés n'est suffisante isolément.
3. Tester une classification de probabilité d'écho, avec seuil
   d'abstention en cas d'ambiguïté ; comparer avec une suppression
   directe du spectre ou du masque avant recherche de nouveaux candidats.
4. Sur les cas où un chirp réel chevauche l'écho, comparer traitement de
   l'écho et préservation du signal réel.

**Métriques :** rappel des chirps directs par SNR, taux d'échos
identifiés sur échos **vérifiés**, taux de suppression erronée de vrais
chirps, erreurs de suivi et de début/fin. Mesurer également les confusions
écho / harmonique / autre individu.

**Critère de refus :** éliminer des chirps authentiques pour réduire
artificiellement le nombre de candidats.

**Point scientifique :** en l'absence de zones négatives confirmées,
les candidats non appariés à une annotation ne sont pas des faux positifs
démontrés. Les 1 348 candidats de la meilleure campagne ne permettent
pas de quantifier seuls les échos.

## Priorité 1 — Seuils asymétriques et fin de trajectoire (H02)

**Observation à vérifier :** sur la visualisation utilisée, la partie
gauche d'un chirp se termine dans le bruit environnant et sa trajectoire
reste généralement visible, alors que la partie droite se confond avec
son propre écho. Vérifier la convention des axes (gauche / droite,
temps croissant, direction de balayage) avant d'interpréter les
extrémités physiquement.

**Principe :** le suivi pourrait appliquer des critères d'arrêt différents
selon le côté, plutôt qu'un seuil unique. En présence de branches
concurrentes, une continuation descendante et régulière serait
favorisée, **mais seulement comme a priori souple** : certains appels
ont naturellement des remontées, changements de pente ou formes atypiques.

### Expériences envisagées

1. Mesurer séparément les erreurs, les couvertures et les scores
   géométriques aux deux extrémités, à SNR et contexte d'écho comparables.
2. Comparer des seuils d'arrêt indépendants par côté.
3. Comparer continuité de pente, pénalité d'inversion, descente préférée,
   et détection de copies retardées. Faire une ablation : une seule
   stratégie activée à la fois, puis combinaisons.
4. Évaluer une atténuation spectrale de l'écho sur copie de travail,
   en vérifiant que le véritable chirp n'est pas raccourci.
5. Sur les chemins bifurquants, noter chaque décision du suivi pour
   pouvoir inspecter les erreurs.

**Métriques :** erreur de début et de fin en ms, couverture,
`kHz_eq` sur les trois tiers de la trajectoire, taux d'accrochage
à un écho et taux de tronquage abusif.

**Cas bloquants :** chemin descendant mais faux ; écho plus puissant
que l'appel direct ; chirps voisins réellement indépendants.

## Priorité 2 — Harmoniques : recherche conditionnelle au chirp parent (H01)

**Hypothèse :** une harmonique potentielle peut être plus courte ou
moins intense que le chirp parent, tout en occupant une région
temps-fréquence prédictible. Une recherche conditionnelle peut tester
un seuil inférieur à celui du détecteur de chirps indépendants.

### Expériences envisagées

1. À partir de paires parent/harmonique **validées**, mesurer les
   distributions de rapport de fréquence, de décalage temporel, de
   durée et de puissance. Vérifier d'abord la tendance supposée :
   elle ne doit pas être codée comme contrainte universelle.
2. Prédire des couloirs temps-fréquence à partir de la trajectoire
   parent et des rapports harmoniques plausibles.
3. Comparer la détection générale et une seconde recherche localisée
   avec seuils SNR/longueur propres aux harmoniques.
4. Tester les harmoniques absentes, faibles, fragmentées, chevauchées
   et les erreurs de rattachement à un parent.

**Métriques :** rappel des harmoniques annotées, précision sur zones
négatives **spécifiquement vérifiées**, distance à la trajectoire
attendue, fiabilité du rattachement au parent.

**Règle de métier :** une harmonique rattachée à un parent n'ajoute pas
un passage biologique ou un chirp indépendant au comptage.

## Priorité 3 — Probabilité temporelle de prochain chirp (H03)

**Hypothèse :** pour un individu unique, les intervalles inter-chirps
sont structurés ; ils peuvent évoluer progressivement avec l'activité.
Ce rythme peut fournir une probabilité de présence dans une fenêtre
temporelle future.

### Expériences envisagées

1. Constituer des séquences annotées comme provenant probablement
   d'un **individu unique** : ne pas imposer cette hypothèse à un WAV
   où plusieurs individus se chevauchent.
2. Comparer un historique glissant robuste des intervalles, une
   tendance locale d'accélération/décélération et un modèle sans rythme.
3. Tester des séquences avec manques, pauses, changement de phase
   (approche, poursuite, etc.), accélération et plusieurs individus.
4. Donner un bonus de priorité à des indices acoustiques faibles
   dans les plages prédites, puis vérifier la preuve spectrale.

**Métriques :** calibration de probabilité de présence, rappel
supplémentaire sur séquences validées, mauvais déclenchements en
zones négatives et robustesse aux changements de rythme.

**Règle absolue :** ne **jamais** créer un chirp sur le seul argument
« on en attend un maintenant ». Un écho peut également suivre un
rythme apparent ; la preuve acoustique reste indispensable.

## Ordre et dépendances

Les trois premiers prototypes à préparer sont :

1. **H04 — jeu d'exemples d'échos vérifiés**, avec vraies vocalisations
   proches et harmoniques comme contre-exemples. Ce jeu sert aussi H02.
2. **H02 — diagnostics de terminaison**, éventuellement testables avec
   des annotations de trajectoire actuelles ; l'étiquette « accrochage
   à l'écho » exige toutefois une revue supplémentaire.
3. **H01 — paires parent/harmonique**, à explorer après définition
   de leurs métriques propres.

**H03** viendra après constitution d'un sous-corpus de séquences
mono-individu ; son évaluation ne peut se résumer au rappel des chirps
individuels.

## Intégration technique à Codex

Le pilote actuel `laboratory/code_agent.py` est volontairement borné
à `bat_analysis/detection.py` et à des métriques de détection et de
suivi sur chirps manuels. **Il ne charge pas encore automatiquement
`experiments/ideas.yaml`.** On ne doit pas le faire explorer
H01/H02/H03/H04 aveuglément avec le seul score global : les métriques
de discrimination écho/harmonique/individu n'existent pas encore.

Il faut d'abord mettre en place des modules de benchmark spécialisés :

- `echo_benchmark` pour H04 et H02, avec échos et vrais chirps
  annotés comme classes séparées ;
- `harmonic_benchmark` pour H01, avec rattachement au parent ;
- `sequence_benchmark` pour H03, avec séries mono-individu.

Pour la future couche Codex, chaque idée devra fournir un contrat
d'essai : fichiers autorisés (sous worktree), tests, métriques, garde-fous,
budget d'essais et critères d'arrêt. Conserver les résultats dans un
dossier distinct par idée et par empreinte de code/dataset. Les patches
restent soumis à revue et ne sont jamais fusionnés automatiquement.

Le corpus `dataset-v1` reste figé : tous les compléments d'étiquetage
d'échos, de zones négatives ou de parentés doivent conduire à une version
distincte, traçable et validée. Le split test reste hors optimisation.
