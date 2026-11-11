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
