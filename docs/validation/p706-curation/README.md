# Décisions locales P706

Ce fichier est un delta proposé pour `tradition_placement_overrides`, pas un export complet.
L'ajouter à une copie de la liste existante en vérifiant l'unicité de `wikidata_id` ; ne pas
remplacer la liste par ce fichier. Aucune règle n'a été publiée dans grow-api.

- Electro house (Q627906) : Pays-Bas (Q55) comme ancrage principal éditorial. Les deux
  origines déclarées, Pays-Bas et Europe, restent dans les données Bronze. Cette règle
  ne prouve aucune inclusion géographique.
- Pacific reggae (Q7122786) : conserver les trois origines déclarées Mélanésie,
  Polynésie et Nouvelle-Zélande. Aucun ancrage principal éditorial n'est décidé. Le
  contrat actuel ne porte qu'un ancrage : une prise en charge de plusieurs ancrages
  nécessite une évolution coordonnée de Silver, Gold et des consommateurs. Le résultat
  actuel choisi par QID ne constitue pas une décision éditoriale acceptée.

Extremadura (Q5777) est décrite comme communauté autonome actuelle dans
[Wikidata](https://www.wikidata.org/wiki/Q5777), avec un type courant depuis 1983 et un
ancien type daté jusqu'en 1983. Les entrées conservées ne portent pas ces déclarations
P31 et leurs qualificatifs. Avant de reclasser l'entité, enrichir Bronze pour distinguer
le statut administratif courant de son statut historique et composer les contours des
provinces de Badajoz et Cáceres à partir d'une même source. Une simple exception QID ou
un changement global de priorité des types historiques ne résoudrait pas cet invariant.

Paraná et Daghestan : Natural Earth indique que les contours admin-1 doivent correspondre
aux contours admin-0, tout en documentant des défauts de densification de segments au
Brésil. Voir la [documentation de la source](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/).
Les deux écarts observés dépassent la grille source actuelle. Ils restent en conflit
jusqu'à comparaison des géométries brutes et correction sourcée ; aucune tolérance
supplémentaire ni règle `full` n'est proposée pour les masquer.

Les listes `entity_cultural_roles` et `cultural_membership_overrides` sont aussi des deltas
locaux, à ajouter sans remplacer les listes existantes. La qualification culturelle est
indépendante du type principal. L'appartenance administrative est automatique ; le lien
Hawaï → Polynésie s'ajoute à Hawaï → États-Unis et se transmet à ses subdivisions.
Les exceptions `remove` priment sur tous les chemins de transitivité.

La classification administrative actuelle et la composition des deux provinces
d'Extremadura sont désormais implémentées et couvertes par des tests. Leur acceptation
sur un replay Bronze complet reste distincte de cette validation unitaire. Le contour
statistique de la Polynésie demeure inchangé : les liens culturels ne composent pas les
frontières géographiques.
