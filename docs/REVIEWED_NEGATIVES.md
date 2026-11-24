# WAV « No chirp » : références négatives de calibration

## Quelle étiquette fait foi ?

Bat-Acoustic-Analyser utilise le champ **par WAV** :

```json
{
  "review_status": "no_chirp",
  "chirps": []
}
```

Il s'agit d'une **décision explicite de revue**, et non du fait qu'un
fichier ne contienne aucune annotation manuelle. Les WAV sans cette
valeur restent de classe inconnue même lorsqu'ils sont dans le même
dossier qu'un chirp validé.

Le laboratoire rejette les contradictions : un WAV portant à la fois
`review_status=no_chirp` et un chirp manuel, édité ou validé doit
être corrigé à la source avant d'être utilisé comme négatif.
Les propositions automatiques non validées peuvent rester dans un WAV
revue `no_chirp` ; elles ne constituent pas des références.

## Ce que l'on mesure

La même exécution du benchmark traite deux cohortes, **calibration
uniquement** :

- **Positifs** : chirps manuels validés, pour rappel, suivi,
  géométrie, couverture et SNR.
- **Négatifs explicites** : WAV revus `no_chirp`, pour mesurer les
  alertes de **génération de candidats** (et non les pistes finales).

Le rapport donne `totals.reviewed_no_chirp` :

- `wav_count` : nombre de WAV négatifs réellement analysés ;
- `wavs_with_candidate_alert` : WAV où au moins un candidat a été produit ;
- `candidate_alerts` : nombre total de candidats sur ces WAV ;
- `wav_false_alarm_fraction` : proportion des WAV négatifs avec alerte ;
- `candidate_alerts_per_wav` : nombre moyen de candidats par WAV ;
- `candidate_alerts_per_hour` : densité horaire si les durées WAV
  peuvent être lues.

Ces nombres ne décrivent **pas** encore les faux chirps reconstruits
en fin de pipeline : le benchmark courant n'exécute le suivi détaillé
que sur les candidats appariés aux annotations positives. Le taux
global de précision sur l'ensemble des WAV reste inconnu.

Le nombre de candidats **sur les WAV positifs partiellement annotés**
ne doit en aucun cas être traité comme un nombre de faux positifs.

## Exécuter avec les négatifs déjà figés

Depuis Bat-Chirp-Laboratory :

```powershell
python -m laboratory `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --out "C:\Users\damien\Documents\Python\Python-data\baseline-positifs-negatifs-v1" `
  --workers 4 `
  --include-reviewed-negatives
```

Le champ `metadata.frozen_dataset.reviewed_no_chirp_available` indique
le nombre de WAV explicitement négatifs dans la **partition calibration**
du dataset figé. `reviewed_no_chirp_evaluated` indique combien ont
effectivement été utilisés (éventuellement moins si `--max-files`
limite l'expérience). Le paramètre `--max-files` sélectionne les WAV
avec chirps manuels en premier.

Si `reviewed_no_chirp_available=0`, **aucun taux de fausses alertes ne
peut être conclu** à ce stade : il faut d'abord vérifier si l'étiquette
a été conservée dans le JSON figé et si les WAV négatifs ont été
importés.

### Attention au constructeur actuel de dataset-v1

Dans sa version actuelle, `laboratory.dataset_builder` inclut par
défaut les **JSON de sessions contenant au moins un chirp manuel
validé**. Les WAV no_chirp des mêmes sessions sont copiés avec ce JSON.
En revanche, les sessions exclusivement `no_chirp` peuvent avoir été
ignorées lors de la création initiale de `dataset-v1`.

Le nouveau benchmark **ne prétend pas** retrouver les WAV absents du
manifest figé et **ne modifie jamais** le dataset existant.
Pour les ajouter, prévoir un corpus `dataset-v2` (ou un corpus
négatif figé complémentaire), à construire à partir des originaux
et à répartir par session avec protection du jeu test.

## Optimisation agentique

`laboratory.autonomous_optimizer` et `laboratory.code_agent`
activent désormais la cohorte `no_chirp` lors de leurs **nouvelles
campagnes**. Si cette cohorte n'est pas vide :

- Une hausse du nombre de WAV négatifs avec alertes **rejette** un essai.
- Une hausse du total des candidats sur ces négatifs **rejette** un essai.
- Une diminution des fausses alertes sur ces négatifs améliore le
  classement, sans substituer cette mesure au rappel des positifs.
- Un changement du nombre de WAV négatifs évalués rend les essais
  incomparables.

La cohorte reste **calibration seulement** ; elle n'utilise jamais
la partition test. Les anciens `chirp-agent-v1` ont un protocole
différent : utiliser **un nouveau dossier de résultats** afin de
recalculer une baseline avec la nouvelle politique de score.

Le benchmark ne peut mesurer une bonne spécificité que pour la
distribution des WAV revus `no_chirp`. Une baisse des alarmes sur ces
quelques fichiers n'est pas une preuve d'absence de faux positifs dans
toute la population des enregistrements.
