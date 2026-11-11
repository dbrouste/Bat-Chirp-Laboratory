# Audit acoustique V2 — crêtes continues et profils perpendiculaires

Cet audit s'applique **uniquement à calibration**, sans modifier les WAV
ni les JSON. Il prend comme surface acoustique la représentation
`representation="mixed"` de Bat-Acoustic-Analyser (par défaut
**65 % STFT Flattop / 35 % Reassignment Flattop**, mélange sur les amplitudes
linéaires). L'algorithme de crête proposé ci-dessous est un diagnostic
indépendant du détecteur, mais sa surface est celle de l'annotateur.

## Principe

1. Échantillonner 20 positions entre les deux extrémités de l'annotation,
   par interpolation **linéaire** des points manuels.
2. Calibrer le repère carré sur **l'annotation manuelle seule** :
   `u = (t_ms - t0_ms) * k`, avec
   `k = (max(f_khz)-min(f_khz))/(t_fin_ms-t_debut_ms)`.
   Cette échelle unique transforme une distance horizontale (ms) en
   **kHz équivalents géométriques** (`kHz_eq`).
   Elle est indépendante du zoom, du nombre de sommets et du ridge.
   Une pente très forte n'est pas plafonnée. Si k < 1 kHz/ms, utiliser
   k = 10 kHz/ms avec `normalization_mode=near_horizontal_fallback`
   (chirp horizontal ou carré dégénéré).
3. À chaque position, interroger par interpolation bilinéaire l'image
   STFT/Reassignment le long de la normale, de -8 à +8 unités normalisées
   par pas de 0,5 kHz_eq. Une valeur positive est orientée vers les
   **fréquences supérieures**.
4. Chercher une **suite continue** de crêtes par programmation dynamique.
   Récompenser le contraste à un bruit estimé hors du chirp, pénaliser
   les distances à la référence et les variations de décalage d'un
   échantillon à l'autre. Un saut supérieur à 3 unités est interdit.
5. Rapporter l'écart normal signé médian, l'écart normal absolu médian,
   le P90 absolu, la différence de fréquence signée, le contraste
   visuel et le taux de points à ±3 unités.
6. Générer une file CSV de revue et des fichiers HTML interactifs
   montrant spectrogramme (mélange 65/35), annotation manuelle et crête
   continue pour les cas prioritaires.

**Important :** l'écart perpendiculaire est euclidien dans le repère carré
`(u, f_khz)`, exprimé en `kHz_eq`, pas en écart vertical de fréquence.
Une courbe horizontale conserve donc son écart en kHz, alors que les
obliques/quasi verticales utilisent la même unité de longueur.
`median_signed_frequency_khz` conserve la différence fréquentielle
distincte. Un offset normal positif pointe vers les fréquences supérieures.
Les anciens champs `*_normal_khz` restent disponibles pour les scripts
existants, mais représentent des `kHz_eq`; les sorties V3 comportent aussi
des champs `*_normal_khz_eq` et `normalization_mode`. Il faut recalibrer
les seuils de revue avant de comparer l'ancien et le nouveau classement.

## Résolution temporelle de l'image mixte (correctif)

Le calcul initial utilisait une STFT 1024/75 %, soit un hop de
**256 échantillons (667 µs à 384 kHz)**, trop grossier pour analyser
un chirp de 2 à 3 ms. Même si le Reassignment possédait une grille
plus fine, la fusion est **rééchantillonnée sur la grille de la STFT**
dans le pipeline de l'annotateur.

L'audit V2 conserve **exactement** cette fonction de fusion, mais
impose désormais par défaut un hop STFT de **32 échantillons maximum**
(83,3 µs à 384 kHz) et un hop Reassignment explicite de
**16 échantillons** (41,7 µs à 384 kHz). Si le recouvrement demandé est
encore plus important, l'audit conserve le plus petit hop obtenu.

Les options sont `--stft-hop 32` et `--reassignment-hop 16`.
Pour augmenter la finesse, utiliser par exemple
`--stft-hop 16 --reassignment-hop 8`, avec un coût de calcul supérieur.
Le budget de colonnes est automatiquement augmenté pour honorer le hop
Reassignment demandé, dans une limite de sécurité de 10 000 colonnes.
L'HTML indique les hops *effectifs* en échantillons et microsecondes.
Les paramètres STFT (taille FFT / hop) et Reassignment (512 / hop) restent
indépendants, mais la fusion se fait toujours sur la grille STFT.

**Les anciens rapports V2 doivent être régénérés** : les scores et
visualisations calculés avec le hop grossier ne sont pas comparables
directement aux nouveaux scores. Les seuils demeurent non calibrés.

Le suivi est volontairement ancré à la trajectoire manuelle. Il peut
mal interpréter un harmonique, un écho, un croisement de chirps, une
annotation insuffisamment longue ou une faible énergie locale. Les
seuils de priorité actuels sont **exploratoires et non calibrés** ;
l'absence d'alerte ne constitue pas une validation manuelle.

## Exécution Windows

Depuis le répertoire Bat-Chirp-Laboratory :

~~~powershell
git pull origin main
python -m pytest -q tests/test_transverse_ridge.py
python tools/audit_acoustic_v2.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\acoustic-v2-normals" `
  --reassignment-percent 35 `
  --fft-size 1024 `
  --overlap 0.75 `
  --stft-hop 32 `
  --reassignment-hop 16 `
  --max-chirps 10 `
  --plot-top 5
~~~

Le laboratoire recherche un clone de Bat-Acoustic-Analyser dans le
répertoire voisin. Si nécessaire, ajouter `--analyser-root "<chemin>"`.
Pour une annotation faite avec soustraction spectrale activée, ajouter
`--spectral-alpha 1.7` ; avec recouvrement
87,5 %, ajouter `--overlap 0.875`.

Après les tests pilotes, supprimer `--max-chirps 10` et utiliser
un nouveau répertoire de sortie pour la campagne complète.

## Résultats

- `acoustic_chirps_v2.csv` : toutes les mesures.
- `annotation_review_queue.csv` : cas prioritaires,
  avec les colonnes `source_wav` et `source_json` provenant du manifeste :
  utiles pour ouvrir les **originaux** dans Bat-Acoustic-Analyser.
  Ne jamais modifier les copies du laboratoire lors de la revue.
- `review_001.html`, etc. : spectrogrammes interactifs
  autonomes exportés pour les plus fortes priorités.
- `acoustic_summary_v2.json` : paramètres et comptages.
- `acoustic_diagnostics_v2.json` : erreurs de calcul.

Le script V1 `tools/audit_acoustic.py` reste disponible pour
des comparaisons sur le même corpus. Il n'y a pas de modification
automatique des annotations, de l'algorithme de détection ni du split.


## Validation du suivi indépendamment des alertes

Le mode `--plot-mode validation` exporte les premiers
`--plot-top N` chirps **même s'ils ne présentent aucune alerte**,
pour vérifier visuellement que le suivi ne reste pas artificiellement
collé à l'annotation. Le mode par défaut `--plot-mode suspects`
exporte les cas prioritaires.

Le réglage `--plot-detail both` produit deux vues dans chaque HTML :
le spectrogramme mixte avec les deux trajectoires et une carte des
**profils perpendiculaires** (intensité relative par profil) avec le
chemin continu en blanc. On peut choisir seulement `overlay` ou
`profiles`.

### Injection d'erreurs volontaires

Avec `--validate-shifts --shift-limit 10`, le laboratoire crée
**en mémoire uniquement** des variantes des 10 premières annotations
manuelles : +3, +5 et -5 kHz de décalage vertical. Ces erreurs
synthétiques sont analysées exactement sur le **même WAV**.
Aucun JSON, WAV ni fichier du corpus n'est modifié.

Un tableau `shift_validation.csv` contient, pour chaque
perturbation, l'écart normal médian avant/après, la différence et une
indication de récupération du **sens opposé au décalage**. Cette
indication est **exploratoire** et ne prouve pas la précision réelle du
suivi ; en particulier, une erreur verticale de 5 kHz ne correspond
pas exactement à 5 unités perpendiculaires.

Exemple recommandé après mise à jour du dépôt :

~~~powershell
git pull origin main
python -m pytest -q tests/test_transverse_ridge.py
python tools/audit_acoustic_v2.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\acoustic-v2-validation" `
  --reassignment-percent 35 `
  --fft-size 1024 `
  --stft-hop 32 `
  --reassignment-hop 16 `
  --max-chirps 10 `
  --plot-top 10 `
  --plot-mode validation `
  --plot-detail both `
  --validate-shifts `
  --shift-limit 10
~~~

Consulter `acoustic_summary_v2.json` pour le bilan,
`shift_validation.csv` pour les récupérations de signe
et `review_001.html` ... pour les comparaisons visuelles.
Les faux positifs/négatifs devront être analysés dans Bat-Acoustic-Analyser
avant de calibrer les seuils de priorité.
