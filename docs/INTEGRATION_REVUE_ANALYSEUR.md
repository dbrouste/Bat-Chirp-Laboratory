# Intégration de la revue scientifique dans l'analyseur

## Audit préalable et plan (17 novembre 2026)

L'étape 5 est conservée dans le commit `592eff6`, tag
`checkpoint/scientific-review-v1` : 259 tests rapides réussis, quatre
intégrations exclues ; l'intégration ciblée de l'exporteur passe également.
Le dossier local `bat_chirp_geometry.egg-info/` reste hors commit.

Audit en lecture seule de Bat-Acoustic-Analyser avant modification :

- `annotation_quality.py` résout les lignes CSV par manifeste et vérifie les
  empreintes ; `set_quality_status` écrit actuellement dans le JSON source.
- `annotation_quality_dialog.py` fournit table, filtres, ouverture par ID,
  changement de racine et synchronisation du corpus. Ces deux dernières actions
  doivent être absentes du mode de revue scientifique.
- `window.py::_open_quality_review_chirp` appelle déjà `_load_wav` avec
  `autoplay=False`, sélectionne le chirp et demande une FFT du zoom. Les gardes
  de génération de session dans `_spectrogram_ready` et `_render_spectrogram`
  protègent déjà le zoom contre les résultats tardifs.
- Le chargement ordinaire sauvegarde préalablement les modifications courantes
  et peut lancer analyse du dossier, notch et préchargements. L'entrée en revue
  doit refuser un document source non enregistré et inhiber les automatismes.
- Les exports V1 contiennent quatre CSV `.analyser.csv`, un manifeste d'export,
  des dossiers de cas stables et un `analyser_workspace` avec copies physiques.
  Les décisions initiales sont vides et ne doivent pas être remplacées.

Plan retenu avant toute modification du deuxième dépôt :

1. Ajouter une session de revue dérivée du résolveur existant, avec validation
   stricte des chemins internes, des identités et de la provenance développement.
2. Intégrer un panneau spécialisé dans le dialogue QA existant, sans modifier
   les comportements historiques de contrôle des annotations sources.
3. Ajouter un stockage CSV atomique (temporaire voisin, flush/fsync, replace),
   verrou d'écriture et contrôle de version ; conserver chaque décision passée
   en ajoutant les révisions plutôt qu'en remplaçant une ligne existante.
4. Charger la dernière décision par `case_id`, offrir filtres et navigation,
   préserver les modifications du formulaire avant changement de cas.
5. Borner le chargement/sauvegarde aux copies du projet, inhiber synchronisation,
   détection automatique et lecture automatique, conserver les gardes du zoom.
6. Tester stockage, conflits, résolution, protections et interface Qt sur données
   synthétiques uniquement, puis documenter les commandes et limites.

Branches de publication : `feat/laboratory-scientific-review` (laboratoire) et
`feat/scientific-review-interface` (analyseur). Aucun algorithme scientifique
n'est modifié. Les fichiers locaux `adaptive_notch.py`, `adaptive_notch.py.bak`
et `spectral_floor.py` sont conservés.

## Synchronisation observée sur GitHub

Les branches ont été consultées via le connecteur GitHub, sans push ni merge :

- Laboratoire : GitHub `develop` = `91f4922` ; six commits locaux supplémentaires,
  dont le checkpoint de revue `592eff6`. GitHub `main` = `a820a1f` ; la référence
  `main` locale est ancienne et ne doit pas servir de preuve de synchronisation.
- Analyseur : GitHub `develop` = `8021006` ; HEAD local initial `5d370bc`,
  14 commits accessibles supplémentaires ; les trois fichiers locaux ci-dessus
  ne font pas partie de ces commits.
- Traitement scientifique : checkout propre à `db919ef` ; GitHub `main` est
  `683bb7e`. Aucune modification ou synchronisation de ce dépôt n'est effectuée.

Les données privées, sorties de campagnes, WAV et annotations ne sont ni
ajoutés à Git ni transférés. Aucun accès à la validation ou au test réservé.

Lors de l'audit initial, l'API GitHub confirmait également l'absence de tags
`checkpoint/` distants. Le tag `checkpoint/scientific-review-v1` était alors local.

## Fonctionnement réalisé

Le dialogue existant reconnaît une file `.analyser.csv` et y intègre un panneau
de revue spécialisé. Il charge les deux décisions indépendantes, confiance,
commentaire, auteur, date UTC et preuves facultatives. Les six classes de signal
et cinq classes d'annotation sont disponibles, avec valeurs initiales vides.
Un choix explicite Non déterminé / Non évaluée enregistré compte comme traité.
Les deux axes ne sont jamais déduits l'un de l'autre.

La reprise lit la dernière révision de chaque `case_id`. Les filtres permettent
de voir tous les cas, les cas à traiter ou les cas déjà traités. Les boutons
Précédent / Suivant naviguent dans les lignes visibles ; Ouvrir le cas charge
la copie et son identifiant de navigation sans lecture automatique.

Les décisions sont ajoutées comme révisions dans `human_decisions.csv`, avec
conservation de toutes les lignes précédentes. Le stockage utilise un verrou
système, un contrôle de l'empreinte chargée, un temporaire voisin, flush/fsync
et remplacement atomique. En cas de conflit, le formulaire n'est pas enregistré
et l'utilisateur doit recharger les décisions. Un éditeur externe ignorant le
verrou ne participe pas au protocole de concurrence ; éviter son usage simultané.

Le résolveur vérifie la provenance développement, les empreintes des cas et de
la file, le manifeste et les identités ; seuls les chemins de travail internes
sont autorisés. Il accepte les anciens séparateurs Windows et le déplacement
d'un export complet. Les originaux cités dans les annexes ne sont jamais ouverts
par l'import de revue. Aucune ouverture de validation/test, aucune propagation
au corpus et aucun changement des calculs scientifiques.

## Lancer l'interface

Depuis Bat-Acoustic-Analyser :

```powershell
$Data = "$env:USERPROFILE\Documents\Python\Python-data"
.\.venv\Scripts\python.exe .\main.py --review-csv "$Data\development-review-v1\no_blob.analyser.csv"
```

Remplacer `no_blob` par `nms`, `end_error_top20` ou `negative_final` selon la
file à ouvrir. Le lanceur ne rouvre pas le dernier WAV. Le menu Analysis permet
également de charger une autre file. Aucun export réel n'est régénéré ici.

Les décisions sont enregistrées par le panneau, jamais dans les métadonnées QA
des JSON sources. Les éditions manuelles éventuelles de l'éditeur principal
restent limitées aux copies. Les automatismes de détection/comparaison,
préchargement, notch, synchronisation et éditeur externe sont bloqués dans ce
mode. Après ouverture d'un cas, la session reste limitée à la file active ;
quitter et relancer sans option pour retrouver le mode général.

Le guide complet de l'analyseur est `docs/REVUE_SCIENTIFIQUE.md` dans son dépôt.
Il documente aussi l'historique du CSV, la reprise et les commandes de tests.

## Limites conservées

- Les courbes automatiques positives complètes ne figuraient pas dans les traces
  V1 : aucune trajectoire n'est reconstruite. Les coordonnées d'extrémités et
  candidats NMS restent dans les annexes JSON, sans couche graphique nouvelle.
- Les négatifs gardent leurs courbes dérivées automatiques et leurs IDs dédiés.
  Une décision humaine ne les transforme pas en références manuelles validées.
- Les tests Qt hors écran ne remplacent pas la contre-expertise acoustique
  et biologique. Aucune décision n'est préremplie et aucun vrai cas n'est classé.
- Les garanties de verrou et remplacement visent un système de fichiers local,
  pas une fusion collaborative avec des éditeurs externes ou tous les partages
  réseau. Toutes les révisions restent lisibles dans le CSV.

## Vérifications finales

- Laboratoire : `validate_local.ps1`, **259 tests rapides réussis**, quatre
  intégrations exclues, `pip check` réussi.
- Exporteur et intégration des quatre files avec le vrai résolveur de l'analyseur :
  **13 tests réussis**, sur données synthétiques.
- Analyseur : `test_scientific_review.py`, `test_annotation_quality.py` et
  `test_spectrogram_interactions.py`, **70 tests réussis**, trois sous-tests
  réussis. Quatre avertissements de dépréciation NumPy/pyqtgraph préexistants.
- Une vraie fenêtre Qt a chargé un court WAV synthétique avec préférences
  isolées : pas d'autoplay, pas de détection et zoom conservé après arrivée
  du spectrogramme. Le panneau a aussi été rendu hors écran pour contrôle visuel.
- `git diff --check` passe dans les deux dépôts. Les trois fichiers locaux
  préexistants de l'analyseur conservent exactement leurs SHA-256 initiaux.

Fichiers de l'intégration dans l'analyseur : `main.py`, `README.md`,
`bat_analyser/annotation_quality.py`, `annotation_quality_dialog.py`, `window.py`,
nouveaux `scientific_review.py`, `scientific_review_panel.py`,
`tests/test_scientific_review.py` et `docs/REVUE_SCIENTIFIQUE.md`.
Dans le laboratoire : schéma de `tools/prepare_scientific_review.py`,
test d'intégration, README et documents de revue. La sauvegarde de l'intégration
utilise un commit distinct dans chaque dépôt et un tag annoté
`checkpoint/scientific-review-interface-v1`. Le checkpoint d'étape 5 reste
inchangé ; les branches `develop` et `main` ne sont pas fusionnées ou modifiées.
