## Actualiser une annotation corrigée sans changer le split

Si le fichier source JSON a été corrigé dans Bat-Acoustic-Analyser, il ne
faut pas reconstruire le corpus avec un tirage différent. L'outil suivant
remplace la copie du **JSON complet** dans le corpus existant et actualise
les comptages et l'empreinte du manifeste. Il refuse toute modification
des WAV, de la liste de fichiers associés ou des partitions de test.

Depuis la racine de Bat-Chirp-Laboratory sous PowerShell :

~~~powershell
git pull origin main
python tools/update_dataset.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --wav "20250615_175727.wav" --dry-run
python tools/update_dataset.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --wav "20250615_175727.wav"
~~~

Le script détecte automatiquement le JSON source depuis manifest.csv. Il
ne modifie jamais les fichiers sources. Une sauvegarde de la copie
précédente du JSON et du manifeste est créée sous previous_metadata/.
Si le WAV a changé, si le JSON référence d'autres fichiers, ou si
l'enregistrement est en partition test, le script s'arrête.

Après l'actualisation, recalculer l'audit dans un nouveau répertoire :

~~~powershell
python tools/audit_annotations.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --output "C:\Users\damien\Documents\Python\Python-data\quality-v2"
~~~

Remarque : les annotations sont stockées par répertoire; la mise à jour
synchronise donc **tous** les chirps de tous les WAV de ce JSON. Les
anciens résultats de benchmark et d'audit ne sont pas réécrits.


## Synchronisation de toutes les corrections de calibration (nouveau)

Depuis Bat-Acoustic-Analyser, ouvrir **Analysis > Contrôle qualité des
annotations manuelles**, charger l'audit puis son `manifest.csv`, et utiliser
**Synchroniser le corpus…**. Le bouton effectue une vérification à blanc puis
demande confirmation avant d'écrire les copies dans le dataset.

Équivalent en ligne de commande :

~~~powershell
python tools/update_dataset.py --dataset "C:\\Users\\damien\\Documents\\Python\\Python-data\\dataset-v1" --all-changed --dry-run
python tools/update_dataset.py --dataset "C:\\Users\\damien\\Documents\\Python\\Python-data\\dataset-v1" --all-changed
~~~

Pour des originaux déplacés, utiliser `--source-root "D:\\NouveauDossier"`
avec la même racine que dans la fenêtre de revue de l'annotateur.

- La commande parcourt chaque **JSON de calibration** une seule fois, même s'il
  référence plusieurs WAV.
- La comparaison SHA-256 repère les JSON originaux modifiés, y compris les
  décisions de revue. Aucun chirp non annoté n'est recherché ni ajouté.
- Les WAV d'origine et du corpus sont contrôlés et les chemins relatifs
  distinguent les enregistrements homonymes.
- Une vérification préliminaire de toutes les sessions précède la mise à jour ;
  si une session échoue, l'opération n'écrit aucun fichier.
- La partition `test` reste **strictement verrouillée** pour cette commande.
  Le découpage des sessions ne change pas.
- Chaque JSON modifié est copié avec sauvegarde de la version précédente sous
  `previous_metadata/`. Les métriques et rapports déjà générés ne sont **pas**
  réécrits : relancer audit et benchmark après synchronisation.

### Décisions de revue

Les métadonnées `annotation_quality` sont enregistrées sur le **chirp manuel
validé du JSON original**, pas sur sa copie dans le dataset. Choix disponibles :
`to_review`, `verified_correct`, `correction_needed`, `corrected`,
`uncertain`, `excluded` (et `ignored` pour rétrocompatibilité). Le statut
`corrected` n'est accepté que si la trajectoire source a réellement changé
par rapport à celle du corpus audité. La date, les empreintes géométriques et
l'historique des décisions permettent d'auditer les corrections.

Les erreurs d'annotation ne sont pas automatiquement corrigées. Le statut `excluded` est respecté par le benchmark SNR : le chirp manuel validé
est retiré des références au moment de l'évaluation, sans modifier le JSON.
Les autres chirps du même WAV restent disponibles.
