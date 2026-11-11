# Construire le corpus local WAV / annotations JSON

Ce script prepare un jeu reproductible de **calibration** et un jeu de **test** pour Bat-Chirp-Laboratory.
Il analyse un dossier source contenant plusieurs sous-dossiers de WAV et de JSON
d'annotations de **Bat-Acoustic-Analyser**.

## Format pris en charge

Dans Bat-Acoustic-Analyser, un JSON d'annotations est generalement situe dans
le meme dossier que les WAV et reference **plusieurs fichiers** :

~~~json
{
  "schema_version": 1,
  "files": {
    "rec_001.wav": {"chirps": [
      {"source": "manual", "status": "validated", "points": [
        {"t_ms": 1, "f_khz": 70}, {"t_ms": 2, "f_khz": 50}
      ]}
    ]},
    "rec_002.wav": {"chirps": []}
  }
}
~~~

Le script copie **le JSON original exactement**, ainsi que **tous les WAV
references dans son champ files**. Il ne filtre ni ne modifie les annotations.
Un JSON n'est selectionne que s'il contient au moins un chirp
source="manual", status="validated" (reference de benchmark).
Les WAV seulement automatiques du meme JSON sont aussi copies pour ne pas
rendre le JSON incoherent. Les fichiers WAV sans JSON ne sont pas copies.

Un JSON avec un WAV manquant ou une entree invalide est exclu entierement
et signale dans warnings.csv. Les autres JSON valides sont conserves.

## Filtrer les JSON provenant d'autres applications

Le laboratoire distingue deux generations de fichiers :

1. **Nouveaux JSON** : champ `created_by` exactement egal a
   `Bat-Acoustic-Analyser`. Le nom du JSON peut etre quelconque, mais
   `schema_version=1`, `units.time=ms`, `units.frequency=kHz` et
   `files` doivent etre conformes.
2. **JSON historiques sans created_by** : memes schema, units et files, ET
   nom du JSON identique a celui du dossier contenant (ex.
   `Noumea_2026/Noumea_2026.json`).

Pour les deux formats, la presence d'au moins un chirp
`source=manual`, `status=validated` avec au moins deux points
`t_ms/f_khz` numeriques et finis, de temps distincts, est indispensable.
Un document signalant un autre `created_by` est toujours rejete.

Tous les fichiers JSON ignores ou invalides sont repertories dans
`warnings.csv` avec leur nom et la raison (y compris les fichiers JSON
d'autres categories). Le recapitulatif `summary.json` indique separement
`json_tagged_accepted` et `json_legacy_accepted`.

**Attention :** une detection structurelle ne peut pas prouver absolument
l'origine d'un ancien fichier sans identifiant. Le choix strict du nom
de dossier minimise le risque de contamination, au prix de pouvoir
exclure d'anciens fichiers renommes. Ils restent intacts sur le disque.

## Lancer sous Windows (PowerShell)

Depuis la racine du depot Bat-Chirp-Laboratory :

~~~powershell
# Observer le corpus sans rien copier
py -3.12 tools/build_dataset.py `
  --source "D:\MesEnregistrements" `
  --output "D:\BatLaboratory\dataset-v1" `
  --dry-run

# Construire effectivement le corpus
py -3.12 tools/build_dataset.py `
  --source "D:\MesEnregistrements" `
  --output "D:\BatLaboratory\dataset-v1" `
  --test-ratio 0.20 `
  --seed 42
~~~

Le script ne necessite pas de paquets supplementaires. --source est le dossier
qui contient **tous** les dossiers d'enregistrements; le scan est recursif.
--output doit etre un dossier nouveau ou vide, **hors de la source**.
Pour regenerer le corpus, choisir un nouveau dossier (dataset-v2) :
aucun contenu existant n'est efface automatiquement.

## Structure produite

~~~text
D:\BatLaboratory\dataset-v1\
  calibration\
    wav\site_A\nuit_1\rec_001.wav
    annotations\site_A\nuit_1\nuit_1.json
  test\
    wav\site_B\nuit_4\rec_008.wav
    annotations\site_B\nuit_4\nuit_4.json
  manifest.csv
  warnings.csv
  summary.json
~~~

Les sous-repertoires d'origine sont preserves : plusieurs WAV avec le
meme nom dans des sessions distinctes ne se remplacent jamais.

- manifest.csv : chemin d'origine, chemin copie, partition, SHA-256 WAV et
  JSON, nombre de chirps manuels valides, manuels non valides et automatiques.
- warnings.csv : JSON invalides, WAV manquants, references conflictuelles,
  groupes sans chirp manuel valide, etc.
- summary.json : statistiques de scan, nombre de sessions, comptages par partition.

**Split sans fuite :** on repartit les sessions (dossiers du JSON) entieres,
pas les chirps individuels. Des WAV identiques bit pour bit, meme presents dans
plusieurs dossiers, sont toujours dans la meme partition. Le ratio 20 % est
une **cible par nombre de chirps manuels valides** : il peut differer de 20 %
si les sessions sont grosses ou peu nombreuses. Avec une seule session
independante, il n'y a **pas** de jeu de test; c'est signale.

Le split est reproductible a corpus et seed identiques. Modifier le contenu
des dossiers ou des sessions peut changer les partitions : une campagne
officielle doit figer son dataset et son manifeste.

## Lancer le benchmark du laboratoire

Les chemins produits restent compatibles avec le lecteur du laboratoire :

~~~powershell
.\.venv\Scripts\python.exe -m laboratory `
  --annotations "D:\BatLaboratory\dataset-v1\calibration\annotations" `
  --wav-dir "D:\BatLaboratory\dataset-v1\calibration\wav" `
  --out "D:\BatLaboratory\results-calibration" `
  --workers 4
~~~

Pour le benchmark final, remplacer **calibration** par **test** dans les deux
chemins, et choisir un autre repertoire de resultats. Ne pas regarder ou
optimiser les parametres sur le jeu de test avant de figer le modele.

## Verifier le generateur

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_dataset_builder.py
~~~

La copie est verifiee avec un hash SHA-256. Le script ne modifie jamais les
WAV et JSON sources. Il ne genere pas de scores ni d'annotations automatiques :
il construit uniquement les entrees du laboratoire.
