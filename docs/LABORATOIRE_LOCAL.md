# Laboratoire autonome local V1 — Windows

Le premier orchestrateur autonome reste **entièrement sur le PC** :
aucun WAV, JSON ni rapport n'est envoyé vers GitHub/Codex ou un service
externe. GitHub ne transporte que le code source des outils.

## Rôle de chaque composant

- `laboratory/local_campaign.py` : orchestrateur d'expériences avec
  état SQLite, reprise, budget d'essais et rapport HTML.
- `laboratory/sensitivity_audit.py` : expérience acoustique réelle,
  limitée à la partition calibration, avec perturbations en mémoire.
- `laboratory/local_quality.py` : mesure locale glissante,
  partagée avec l'audit V2.
- `campaign.sqlite` : historique des exécutions et résultats
  par seuil et par type d'erreur.
- `campaign_scores.csv` : résultats comparatifs, y compris
  la région début/milieu/fin.
- `campaign_report.html` : tableau comparatif autonome.
- `campaign_summary.json` : résultats machine-lisibles pour
  un futur agent Codex.

**Il n'est pas encore question de laisser Codex modifier le benchmark
de manière autonome.** Le benchmark reste indépendant, et les résultats
ne désignent pas automatiquement un gagnant.

## Démarrage Windows

Dans PowerShell, depuis le clone de Bat-Chirp-Laboratory :

~~~powershell
git pull origin main
python -m pytest -q tests/test_local_campaign.py tests/test_sensitivity_audit.py tests/test_local_quality.py

python tools/run_local_campaign.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\autolab-v1" `
  --skip-chirps 3 `
  --max-chirps 30 `
  --windows 3,4,5,7 `
  --thresholds 0.75,1,1.5,2,2.5 `
  --max-trials 1
~~~

Ce premier démarrage ne réalise qu'**une nouvelle évaluation acoustique**
(une taille de fenêtre) ; il teste ensuite toutes les valeurs de seuil
sur les résultats mémorisés. Pour reprendre les autres tailles de fenêtre,
relancer exactement la même commande avec `--max-trials 3`.
Les essais déjà terminés ne sont pas recalculés.

Lors de chaque exécution, les logs sont dans
`autolab-v1/runs/<run_id>/execution.log` et les sorties
acoustiques de chaque configuration sont conservées séparément.
Si un essai échoue, inspecter son log puis relancer avec
`--retry-failed`.

Pour un smoke test plus petit, remplacer le nombre de chirps par
`--max-chirps 3` et choisir un **nouveau** répertoire
`--output`. Les variantes sont ainsi non comparables
à la campagne de 30 chirps tant qu'elles ne portent pas sur les mêmes
références.

## Pourquoi recalculer par longueur de fenêtre ?

La V1 exécute une analyse acoustique complète une seule fois
par **taille de fenêtre**, puis balaie **tous les seuils sans coût
acoustique additionnel**. Cela réduit déjà significativement les
aller-retour. Une étape ultérieure consistera à stocker les 20 écarts
normaux élémentaires de chaque trajectoire pour tester également
les tailles de fenêtre sans recalcul acoustique.

Le moteur n'a pas besoin d'Optuna à ce stade : l'exploration
cartésienne limitée est reproductible et plus facilement vérifiable.

## Reproductibilité et limites

Chaque essai est identifié par une empreinte portant sur les
paramètres, le manifeste, les métadonnées des WAV/annotations et
le code Python principal du laboratoire. Les mises à jour du code
ou des fichiers source entraînent de nouveaux essais.

Les *flags* de référence sont comptés **par perturbation**, pas par
chirp distinct. L'absence d'annotation experte confirmée comme correcte
interdit de convertir ces nombres en faux positifs ou en précision.
Le gain ne doit pas être évalué seulement au nombre d'alertes :
on doit aussi examiner les fenêtres spatiales des erreurs et
le comportement sur des données jamais utilisées pour sélectionner
les paramètres.

Le jeu \`test\` est exclu de l'exploration. Les originaux restent inchangés.
Le cache des WAV n'est pas encore implémenté ; l'analyse peut rester
coûteuse si le nombre de chirps ou de tailles de fenêtre augmente.
