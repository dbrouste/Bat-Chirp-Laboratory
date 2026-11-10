# Calibrer le score géométrique — protocole v1

## 1. Ce qui est déjà calculé

Le benchmark indépendant (`--scoring geometry`) produit les trajectoires
automatiques sans utiliser les références manuelles, puis les compare à des
chirps manuels validés :

- transformation temps–fréquence fixée **uniquement par la pente manuelle** ;
- 20 normales locales régulièrement espacées sur la courbe ;
- correspondances ordonnées entre les normales et la trajectoire automatique ;
- qualité locale décroissant avec l'erreur, couverture et pénalité de queue
  incorrecte, notamment lors d'un rebond vers un écho ;
- score continu de 0 à 100, appariement un-à-un et rappel pondéré par le SNR.

**Valeurs de référence initiales à vérifier sur le corpus :** tolérance de
5 kHz à SNR ≤15 dB, 3 kHz à SNR ≥35 dB, avec interpolation linéaire ;
poids d'importance de 1 à 3 ; 20 normales ; recherche dans un rayon de
`2 × (tolérance / √2)`. Le score lui-même n'est pas une probabilité.

## 2. Jeu d'exemples étiquetés

Chaque exemple est **une paire** `(annotation manuelle, proposition automatique)`.
Une personne juge `expected_good` : vrai si la proposition suit correctement
le chirp manuel, faux pour une proposition fausse, coupée, déviant vers l'écho,
ou correspondant à une harmonique différente. Les exemples doivent comprendre
des cas difficiles et simples, différents SNR et fréquences, knees, échos,
superpositions et segments tronqués. Une annotation manuelle absente de la
sortie de détection est un **raté de détection**, à évaluer dans le benchmark
complet : elle ne constitue pas une paire à calibrer.

Format JSON :

```json
{
  "examples": [
    {
      "id": "sessionA-chirp001-bonne",
      "group": "session-A-20261111",
      "split": "development",
      "expected_good": true,
      "snr_max_db": 40.0,
      "manual_points": [[0.0, 100.0], [2.0, 80.0], [4.0, 60.0]],
      "automatic_points": [[0.0, 100.5], [2.0, 79.5], [4.0, 60.5]]
    },
    {
      "id": "sessionA-chirp001-echo",
      "group": "session-A-20261111",
      "split": "development",
      "expected_good": false,
      "snr_max_db": 40.0,
      "manual_points": [[0.0, 100.0], [2.0, 80.0], [4.0, 60.0]],
      "automatic_points": [[0.0, 100.0], [4.0, 60.0], [5.0, 90.0]]
    }
  ]
}
```

Exemple **fictif**, fourni pour la structure : les coordonnées du véritable
corpus doivent venir des WAV et annotations. Dans les trajectoires, les temps
sont en **ms absolues dans le WAV** et les fréquences en **kHz**. Le SNR doit
être déterminé à partir de la référence humaine uniquement, avec la méthode
fixe utilisée par le laboratoire, et ne jamais venir d'une courbe automatique.

## 3. Répartition sans fuite

Répartir les **nuits ou sessions complètes** entre développement, validation
et test, par exemple 60/20/20 % des nuits, en veillant à la diversité du
corpus. Deux chirps du même WAV/nuit ne doivent pas se retrouver dans deux
ensembles différents. Le script refuse qu'un même `group` soit utilisé dans
plusieurs ensembles.

- **development** : sélection des paramètres, avec des exemples positifs **et**
  négatifs ;
- **validation** : mesure de performance du réglage obtenu, sans ajustement
  supplémentaire sur ce même ensemble ;
- **test** : ensemble scellé, non évalué pendant la calibration (les champs de
  courbe peuvent même être omis des exemples `test` dans ce fichier).

Si l'on change les critères à la suite des résultats de validation, il faut
considérer cette validation comme réutilisée et réserver un autre ensemble
indépendant pour la confirmation finale. Les seuils ne doivent pas être choisis
en regardant directement le test.

## 4. Exécution et critères

```powershell
.\.venv\Scripts\python.exe -m laboratory.calibration `
  --examples "D:\BatLaboratory\geometry_examples.json" `
  --output "D:\BatLaboratory\geometry_calibration.json"
```

Le script explore 324 combinaisons : rayon de recherche, tolérance angulaire
des queues, couverture minimale, erreur médiane maximale et score minimal.
Il maximise le **F1 pondéré par SNR** sur le développement (avec départage
favorisant les vrais chirps fortement audibles et limitant les faux accords).
Il enregistre les métriques de validation **une seule fois**, sans lire ni
évaluer les courbes du test.

La configuration JSON résultante peut être transmise au benchmark :

```powershell
.\.venv\Scripts\python.exe -m laboratory `
  --processing-root "D:\Python\bat_full_spectrum_processing" `
  --annotations "D:\Bat\annotations" --wav-dir "D:\Bat\WAV" `
  --out "D:\BatLaboratory\geometry-results" --workers 4 `
  --scoring geometry `
  --geometry-config "D:\BatLaboratory\geometry_calibration.json"
```

Le rapport contient le score moyen **pondéré sur les chirps manuels** ; un
chirp non détecté contribue un score nul. Les faux positifs ne sont comptés
que si `annotation_exhaustive: true` est explicitement présent dans le record
du WAV. Conserver les rapports et commit IDs avant toute modification de
l'algorithme. Comparer également les durées de calcul.

## 5. Limites connues / prochaine campagne

- La calibration v1 règle **l'acceptation géométrique**, pas les seuils du
  détecteur ni ses trajectoires : l'optimisation de l'algorithme viendra après.
- Les 324 combinaisons sont une petite grille prédéfinie, pas une recherche
  hyperparamétrique intelligente. Les coefficients de SNR 3/5 kHz et 1→3
  restent eux aussi à **étudier empiriquement** sur les vrais chirps.
- La qualité des jugements humains doit être contrôlée : réétiqueter un sous-
  échantillon à l'aveugle et examiner les désaccords avant d'interpréter F1.
- Les prolongements de début sont désactivés par défaut : à calibrer après
  constitution de cas annotés pertinents.
- À privilégier : un premier lot d'environ **50–100 paires**, puis augmenter
  vers 200+ avec des négatifs réalistes (échos, faux segments, harmoniques).
