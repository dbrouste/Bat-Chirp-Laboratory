# Audit acoustique du corpus manuel

Cette phase mesure un **soutien spectral local**, pas la justesse absolue
d'une annotation. Le calcul reste indépendant des algorithmes candidats et
ne modifie ni WAV, ni JSON, ni la répartition calibration/test.

Méthode actuelle : fonction existante
\`bat_analysis.reassignment.reassigned_spectrogram\` du dépôt
\`bat_full_spectrum_processing\`, fenêtre Flattop 512 et hop 64
(87,5 % de recouvrement). Une fenêtre courte contenant le chirp et
15 ms de marge de chaque côté est lue dans le WAV, sans charger tout
l'enregistrement. Le signal est converti en mono par moyenne des canaux.
Vingt points sont échantillonnés régulièrement entre les bornes manuelles.

Pour chaque point : calcul d'un rapport local puissance sur bruit
estimé dans les trames temporelles situées hors du chirp (marge de 2 ms),
et mesure de la distance à la crête réassignée dominante dans une bande
de ±12 kHz et trois trames voisines. Les fréquences dans ±3 kHz de la
référence servent à estimer le signal. Les métriques produites sont :

- \`snr_median_db\`, \`snr_max_db\`, \`snr_p10_db\` : SNR relatifs, non calibrés ;
- \`ridge_offset_median_khz\`, \`ridge_offset_p90_khz\` ;
- \`support_fraction_3khz\` : fraction des 20 points à moins de 3 kHz
  de la crête dominante, avec énergie réassignée non nulle.

Trois étiquettes de **priorité de revue** sont attribuées uniquement pour
aider au tri : \`strong_support_unverified\`,
\`moderate_support_unverified\`, \`review_first\`. Elles ne constituent
pas une validation A/B/C. Un écho, une harmonique ou un autre chirp
peut dominer la crête réassignée et produire un faux signalement.

## Première exécution Windows

~~~powershell
git pull origin main
python -m pip install -r requirements.txt
python tools/audit_acoustic.py --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" --output "C:\Users\damien\Documents\Python\Python-data\acoustic-v1" --processing-root "C:\Users\damien\Documents\Python\Python-Toolbox\bat_full_spectrum_processing" --max-chirps 10
~~~

Le paramètre \`--processing-root\` doit désigner un clone réel du dépôt ;
adapter le chemin si nécessaire. Après vérification du pilote de 10 chirps,
supprimer \`--max-chirps 10\` pour analyser toute la calibration.

Résultats :
- \`acoustic_chirps.csv\` (une ligne par chirp) ;
- \`acoustic_summary.json\` (comptage des priorités et erreurs) ;
- \`acoustic_errors.json\` (anomalies rencontrées).

Le jeu de test est **toujours exclu**, sans option pour l'activer.
Aucune annotation ne sera supprimée ou corrigée automatiquement.
