# Pilote agentique Codex — modification de code sous contrôle

## Ce que fait la deuxième couche

Cette version complète l'agent de réglage local, sans le remplacer.
Elle donne à Codex la possibilité de **proposer des changements du
détecteur Python** dans des copies Git indépendantes (worktrees), puis
soumet chaque proposition à un contrôleur local non-IA qui :

1. vérifie le manifeste et toutes les empreintes des WAV/JSON de
   **calibration uniquement** ;
2. vérifie que le dépôt original du détecteur est propre et correspond
   au code de la campagne de paramètres ;
3. mesure une baseline avec **les paramètres gagnants** de cette campagne ;
4. demande à Codex une modification précise sur une copie Git isolée,
   jamais sur le dépôt original ;
5. rejette toute proposition modifiant un fichier autre que
   `bat_analysis/detection.py`, modifiant un commit, créant un fichier
   non suivi ou contenant un conflit Git ;
6. exécute `tests/test_snr_benchmark.py` et
   `tests/test_tracking_margin.py` dans cette copie ;
7. lance les 37 WAV annotés du corpus de calibration dans le benchmark
   complet, compare à la baseline **avec les mêmes paramètres** et applique
   les garde-fous existants ;
8. conserve le diff du meilleur essai **dans son worktree** pour une
   éventuelle revue humaine. Aucune fusion, push ou modification
   automatique de `main`/`develop`.

La campagne par défaut est limitée à **3 propositions de code**. Le
raisonnement et la génération de modifications utilisent Codex CLI et
nécessitent une connexion pour joindre le modèle, mais tous les
benchmarks WAV s'exécutent **localement sur le PC**.

Le prompt transmis à Codex ne contient aucun WAV, JSON manuel,
chemin local du dataset ou donnée brute de référence : seulement les
mesures agrégées. Les commandes de Codex s'exécutent avec le sandbox
`workspace-write` et des approbations désactivées. Les vérifications
Git du contrôleur constituent une protection supplémentaire, non une
garantie de confinement absolue du système d'exploitation. Si les
enregistrements ou les secrets nécessitent une isolation renforcée,
utiliser une VM ou un compte Windows séparé sans accès au dossier
Python-data. Ne pas fournir d'accès réseau aux outils du sandbox.

## Préparation Windows (une seule fois)

Exiger les deux repos voisins :

    Python-Toolbox/
      Bat-Chirp-Laboratory/
      bat_full_spectrum_processing/

Installer Codex CLI et se connecter :

    npm install -g @openai/codex
    codex login
    codex --version

Le contrôleur est en Python, comme le laboratoire. Depuis
`Bat-Chirp-Laboratory` :

    git pull origin develop
    python -m pytest -q tests/test_code_agent.py tests/test_autonomous_optimizer.py

Vérifier que le dépôt détecteur est propre :

    cd ..\bat_full_spectrum_processing
    git status
    cd ..\Bat-Chirp-Laboratory

La campagne de paramétrage doit déjà avoir été exécutée dans le dossier
`chirp-agent-v1` avec son `agent_summary.json`, et le code du détecteur
ne doit pas avoir changé depuis. Sinon, relancer d'abord l'agent de
paramètres dans un nouveau dossier.

## Lancement

Double-cliquer sur :

    tools\run_code_agent_windows.cmd

Par défaut, les fichiers sont lus dans :

    %USERPROFILE%\Documents\Python\Python-data\dataset-v1
    %USERPROFILE%\Documents\Python\Python-data\chirp-agent-v1\agent_summary.json

Les résultats vont dans :

    %USERPROFILE%\Documents\Python\Python-data\chirp-code-agent-v1

Pour réaliser d'abord une proposition sans boucle complète :

    python -m laboratory.code_agent ^
      --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" ^
      --parameter-agent-summary "C:\Users\damien\Documents\Python\Python-data\chirp-agent-v1\agent_summary.json" ^
      --out "C:\Users\damien\Documents\Python\Python-data\chirp-code-agent-v1" ^
      --workers 4 --max-new 1

Reprendre simplement avec la même commande sans `--max-new 1`.
Les essais déjà enregistrés ne sont pas répétés. Le contrôleur
s'arrête et affiche une erreur s'il rencontre un worktree orphelin
créé par un arrêt brutal : ne jamais le supprimer aveuglément.

## Résultats et décisions

Le fichier `code_agent_summary.json` contient, entre autres :

- la baseline avec la meilleure configuration de réglage ;
- les tentatives et leur statut (`failed`, `complete`) ;
- le score et la raison d'admissibilité ;
- les chemins des worktrees et des rapports complets ;
- l'indication d'approbation humaine requise.

Chaque tentative conserve `codex.log`, `pytest.log`,
`benchmark/report.json` et la copie Git `code/`.
Les modifications ne sont pas commitées : les examiner avec :

    git -C "CHEMIN_DU_WORKTREE" diff -- bat_analysis/detection.py

Le gagnant est une **proposition** à comparer visuellement et sur un
jeu de validation indépendant avant toute fusion.

### Limites

- L'agent code ne s'exécute pas dans ChatGPT : c'est un programme
  Windows local déclenché par l'utilisateur.
- Cette V1 ne modifie que `bat_analysis/detection.py`, trois essais
  maximum ; elle ne cherche pas encore d'architectures nouvelles.
- Le jeu de test reste inaccessible aux benchmarks de recherche.
- Les données de calibration ne sont pas annotées exhaustivement ;
  le nombre de candidats n'est **pas** une mesure du taux de faux positifs.
- Le score géométrique reste non calibré. Les résultats peuvent
  suradapter la calibration. Aucune amélioration ne doit être présentée
  comme validée hors corpus sans test indépendant.
- Il faut vérifier manuellement les scripts Codex locaux, les résultats
  et les traces avant de conserver ou promouvoir une proposition.
- L'exécution Codex sous Windows peut nécessiter une configuration
  appropriée du sandbox local. Les tests sont présents dans le dépôt,
  mais leur bon fonctionnement Windows reste à confirmer.
