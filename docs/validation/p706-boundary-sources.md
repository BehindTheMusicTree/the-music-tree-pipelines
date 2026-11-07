# Sources possibles pour les contours manquants — 7 novembre 2026

Décision utilisateur : conserver les régions et les genres et signaler les contours manquants.
Cette décision autorise une géométrie absente ; elle ne valide pas les changements d'ancrage.

Un contour manuel est possible s'il matérialise une définition documentée. Conserver son QID,
sa source, sa licence, sa version, sa période éventuelle, les composants retenus et son statut
de complétude. Un contour cartographique approximatif ne devient pas automatiquement une
preuve d'inclusion totale.

Deux usages doivent être distingués lors de l'intégration : un contour documenté comme
approximatif peut servir à l'affichage, tandis qu'une preuve d'inclusion totale exige une
frontière suffisamment établie et complète. Cette distinction reste à représenter dans le
contrat de données avant d'ajouter des contours approximatifs ; le pipeline actuel ne doit
pas les interpréter implicitement comme des preuves.

| Région | Piste à vérifier | Travail restant |
|---|---|---|
| Péninsule Ibérique | Contour physique sourcé ou découpage manuel documenté | Définir la limite terrestre ; exclure les îles et les territoires ultramarins. |
| Caucase | Définition géographique documentée et contour manuel | Choisir le périmètre ; une union de pays ne suit pas nécessairement la région physique. |
| Balkans | Composition ou découpage manuel | Documenter la limite nord et les portions de pays retenues. |
| Nations celtiques | Composition explicite de territoires | Choisir la définition culturelle et vérifier les dépendances insulaires. |
| Europe centrale | Composition explicite de territoires | Fixer la convention retenue et sa provenance. |
| Joseon | OpenHistoricalMap et cartes historiques coréennes | Trouver un polygone exploitable, vérifier sa source et sa période. Aucun contour téléchargé ou validé. |
| Moyen-Orient | Composition explicite de territoires | Fixer la définition retenue. |
| Bassin méditerranéen | Donnée terrestre de bassin ou définition culturelle | Distinguer bassin versant, région culturelle et mer ; un polygone marin ne suffit pas. |
| Îles du Pacifique | Inventaire explicite des îles et groupes d'îles | Documenter les exclusions, l'antiméridien et la complétude. |
| Petites Antilles | Inventaire explicite des îles et groupes d'îles | Vérifier chaque composant et les parties de territoires administratifs. |
| Océan Indien (`Q1239`) | Marine Regions, limites IHO | Vérifier le contour marin et définir les terres associées : l'eau ne constitue pas un footprint musical terrestre. |
| Îles est-africaines (`Q28901162`) | Inventaire explicite d'îles | Définir les composants et la complétude. |
| Caraïbes méridionales (`Q7569750`) | Inventaire explicite d'îles | Définir la sous-région ; ne pas reprendre toute la composition des Caraïbes. |
| Monde musulman (`Q779924`) | Définition culturelle documentée | Une union d'États exige une convention explicite et ne représente pas nécessairement les populations concernées. |

## Ressources effectivement vérifiées

- [geoBoundaries, API](https://www.geoboundaries.org/api.html) expose les métadonnées et liens
  de téléchargement des frontières administratives. L'endpoint
  [Brésil ADM1](https://www.geoboundaries.org/api/current/gbOpen/BRA/ADM1/) annonce 27 unités,
  une année représentée 2006, une licence CC BY 2.5 et une source Wikimedia Commons.
  L'endpoint [Russie ADM1](https://www.geoboundaries.org/api/current/gbOpen/RUS/ADM1/) annonce
  83 unités, une année représentée 2017 et une licence ODbL 1.0, avec une origine OSM/Wambacher.
  Ces métadonnées confirment une alternative pour Paraná et le Daghestan ; elles ne prouvent
  pas encore la qualité des contours ni leur compatibilité avec les frontières Natural Earth.
  Remplacer seulement un enfant par une source différente peut créer d'autres désaccords de bord.
- [OpenHistoricalMap, projet Corée](https://wiki.openstreetmap.org/wiki/OpenHistoricalMap/Projects/Korea)
  documente les dates des objets et distingue les lieux ponctuels des frontières.
  Cela confirme une piste de recherche, pas la présence d'un contour Joseon déjà utilisable.
- [Atlas national coréen](https://nationalatlas.ngii.go.kr/pages/page_554.php) décrit des cartes
  de Joseon de plusieurs périodes. Une numérisation manuelle doit retenir une période et une
  source précise ; la péninsule actuelle ne représente pas automatiquement toute son histoire.
- [Marine Regions, téléchargements](https://www.marineregions.org/downloads.php) fournit des
  contours d'océans et de mers. Leur adéquation à une région terrestre reste à établir.
- [Natural Earth, Physical Labels](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-physical-labels/)
  décrit explicitement une couche de labels et avertit de l'imprécision de ses frontières
  physiques pour les opérations géométriques. Elle ne remplace pas une frontière validée.

Aucune nouvelle source de contours n'est intégrée au pipeline sur la seule base de cette recherche.
Les données existantes restent reproductibles ; les cas non établis restent manquants ou incomplets.

## Compositions intégrées le 7 novembre 2026

Les deux compositions utilisent Natural Earth, version épinglée par `NATURAL_EARTH_VERSION`,
en domaine public : [sources versionnées](https://github.com/nvkelso/natural-earth-vector),
[licence](https://www.naturalearthdata.com/about/terms-of-use/).
Les sélecteurs exacts et la version sont conservés dans le Parquet Bronze.

- Nations celtiques (`Q236626`) : les six nations décrites par
  [Transceltic](https://www.transceltic.com/about-celtic-nations).
  Unités `GU_A3` IRL, NIR, SCT, WLS, IMN ; subdivisions `iso_3166_2`
  GB-CON, FR-22, FR-29, FR-35, FR-44, FR-56. La Bretagne retenue est historique,
  avec Loire-Atlantique ; l'Irlande comprend les deux juridictions actuelles.
- Balkans (`Q23522`) : convention large d'affichage de onze pays entiers,
  unités `ADM0_A3` ALB, BIH, BGR, HRV, GRC, KOS, MNE, MKD, ROU, SRB, SVN.
  Les frontières des pays ne prétendent pas définir une limite culturelle ni historique.
  La Turquie européenne est omise plutôt que remplacée par toute la Turquie :
  le contour est donc déclaré incomplet.

Les deux compositions portent `usage=display`. Même la composition celtique complète
pour sa convention ne prouve aucune inclusion. Aucun contour actuel n'est attribué à Joseon.
