# Contrat — conserver les régions sans contour dans la carte

Le défaut de conception est dans le contrat Gold : l'existence d'une région dépend de sa
représentation géométrique. Une région sans contour est éliminée des zones candidates ;
le fallback `regional_parent` peut ensuite déplacer ses genres. Silver conserve pourtant
l'identité et l'ancrage. Corriger seulement le frontend ne peut pas récupérer ces données.

## Changement proposé

Dans `the-music-tree-pipelines`, Gold conserve les régions géographiques ancrées même sans
contour. Chaque région exportée porte un `boundaryStatus` (`available` ou `missing`).
Pour `missing`, `bbox`, `mainBbox` et `centroid` sont nulls ; aucun polygone ni point de
remplacement n'est créé dans PMTiles. Les genres restent attachés à leur région d'origine,
ce qui les exclut du fallback vers un ancêtre. Le rapport de frontières manquantes reste
explicite. Les niveaux de navigation référencent les régions conservées, même lorsqu'elles
ne sont pas sélectionnables par un clic géométrique.

Dans `grow-the-music-tree-api`, la validation d'import vérifie la cohérence du statut et des
champs de géométrie, ainsi que les références de navigation. Le stockage de l'index existe
déjà ; aucune migration SQL n'est nécessaire.

Dans `grow-the-music-tree-frontend`, le schéma strict accepte ces régions. Une liste dédiée
rend accessibles les régions sans contour, puis leur panneau de genres. Le panneau indique
l'absence du contour et ne propose pas de zoom vers une position inventée. Les couches et
la caméra utilisent uniquement les régions ayant une géométrie. Les anciens index sans
`boundaryStatus` restent lisibles pendant la transition de contrat.

Un ajout uniquement dans le rapport Gold serait plus petit, mais ne préserverait pas ces
régions dans la carte consultable. Des coordonnées de substitution rendraient le contrat
inchangé au prix d'une localisation trompeuse. La représentation explicite est préférable.

## Coût, risques et validation

Trois PR de code dans des worktrees distincts : export et schéma Gold, validation API,
puis schéma et interactions frontend. Aucun changement de curation, publication, déploiement
ou release n'est inclus dans cette proposition.

Le risque principal est le rejet de l'index par le schéma strict ou l'utilisation de coordonnées
nulles par la caméra. Un même fixture de région sans contour doit traverser les trois couches.
Les tests vérifient la conservation du QID et des genres, l'absence de déplacement vers
l'ancêtre, l'absence de fausse géométrie, l'import, l'accès au panneau et la caméra.
Les index existants et les villes doivent rester couverts par les régressions.

## État

L'intégration du catalogue manuel et les preuves topologiques sont réalisées dans le worktree
P706. L’utilisateur a autorisé cette extension aux deux dépôts grow le 7 novembre 2026.
Les trois couches sont implémentées dans des worktrees distincts et restent sans déploiement. Les huit différences d'ancrage et les définitions culturelles non fixées
restent ouvertes indépendamment de cette décision.
