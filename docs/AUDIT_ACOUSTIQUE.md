# Audit acoustique des annotations manuelles — même image 65/35 que l'annotateur

L'audit réutilise **directement** la fonction
`bat_analyser.spectrogram_pipeline.compute_display_representation`
du dépôt `Bat-Acoustic-Analyser`. Il ne recrée pas la fusion dans le laboratoire.

## Réglages par défaut

- **65 % STFT Flattop / 35 % Reassignment Flattop** sur les
  **amplitudes linéaires**, puis conversion en dB.
- FFT STFT = 1024, recouvrement STFT = 75 % (options du logiciel
  configurables ; le mode « Auto (zoom) » peut produire un autre recouvrement).
- Reassignment Flattop FFT = 512, hop adaptatif calculé par l'application.
- Soustraction spectrale désactivée par défaut (alpha=0) ;
  fournir `--spectral-alpha 1.7` si elle était active dans l'annotateur.
- Maximum 1600 colonnes par extrait ; un grand zoom peut donc donner
  une résolution différente de celle de l'interface.

Cette représentation est **l'affichage de l'annotateur**, pas l'algorithme
de détection automatique du dépôt `bat_full_spectrum_processing`.
Le tracker automatique emploie aussi un ratio numérique 65/35, mais dans
l'autre sens et après une autre transformation : 65 % d'évidence
Reassignment et 35 % d'évidence STFT, avec contraste en dB et lissage.
**Les deux mélanges ne sont pas équivalents.**
Les métriques produites sont des contrastes et des écarts estimés sur
l'image mélangée ; leur SNR local n'est pas le SNR Flattop 512 du benchmark
de détection et ne doit pas être comparé directement aux seuils 15/35 dB.

L'audit charge un court extrait autour de chaque trajectoire manuelle et
échantillonne 20 points. Il estime le bruit sur les colonnes hors du chirp,
puis mesure le contraste local et l'écart fréquentiel à la crête dominante
dans une bande de ±12 kHz. Les valeurs produites sont :

- `snr_median_db`, `snr_max_db`, `snr_p10_db` :
  contraste local en dB de la représentation mixte ;
- `ridge_offset_median_khz`, `ridge_offset_p90_khz` ;
- `support_fraction_3khz` : fraction des 20 points
  situés à ±3 kHz de la crête locale dominante.

Les étiquettes `strong_support_unverified`,
`moderate_support_unverified` et
`review_first` ne sont que des priorités de **revue
humaine** : une harmonique, un écho ou une autre chauve-souris peuvent
décaler le maximum spectral.

## Exécution Windows — pilote 10 chirps

Depuis la racine de Bat-Chirp-Laboratory :

~~~powershell
git pull origin main
python -m pip install -r requirements.txt
python tools/audit_acoustic.py `
  --dataset "C:\Users\damien\Documents\Python\Python-data\dataset-v1" `
  --output "C:\Users\damien\Documents\Python\Python-data\acoustic-blend-v1" `
  --analyser-root "C:\Users\damien\Documents\Python\Python-Toolbox\Bat-Acoustic-Analyser" `
  --reassignment-percent 35 `
  --fft-size 1024 `
  --overlap 0.75 `
  --spectral-alpha 0 `
  --max-chirps 10
~~~

Le paramètre `--analyser-root` est facultatif si le dépôt
Bat-Acoustic-Analyser est dans le dossier voisin de Bat-Chirp-Laboratory.
Comparer ses propres réglages dans l'interface avant une grande campagne.
Un recouvrement de 87,5 % se spécifie avec `--overlap 0.875`.

Après le pilote, enlever `--max-chirps 10` et utiliser un
**nouveau répertoire** de sortie pour la campagne complète.

## Rapports

- `acoustic_chirps.csv` : indicateurs par trajectoire ;
- `acoustic_summary.json` : paramètres exacts du calcul,
  scores de priorité de revue et nombre d'erreurs ;
- `acoustic_errors.json` : anomalies éventuelles.

Seules les annotations `source="manual"` et
`status="validated"` de **calibration** sont évaluées.
Le test indépendant n'est jamais ouvert. L'audit ne modifie aucun fichier
du corpus et ne certifie pas la justesse acoustique des annotations.
