# Benchmark de précision des annotations automatiques de chirps

**Spécification de travail — v0.1 — 8 novembre 2026**

**Projets concernés :** bat_full_spectrum_processing (calcul) et Bat-Acoustic-Analyser (visualisation, dans un second temps).

**Statut :** proposition à discuter et valider **avant toute implantation**. Les seuils marqués « proposés » ne sont pas des paramètres déjà validés expérimentalement.

## 1. Objectif

Quantifier de manière reproductible :

1. la capacité d'un algorithme à retrouver les chirps présents dans les annotations manuelles ;
2. la fidélité de chaque trajectoire automatique par rapport à la trajectoire manuelle ;
3. la portion de chirp correctement suivie ;
4. les prolongements incorrects, notamment ceux qui remontent sur un écho en fin de chirp ;
5. les faux positifs et les fragmentations, lorsque le jeu de référence permet de les évaluer.

La comparaison doit être possible entre plusieurs branches/commits de détection **sur les mêmes WAV et annotations manuelles**. Les annotations manuelles constituent la vérité de référence ; les résultats automatiques ne doivent jamais les modifier.

### 1.1 Principes retenus

- Normaliser chaque chirp pour que **la pente principale manuelle devienne −45°** dans un repère temps-fréquence.
- Calibrer les deux axes **à partir du chirp manuel seulement**.
- Comparer les trajectoires avec **20 perpendiculaires** tracées en des points répartis le long de la courbe manuelle.
- Pénaliser une trajectoire automatique trop courte.
- Accepter une trajectoire automatique qui continue après la fin manuelle **uniquement si sa continuation suit la tangente de fin manuelle**.
- Favoriser la détection et la précision des chirps à SNR élevé.
- Ne pas fusionner automatiquement des chirps qui se superposent temporellement : des harmoniques peuvent coexister.

### 1.2 Hors périmètre de cette première version

- Identification d'espèces ;
- modification des annotations ;
- apprentissage ou optimisation automatique des paramètres de détection ;
- validation des mesures biologiques (durée, fréquence caractéristique, knee, amplitude) au-delà de leur éventuelle utilisation comme données annexes ;
- préjuger de la valeur des pondérations sans évaluation sur un corpus représentatif.

## 2. Entrées et conventions

Pour chaque WAV :

- échantillonnage, durée et identifiant du fichier ;
- liste des annotations **manuelles**, chacune avec un identifiant et une trajectoire \((t_j,f_j)\) ;
- liste des annotations **automatiques** d'une branche/commit, chacune avec un identifiant et une trajectoire ;
- SNR maximal de **chaque chirp manuel** mesuré par une méthode fixe ;
- information « annotations manuelles exhaustives : oui/non » au niveau du WAV ou d'une fenêtre temporelle.

**Unités :** temps en **ms**, fréquence en **kHz**, SNR en **dB**.

Chaque annotation est représentée par une courbe orientée de son début temporel vers sa fin. Une interpolation **PCHIP** peut être utilisée pour une courbe monotone en temps, sans introduire d'oscillation entre les points ; une interpolation linéaire est le repli sûr.

### 2.1 Validation des entrées

- Exiger au minimum deux points distincts et une durée strictement positive.
- Trier les points par temps ; traiter explicitement les temps dupliqués (fusion contrôlée ou annotation déclarée invalide).
- Ne jamais masquer silencieusement une courbe non monotone en temps : signaler ce cas, voire la découper si la représentation le permet.
- Ignorer les valeurs NaN/infinies en les signalant ; rejeter la courbe si sa géométrie n'est plus définie.
- Ne pas extrapoler une annotation manuelle au-delà de ses extrémités pour calculer sa couverture.

## 3. SNR et tolérance

Le SNR utilisé pour l'évaluation est calculé **sur la trajectoire manuelle** à partir du WAV avec une méthode de mesure figée et identique entre tous les commits. Une modification de l'algorithme automatique ne doit pas changer le SNR de référence.

Soit \(s\) le SNR maximal manuel et \(u=\operatorname{clip}((s-15)/20,0,1)\).

**Tolérance fréquentielle de référence :**

\[
T_f(s) = 5 - 2u \quad \text{(kHz)}
\]

**Poids du chirp dans les statistiques :**

\[
w(s) = 1 + 2u
\]

| SNR manuel | Tolérance de référence | Poids |
|---|---:|---:|
| ≤ 15 dB | 5 kHz | 1 |
| 25 dB | 4 kHz | 2 |
| ≥ 35 dB | 3 kHz | 3 |

Ces fonctions sont continues entre les seuils. Les valeurs 3 et 5 kHz viennent des critères déjà retenus pour le benchmark ; la pondération de 1 à 3 est une proposition à vérifier.

**Attention aux unités :** les 3–5 kHz représentent une tolérance fréquentielle de référence. Le calcul géométrique emploie une distance **perpendiculaire dans le repère normalisé**, qui doit être convertie explicitement (§ 5.3) ; on ne peut pas comparer directement ces deux grandeurs.

## 4. Normalisation à −45°

### 4.1 Estimation de la pente principale

Sur la trajectoire **manuelle uniquement**, estimer la pente principale \(m\) de la relation \(f(t)\) en kHz/ms. Méthode proposée : régression robuste (par exemple Theil–Sen) sur des échantillons répartis régulièrement en distance curviligne.

La pente est globale : **elle sert au calibrage des axes**, pas aux perpendiculaires de mesure, qui utilisent les tangentes locales.

### 4.2 Changement de repère

Pour \(m<0\), choisir un point origine manuel \((t_0,f_0)\) et poser :

\[
X=|m|(t-t_0),\qquad Y=f-f_0.
\]

Ainsi, la pente principale devient \(dY/dX=-1\), soit **−45°**. Les deux coordonnées s'expriment en **kHz équivalents**.

Appliquer **exactement la même transformation** à tous les points automatiques candidats. Ne jamais recalibrer les axes selon leur pente : cela gommerait une erreur réelle de l'algorithme.

Pour un chirp ascendant, on peut inverser le signe de \(Y\) pour obtenir aussi −45° :

\[
Y=-\operatorname{sign}(m)(f-f_0).
\]

Cette généralisation conserve les distances, mais le rapport doit signaler le chirp ascendant.

### 4.3 Cas dégénérés

Une pente presque nulle ne permet pas de rendre le chirp à −45° par un simple changement d'échelle sans amplifier excessivement le temps. **Choix proposé :** si \(|m|<1\) kHz/ms ou si la régression est instable, appliquer une échelle de repli configurable (par exemple 10 kHz/ms), marquer « normalisation dégradée » et **ne pas prétendre que la pente est −45°**.

Ce cas est à tester sur les chirps presque horizontaux et les trajectoires très courbées.

## 5. Mesure sur 20 perpendiculaires

### 5.1 Position des échantillons

1. Interpoler la courbe manuelle dans le repère normalisé.
2. Calculer son abscisse curviligne \(l\).
3. Placer **N = 20** points à intervalles réguliers en \(l\), extrémités comprises.
4. Calculer en chaque point \(M_i\) une **tangente locale** unitaire \(\tau_i\), lissée si nécessaire, puis sa normale unitaire \(n_i\).
5. Construire la droite \(D_i(\lambda)=M_i+\lambda n_i\).

La répartition en distance curviligne évite d'accumuler les mesures dans les parties plates ou surreprésentées par les points d'édition.

### 5.2 Intersection avec l'annotation automatique

Pour chaque droite \(D_i\) :

1. rechercher ses intersections géométriques avec les segments de la courbe automatique dans le repère normalisé ;
2. rejeter les intersections éloignées au-delà d'une fenêtre de recherche proposée de \(2T_\perp\) ;
3. imposer une **progression cohérente** sur la courbe automatique : l'intersection choisie pour \(i+1\) ne doit pas revenir en arrière par rapport à celle de \(i\) ;
4. si plusieurs intersections restent possibles, choisir la séquence **globale** qui minimise les distances tout en respectant l'ordre, plutôt qu'un minimum local indépendant ;
5. si aucune intersection admissible n'existe, marquer ce point **non couvert**.

Cette contrainte évite qu'un croisement, un écho ou une courbe repliée soit associé plusieurs fois à des portions incompatibles de l'annotation manuelle.

### 5.3 Erreur perpendiculaire

Si l'intersection sélectionnée est \(A_i\) :

\[
d_i = (A_i-M_i)\cdot n_i
\]

\(d_i\) est une distance **signée** en kHz équivalents.

Sur une portion dont la tangente vaut −45°, un décalage **vertical** de \(\Delta f\) produit \(|d_i|=|\Delta f|/\sqrt{2}\).

Pour conserver le sens de notre tolérance de référence :

\[
e_i=\sqrt{2}|d_i|,\qquad T_\perp(s)=T_f(s)/\sqrt{2}.
\]

Il est donc équivalent de comparer \(|d_i|\) à \(T_\perp\), ou \(e_i\) à \(T_f\). **La grandeur \(e_i\) est un écart géométrique en « kHz équivalents », pas nécessairement un écart fréquentiel à temps identique**, surtout près d'un knee.

Conserver par point : temps manuel, fréquence manuelle, position dans la courbe, distance signée, distance absolue, intersection automatique, statut couvert/non couvert.

### 5.4 Qualité géométrique

Pour une intersection admissible :

\[
q_i=\exp\left[-\left(\frac{|d_i|}{T_\perp}\right)^2\right] \in [0,1].
\]

Si le point est non couvert, \(q_i=0\) dans le score global du chirp.

Cette fonction pénalise progressivement les écarts sans instaurer une discontinuité au seuil de tolérance. **Fonction proposée** : elle reste à comparer, sur de vrais chirps, à une décroissance linéaire ou de Huber.

Indicateurs diagnostiques supplémentaires : médiane des \(|d_i|\), 90e percentile, maximum, biais signé médian et nombre d'intersections manquantes. Les valeurs sont calculées sur les points effectivement couverts, avec le nombre de points indiqué.

## 6. Couverture et chirp trop court

La couverture est :

\[
C=\frac{N_{\mathrm{couvert}}}{20}.
\]

La qualité des correspondances couvertes est :

\[
Q=\frac{1}{N_{\mathrm{couvert}}}\sum_{\mathrm{couvert}}q_i,
\]

avec \(Q=0\) s'il n'existe aucune correspondance.

**Score de base de la trajectoire :**

\[
S_{\mathrm{base}}=100\,C\,Q=\frac{100}{20}\sum_{i=1}^{20}q_i.
\]

Un chirp automatique couvrant la moitié de la trajectoire manuelle ne peut donc pas dépasser **50/100**, même si la moitié retrouvée est parfaite. Une trajectoire inexistante obtient **0/100**.

Cette pénalité est géométrique et continue ; elle ne suppose pas que les extrémités manuelles et automatiques aient exactement les mêmes dates.

## 7. Chirp automatique plus long : prolongement de fin

On distingue :

- la partie automatique située dans la zone de correspondance avec l'annotation manuelle ;
- la **queue automatique** après la dernière extrémité temporelle manuelle.

### 7.1 Droite de prolongement admissible

Estimer une tangente de fin unitaire \(v_{\mathrm{fin}}\) sur les derniers **10 à 20 %** de longueur de la courbe manuelle (régression robuste locale proposée). Soit \(M_{\mathrm{fin}}\) le dernier point manuel.

Le prolongement idéal est le **rayon orienté** :

\[
R(a)=M_{\mathrm{fin}}+a\,v_{\mathrm{fin}},\qquad a\geq 0.
\]

Une queue peut être acceptée si elle reste dans un **couloir autour du prolongement**, avance dans la bonne direction et ne présente pas de brusque changement d'orientation.

### 7.2 Test géométrique de la queue

Échantillonner la queue automatique en distance curviligne (par exemple 10 à 20 points selon sa longueur). Pour chaque point \(P\) :

- **Avancement :** \(a=(P-M_{\mathrm{fin}})\cdot v_{\mathrm{fin}}\geq0\) ;
- **Écart latéral :** \(r=|(P-M_{\mathrm{fin}})\times v_{\mathrm{fin}}|\), dans le repère 2D normalisé ;
- **Orientation locale :** angle entre la tangente automatique locale et \(v_{\mathrm{fin}}\).

Critères **proposés**, à calibrer :

- 90e percentile de \(r\) inférieur ou égal à \(T_\perp\) ;
- pas d'écart ponctuel supérieur à \(2T_\perp\) ;
- orientation locale généralement dans **±20°** de la tangente de fin ;
- absence de retour en arrière significatif.

Si la queue suit correctement ce prolongement, **aucune pénalité pour sa longueur** (sous réserve d'un contrôle éventuel de longueur excessive, voir § 13). Si elle bifurque vers un écho, seule la **portion non conforme** contribue à la pénalité.

### 7.3 Pénalité d'extension

Noter \(L_{\mathrm{queue}}\) la longueur curviligne de la queue et \(L_{\mathrm{invalide}}\) la longueur qui échoue aux critères de prolongement.

**Pénalité proposée :**

\[
p_{\mathrm{ext}}=\operatorname{clip}\left(
\frac{L_{\mathrm{invalide}}}{L_{\mathrm{manuel}}+L_{\mathrm{queue}}},0,1\right).
\]

**Score individuel final :**

\[
\boxed{S_{\mathrm{chirp}}=100\,C\,Q\,(1-p_{\mathrm{ext}})}
\]

Le rapport conserve séparément \(C\), \(Q\), \(p_{\mathrm{ext}}\), la longueur totale de queue et sa longueur non conforme. La pénalité multiplicative n'est pas figée : on vérifiera sur le corpus qu'une forte dérive finale est sanctionnée suffisamment.

### 7.4 Début automatique trop précoce

En symétrie, une extension **avant le début manuel** peut être testée contre le prolongement en arrière de la tangente initiale. **Proposition à valider** : même règle et même pénalité que pour la fin, sans confondre bruit précédant le signal et véritable chirp.

## 8. Appariement manuel ↔ automatique

Le benchmark ne doit pas comparer arbitrairement une courbe automatique à plusieurs références manuelles.

### 8.1 Génération des paires candidates

Une paire est candidate seulement si :

- les enveloppes temporelles se recoupent (avec une très petite marge configurable) ;
- la bande fréquentielle n'est pas manifestement incompatible ;
- la mesure par perpendiculaires aboutit à un minimum de correspondances cohérentes ;
- la paire n'exige pas de rupture de l'ordre sur la trajectoire automatique.

**Ne pas associer sur la seule proximité temporelle :** deux harmoniques ou deux espèces peuvent occuper le même instant.

### 8.2 Coût et affectation unique

Calculer \(S_{ij}\) entre chaque manuel \(i\) et auto \(j\) candidat. Trouver une affectation **un-à-un** qui maximise la somme des scores de qualité pondérés :

\[
\max\sum_{(i,j)\ \mathrm{appariés}}w_i\,S_{ij}.
\]

Une méthode d'affectation bipartie (par exemple hongroise) avec possibilités « sans correspondant » évite qu'un même auto explique plusieurs manuels.

**Seuil de reconnaissance proposé :** au moins 40 % de couverture, erreur médiane inférieure à \(T_\perp\), et un score individuel strictement positif. Les seuils devront être optimisés **sans utiliser le jeu final de test**.

Les manuels non appariés sont des **chirps manqués**. Un manuel partiellement retrouvé peut être reconnu avec un score faible : cela sépare « détecté » de « bien suivi ».

### 8.3 Fragmentation

Après l'affectation unique, examiner si plusieurs annotations automatiques distinctes couvrent **des portions complémentaires du même chirp manuel**. Enregistrer :

- nombre d'autos impliqués ;
- portions manuelles couvertes par chaque fragment ;
- éventuel chevauchement ou trou entre fragments.

Ne pas réunir silencieusement ces fragments pour calculer le score principal : sinon un algorithme qui produit beaucoup de morceaux serait artificiellement avantagé. Marquer le fragment additionnel comme **fragmentation**, et éviter de le compter simultanément comme un faux positif indépendant dans le score global.

### 8.4 Harmoniques et détections croisées

Deux manuels simultanés à fréquences différentes sont deux références distinctes. Leurs associations sont définies globalement par la proximité **géométrique temps-fréquence**. Ne pas fusionner les harmoniques dans le benchmark, même si une autre partie du pipeline décide de leur relation biologique.

## 9. Indicateurs par WAV et par corpus

### 9.1 Détection pondérée par SNR

\[
R_w=\frac{\sum_{i\in\mathrm{manuels\ reconnus}} w_i}
{\sum_{i\in\mathrm{tous\ manuels}} w_i}.
\]

Publier aussi le rappel **non pondéré**, et le rappel spécifique aux chirps **SNR ≥ 35 dB**. Une absence de manuel dans une catégorie doit donner « non applicable », pas 100 %.

### 9.2 Qualité géométrique et couverture

- Moyenne pondérée de \(Q_i\) sur les manuels reconnus (0 si aucun) ;
- couverture moyenne \(C_w\), en mettant **0** pour les manuels non reconnus ;
- moyenne pondérée du score individuel \(S_{\mathrm{chirp}}\), en mettant **0** pour les manuels non reconnus ;
- médiane et 90e percentile des erreurs perpendiculaires ;
- nombre de dérives de fin, et longueurs de queues non conformes.

Indicateur simple prioritaire, utile pour comparer les commits :

\[
T_w=\frac{\sum_i w_i S_{\mathrm{chirp},i}}{100\sum_i w_i}.
\]

Ici, \(S_{\mathrm{chirp},i}=0\) lorsque le manuel n'a pas d'auto associé.

### 9.3 Précision de détection et faux positifs

Calculer \(P=TP/(TP+FP)\) **uniquement** sur un WAV ou une fenêtre dont les annotations manuelles ont été explicitement marquées **exhaustives**.

Si l'annotation humaine est incomplète, un auto sans correspondance ne peut pas être déclaré faux positif : l'état doit être **« non évalué »** et l'indicateur de précision doit être absent.

Les fragments supplémentaires sont rapportés séparément, avec une convention explicite pour ne pas doubler les pénalités.

### 9.4 Score global de comparaison — proposition

Pour un corpus de référence **entièrement revu**, conserver la décomposition discutée :

| Composante | Poids provisoire |
|---|---:|
| Rappel pondéré par SNR \(R_w\) | 40 % |
| Qualité géométrique \(G_w\) | 30 % |
| Couverture pondérée \(C_w\) | 15 % |
| Précision de détection \(P\) | 10 % |
| Absence de fragmentation \(F\) | 5 % |

\[
S_{\mathrm{global}}=100(0.40R_w+0.30G_w+0.15C_w+0.10P+0.05F).
\]

Définition proposée : \(G_w\) est la moyenne pondérée des **qualités individuelles complètes** \(S_{\mathrm{chirp}}/100\) (0 pour les manuels non reconnus) ; \(F=1-\min(1,N_{\mathrm{manuels\ fragmentés}}/N_{\mathrm{manuels}})\). Les erreurs et les manques influencent donc volontairement plusieurs composantes. Il faut tester si cette double sensibilité est souhaitable.

**Si le corpus n'est pas exhaustif**, ne **pas** afficher ce score global comme s'il était comparable à un corpus complet. Fournir \(R_w\), \(T_w\), couverture, erreurs et fragmentation ; au besoin créer un autre indicateur explicitement nommé « score partiel » et sans \(P\).

### 9.5 Temps d'exécution

Mesurer séparément : chargement WAV, calcul spectrogramme/réassignement, détection, modelling/following, simplification et benchmark. Le temps de benchmark lui-même **ne doit pas** être inclus dans le temps de l'algorithme évalué.

## 10. Sorties et traçabilité

### 10.1 Niveau chirp

Pour chaque manuel :

- WAV, identifiant manuel et identifiant auto associé ;
- SNR manuel, poids et tolérance ;
- pente principale et statut de normalisation ;
- 20 positions manuelles, intersections automatiques, erreurs signées et scores \(q_i\) ;
- couverture, qualité géométrique, longueur de queue, pénalité de prolongement, score final ;
- statut : reconnu, manqué, partiel, fragmenté, problème de normalisation, etc.

Pour chaque auto non apparié : identifiant et statut (fragment, faux positif **si exhaustif**, sinon non évalué).

### 10.2 Niveau expérience

- identifiants des WAV et empreintes des WAV + annotations manuelles ;
- nom du détecteur, branche Git et **SHA complet du commit** réellement exécuté ;
- paramètres de détection, de SNR et de benchmark ;
- version du protocole de score, nombre de chirps manuels et automatiques ;
- indicateurs par WAV, par tranche de SNR et pour le corpus ;
- durée de calcul et diagnostic des erreurs.

**Formats proposés :** JSON exhaustif + CSV de synthèse par chirp et par WAV. Des graphes comparatifs pourront être ajoutés plus tard dans Bat-Acoustic-Analyser.

## 11. Pipeline proposé

1. Charger le corpus WAV/annotations manuelles **figé**.
2. Calculer ou charger les SNR manuels avec la méthode de référence figée.
3. Exécuter une branche/commit et enregistrer ses autos sans toucher aux manuelles.
4. Nettoyer/valider la représentation des courbes.
5. Pour chaque manuel, calculer pente, transformation et 20 normales.
6. Présélectionner les autos candidates dans le même WAV.
7. Mesurer chaque paire candidate : intersections ordonnées, erreurs, couverture, prolongements, score.
8. Résoudre l'appariement un-à-un, puis identifier fragmentations et autos non appariés.
9. Calculer les métriques par chirp/WAV/SNR/corpus et les durées d'exécution.
10. Exporter les données et comparer les mêmes métriques entre commits.

Toute erreur sur un WAV doit être rapportée sans arrêter nécessairement le traitement des autres WAV. Un benchmark partiellement échoué ne doit pas être présenté comme un score global complet.

## 12. Jeux de tests à préparer avant la mise en œuvre

| Cas | Attendu |
|---|---|
| Auto identique au manuel | Score individuel ≈ 100 ; 100 % couverture ; aucune pénalité |
| Auto décalé perpendiculairement | Score décroissant avec l'écart |
| Auto décalé uniquement le long de la tangente | Comparaison géométrique peu sensible au décalage intérieur ; les extrémités/recouvrements détectent la perte de couverture |
| Auto limité à la première moitié | Score individuel ≤ 50 |
| Auto limité à la seconde moitié | Score individuel ≤ 50 |
| Auto continue droit après la fin | Queue admise, sans pénalité |
| Auto bifurque vers un écho après la fin | Pénalité de prolongement |
| Auto remonte en fin de trajectoire | Pénalité importante si la remontée sort du couloir |
| Auto commence trop tôt | Marquage et éventuelle pénalité initiale |
| Manual avec knee | Normales locales correctes, sans approximation par une seule droite |
| Deux harmoniques simultanées | Deux appariements indépendants |
| Un manuel découpé en deux autos | Une seule association principale + fragmentation |
| Deux manuels et un seul auto | Un-à-un : impossible de créditer les deux |
| Aucun auto | Score de trajectoire 0 ; rappel 0 |
| Aucun manuel | Rappel non applicable ; faux positifs seulement si zone exhaustive |
| SNR 12 / 25 / 40 dB | Tolérances 5 / 4 / 3 kHz ; poids 1 / 2 / 3 |
| Chirp quasi horizontal | Repli de normalisation documenté, jamais division par zéro |
| Points manuels dupliqués ou incohérents | Erreur explicite ou nettoyage traçable |
| Même entrée, même configuration | Résultats déterministes, indépendants de l'ordre des autos |
| Annotations manuelles incomplètes | Aucune assertion infondée de faux positifs |

Prévoir des tests de sensibilité aux choix de **10, 20 et 40 perpendiculaires**, aux extrémités et au lissage des tangentes.

## 13. Paramètres initiaux — à discuter

| Paramètre | Proposition v0.1 | Statut |
|---|---|---|
| Nombre de perpendiculaires | 20 | Proposition utilisateur |
| Répartition des points | Distance curviligne uniforme, extrémités incluses | Proposé |
| Pente principale | Régression robuste sur le manuel | Proposé |
| Tolérance SNR bas / haut | 5 kHz / 3 kHz | Déjà convenu |
| SNR bas / haut | 15 dB / 35 dB | Déjà convenu |
| Poids SNR bas / haut | 1 / 3 | Proposé |
| Fenêtre maximale de recherche sur normale | \(2T_\perp\) | Proposé |
| Couverture minimale pour dire « reconnu » | 40 % | Proposé |
| Estimation tangente de fin | Derniers 10–20 % du manuel | Proposé |
| Tolérance latérale de queue | \(T_\perp\) au 90e percentile | Proposé |
| Écart latéral extrême de queue | \(2T_\perp\) | Proposé |
| Variation d'orientation de la queue | ±20° | Proposé |
| Pente trop faible pour normalisation stricte | 1 kHz/ms | Proposé |
| Échelle de repli | 10 kHz/ms | Proposé |
| Extensions en début de chirp | Contrôle symétrique | À valider |
| Queue très longue mais parfaitement rectiligne | Acceptée sans pénalité | À valider |
| Score individuel | \(100CQ(1-p_{\mathrm{ext}})\) | Proposé |
| Score global complet | 40 / 30 / 15 / 10 / 5 | À calibrer |

## 14. Questions ouvertes pour notre prochaine discussion

1. **Normales locales ou fixes à +45° ?** Le document retient les tangentes **locales**, afin de respecter les knees. Vérifier que cela correspond à la géométrie souhaitée.
2. **Extrémités :** inclure les deux points de bout dans les 20, ou éviter leurs tangentes moins stables (par exemple 5 % de marge), tout en mesurant la couverture des bouts séparément ?
3. **Tolérance :** conserver \(T_f/\sqrt{2}\) comme corridor géométrique de référence, ou calibrer empiriquement le corridor perpendiculaire sur le corpus manuel ?
4. **Prolongement excessif :** une queue parfaitement alignée mais deux fois plus longue que le chirp doit-elle rester gratuite ?
5. **Pondération des manques :** la formule \(100CQ\) donne une très forte pénalité aux trajectoires raccourcies ; est-ce bien l'effet recherché ?
6. **Fragmentation :** une association principale plus un fragment supplémentaire, ou autoriser une union des fragments pour un score diagnostique secondaire (jamais pour le score principal) ?
7. **Faux positifs :** quels WAV/fenêtres avons-nous réellement annotés de manière exhaustive ?
8. **Score global :** conserver une composition de 5 métriques, ou faire du score de trajectoire pondéré \(T_w\) l'indicateur principal à côté du rappel de détection ?

---

**Décision de travail recommandée :** valider d'abord les règles géométriques et les 15–20 cas tests illustratifs, puis figer une version **v1.0 du protocole** avant toute optimisation de l'algorithme de détection sur ce benchmark. Un jeu de validation séparé du jeu final de test sera nécessaire pour éviter de suradapter Reassignment-v2 aux seuls WAV connus.
