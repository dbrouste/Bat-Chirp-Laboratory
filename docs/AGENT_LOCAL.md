# Agent local d'optimisation — version 1

## Principe

Un **seul lancement** fait une campagne d'expériences sur le détecteur
actuel, compare tous les essais sur le même corpus figé et sélectionne une
configuration *candidate* soumise à validation humaine.

Cette première version est un **agent d'expérimentation à politique
déterministe** (pas encore un modèle de langage). Elle prend des décisions
sur la base des résultats précédents ; **elle ne modifie pas le code**.
Une deuxième couche Codex pourra ultérieurement proposer des changements
sur des branches de développement et relancer les tests. Les corrections
automatiques ne seront jamais fusionnées ni déployées directement.

### Sécurité scientifique

- Le mode autorisé est **calibration seulement** :
  les 37 WAV avec références validées du corpus actuel, et non les WAV
  non annotés ou le jeu test.
- L'intégrité du manifeste, des WAV et des JSON est vérifiée au lancement,
  puis au cours de chaque exécution du benchmark.
- Les sorties sont obligatoirement **en dehors** du dossier du corpus.
- Le poids SNR, le protocole géométrique et la liste des références ne
  changent jamais entre les essais.
- **Même les WAV avec annotations manuelles sont partiellement annotés.**
  Seules les trajectoires manuelles validées sont des références positives.
  Les zones sans annotation sont de statut **inconnu**, et non négatif.
- Les candidats non associés ne sont **pas des faux positifs attestés** ;
  il est impossible de déduire la précision, la spécificité, le taux de
  faux positifs ou le rejet réel des échos avec ces seules références.
- Le nombre total de candidats reste une information sur la charge
  de traitement, mais **n'influence pas le score ni l'admissibilité**
  des essais. Une baisse du nombre de candidats n'est jamais un
  indicateur de meilleure détection.
- Les tolérances géométriques sont encore **non calibrées** ; le
  classement est provisoire.
- Aucune modification de Git, des sources, des annotations originales,
  du logiciel Bat Acoustic Analyser ou du détecteur embarqué.
- Aucun essai ne peut toucher automatiquement la partition test.
- Une meilleure configuration est une **proposition**, pas un déploiement.

## Lancement sur Windows

Après avoir récupéré la branche develop et installé les dépendances :

    git pull origin develop
    python -m pip install -r requirements.txt
    python -m pytest -q tests/test_autonomous_optimizer.py tests/test_frozen_dataset.py

**Double-cliquer** sur :

    tools\run_agent_windows.cmd

Le script utilise par défaut :

    %USERPROFILE%\Documents\Python\Python-data\dataset-v1
    %USERPROFILE%\Documents\Python\Python-data\chirp-agent-v1

Si les répertoires sont différents, définir les variables d'environnement
CHIRP_DATASET et CHIRP_AGENT_OUTPUT, ou lancer directement :

    python -m laboratory.autonomous_optimizer ^
      --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" ^
      --out "C:\Users\damien\Documents\Python\Python-data\chirp-agent-v1" ^
      --workers 4 --max-total 11

Pour un premier essai court (réalisé ensuite automatiquement sans intervention),
il est possible de limiter à 2 **nouveaux** essais, puis de reprendre :

    python -m laboratory.autonomous_optimizer --dataset "CHEMIN_DATASET" --out "CHEMIN_RESULTATS" --max-new 2
    python -m laboratory.autonomous_optimizer --dataset "CHEMIN_DATASET" --out "CHEMIN_RESULTATS"

La commande suivante reprend les expériences déjà validées et poursuit
dans la limite des essais demandés. Le programme ne répète pas une
expérience enregistrée dans agent_state.json.

## Ce que fait l'agent

Il recherche progressivement une configuration sur un espace borné :

1. **Baseline** : valeurs par défaut du détecteur raccordé.
2. **Génération des candidats** : largeur minimale 1,0 ; 0,85 ; 1,65 ms
   à comparer avec la valeur par défaut (1,33 ms).
3. **Marge du suivi** : 2 ; 3 ; 1 ms en repartant du meilleur essai valide.
4. **Réassignation** : FFT 512/hop 16, FFT 256/hop 16,
   FFT 512/hop 32 ; seuls les paramètres du suivi changent.
5. **Gestion des échos** : comparaison du traitement actuel avec
   la désactivation du trimming spécifique aux rebonds d'écho.

Un essai peut être conservé dans les rapports mais **rejeté du classement**
s'il entraîne une régression supérieure à 2 points de rappel sur les
chirps à SNR ≥ 35 dB, une baisse du score de suivi pondéré supérieure
à 2 points, une hausse supérieure à 20 % de l'erreur géométrique
moyenne. Le nombre de candidats n'est pas un critère de rejet
ou de promotion : la calibration ne comprend pas de négatifs exhaustifs.
Ces limites sont des garde-fous de campagne et non des seuils scientifiques
universels.

Les essais admis sont classés avec une combinaison fixe de rappel brut,
rappel pondéré et score de suivi pondéré. Le nombre de candidats est
reporté à titre descriptif pour de futures mesures de coût CPU/mémoire ;
il ne constitue ni un score de précision, ni une pénalité de classement.
Chaque décision et son motif sont enregistrés.

## Résultats

Sous le répertoire de sortie :

- agent_report.html : bilan et classement lisible dans le navigateur ;
- agent_summary.json : baseline, meilleure suggestion, versions et limites ;
- leaderboard.csv : toutes les expériences et les paramètres exacts ;
- priority_cases.csv : chirps manqués à fort SNR, courbes très différentes,
  couverture faible et erreurs d'extrémités ;
- agent_state.json : état de reprise de la campagne ;
- trials/<id>/report.json : rapports complets de chaque essai ;
- trials/<id>/experiments.sqlite : historique détaillé du moteur de benchmark ;
- trials/<id>/cache/ : cache de ce moteur pour l'essai.

Le fichier de verrouillage .agent.lock empêche deux campagnes d'écrire
en même temps au même emplacement. Si le processus Windows s'arrête
brutalement et laisse ce fichier, vérifier qu'aucun agent ne tourne
avant de le supprimer.

Si le manifeste ou le code du détecteur a changé, **un nouveau dossier
de sortie est requis**, afin de ne pas mélanger des évaluations
incomparables.

## Conditions de sortie

Une campagne achevée sans erreur fournit une suggestion (ou les
paramètres initiaux si aucun essai ne les dépasse) et un inventaire des
échecs. Cette première version ne vérifie pas le statut des modifications
locales Git : examiner git status avant une baseline officielle, même
si le hachage du code externe est enregistré par le benchmark.

Les étapes suivantes sont :

1. Distinguer les chirps manqués par le détecteur des pistes candidates
   associées au mauvais chirp, avec revue visuelle ciblée.
2. Permettre à Codex de proposer des changements de code dans un
   espace de travail Git séparé (branch/worktree), sous tests automatisés.
3. Comparer à baseline fixe et garder la branche candidate sous
   validation humaine. Aucun accès au test final durant la recherche.
4. Introduire une distribution d'évaluation représentative de SNR très
   faibles, aujourd'hui quasi absents du corpus de calibration.

Ne pas interpréter cette version comme une optimisation automatique
du modèle lui-même : elle cherche des réglages d'un logiciel existant.
