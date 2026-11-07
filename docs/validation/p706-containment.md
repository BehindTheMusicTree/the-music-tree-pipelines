# P706 validation — 7 November 2026

Status: acceptance remains under investigation. This report does not establish PR readiness.

## Reproduction

Use the dedicated `feature/p706-containment` worktree. The local snapshot lives outside the
working tree in the repository's `.git/agent-evidence/` directory; source data and credentials
are not committed. `p706-replay-v2-20261107` contains `bronze/`, `wikidata/`, `curation/` and
`silver/`. The six island checks use interior geographic witnesses independently of the
physical-label layer and containment graph:

```sh
uv run --package geohistory python docs/validation/p706_topology.py \
  /absolute/path/to/.git/agent-evidence/p706-replay-v2-20261107 \
  /tmp/p706-topological-acceptance.json
```

All seven target country-to-island/region relations are partial. All six island contours
are topologically equal to independently selected connected land components, including the
foreign portions of Ireland, Borneo and New Guinea. Clipped coverage unions differ at most
by the six-decimal coordinate grid: maximum observed Hausdorff distance is 0.000000620 degrees.
The verifier checks both distance and symmetric difference against the source precision;
it does not convert a partial relation into full based on an overlap percentage.

## Observations and limits

The original geographic caches were incomplete for administrative statements. Completing
4,261 missing hierarchy subjects/objects required 86 cached API batches on 7 November.
These observations are newer than the 6 November input snapshot and cannot reconstruct
its historical source state. The anchor comparison uses identical music inputs for both
versions, copied from staging at 19:16–19:27 UTC on 6 November, distinct from the earlier
geographic snapshot. Preserve these observation times when interpreting differences.

Before restoring city placement, the completed replay produced 5,804 full, 35 partial and
4,023 unknown decisions over 9,862 pairs (13,459 source candidate rows), including 323
conflicts. It contained 13,630 geographic statements, 2,055 qualifiers, 59 scoped statements,
96 referenced parts (47 in the entity catalog), 4,355 administrative path statements and
4,627 independent reference boundaries, including 14 islands. Runtime was 152.40 seconds,
peak RSS 1,527,861,248 bytes on macOS. These pre-city numbers are retained only as historical evidence; the final replay is recorded below.
Geometry uses cached normalized references and spatial indexes; coverage does not form a
global pairwise product. Replay makes no HTTP calls after the cached inputs are materialized.

The 37 sensitive regions are listed in `p706-sensitive-regions.json` with changed admin IDs
and geometry sources. Ten lack a reliable drawn boundary: Iberian Peninsula, Caucasus,
Balkans, Celtic nations, Central Europe, Joseon, Middle East, Mediterranean Basin, Pacific
Islands and Lesser Antilles. Point footprints may still locate them; they never become
region boundaries. The four fragile footprints Q1239, Q28901162, Q7569750 and Q779924 have
no independent reference boundary and must remain explicitly missing or incomplete.
The user selected retention of regions and genres with explicit missing-boundary reporting.
Alternative sources and manual composition options are assessed in `p706-boundary-sources.md`.
Silver retains the entities and anchors. Before the missing-boundary contract change, Gold required geometry for a drawable zone
and could move its genres through fallback. The current implementation preserves anchored
geographic regions in the index with null geometry and excludes them from tiles.

## Contracts and review

Staging grow-api's authenticated GET `curation/export/` returned HTTP 200 on 7 November and
included an empty `place_relation_overrides` list. No curation was published. The initial
403 came from manually parsing an interpolated environment file; using the existing dotenv
loader resolved it without changing configuration.

Independent review identified a current P131 path erasing contradictory geometric evidence.
The fix retains endpoint geometric evidence alongside administrative path evidence, yielding
unknown/conflict when independent polygons contradict full containment. Regression coverage
includes scoped, expired, historical and current paths with crossing polygons.

A same-input anchor replay also exposed city placement regressions. Coordinate-based
containment is restored only for a city point within an admin-unit polygon, after resolved
claim placements. It never supplies a regional polygon. The independent reviewer found no
further issue in the city-specific changes; the priority regression additionally verifies
that a resolved P131 parent suppresses the coordinate fallback.

The previously pushed code passed 1,031 unit tests with 93.15% coverage, Ruff/format and
mypy (115 source files). Its PR CI also passed MusicBrainz integration and combined coverage.
The current precision correction passed 1,032 unit tests (21 deselected), with 93.16% coverage,
and mypy checked 115 source files successfully. Previous grow-api validation logged
661 passed, 1 skipped, 128 deselected and 88.52% coverage; this is prior evidence, not a fresh
validation of a changed grow-api checkout.

The city replay retained all 2,616 anchors and reduced the differences from 24 to 14.
The source-precision correction retains all 2,616 and reduces those differences to eight: six
previously changed anchors (two in Jawa Tengah, Boston, Roseau, Kalmyk and Greater Accra)
are restored. Paraná and Dagestan retain geometric conflicts beyond one source grid unit.
`p706-anchor-differences.json` records every remaining difference, labels, outgoing relation
decisions and the concrete explanation. New York City, London, Galicia and Catalonia are
restored. Remaining broader-country fallbacks caused by administrative geometry conflicts,
and the Korea/Netherlands/Melanesia competing-origin changes, still require business acceptance or
additional boundary work. They are not silently accepted by this report.


## Source precision correction

Independent six-decimal borders can leave narrow slivers after normalization. The resolver
requires positive-area overlap and checks that the entire child lies within one coordinate
grid unit of the parent; it never uses the fraction of overlapping area. Larger excursions,
remote detached components, disjoint polygons and boundary-only contact do not gain full
containment. Clipping the parent around the child bounds limits the buffering cost.

Fresh independent incremental review found no actionable correctness issues; its 18 focused
tests passed. The reviewed code preserves the temporal, completeness and circularity guards,
conflict aggregation and actual clipped map coverage.

The fresh topology replay also passed: all seven target relations remain partial, all six
island boundaries equal the independent land components, and every clipped coverage union
is within source precision (maximum Hausdorff distance 6.1997e-7 degrees). The full replay
produced 5,938 full, 35 partial and 3,909 unknown decisions in 521.576 seconds, with peak RSS
1,095,610,368 bytes under concurrent load. The anchor difference artifact explicitly lists
the six restored anchors as well as the eight differences still requiring resolution.

## Contrat des contours manuels

Le catalogue GeoJSON versionné est désormais chargé avant la résolution des relations.
Les champs obligatoires couvrent QID, source, licence, version, définition, composants,
complétude, usage et période. Un contour `display` est dessinable mais ne peut ni prouver
une inclusion ni entrer dans une composition de preuves. Un contour `containment` doit
être complet et reste soumis aux contrôles temporels. Le catalogue est vide : aucune
piste de recherche n'a été transformée implicitement en frontière acceptée.

Validation locale : 188 tests geohistory passent ; mypy ne trouve aucune erreur dans
28 fichiers source. Un test supplémentaire vérifie la matérialisation d'un contour manuel
sans footprint administratif. Ruff et le formatage passent.

L'examen des branches distantes `origin/develop` des deux consommateurs confirme que
le frontend grow valide strictement chaque région et exige `bbox`, `mainBbox` et `centroid`.
L'import grow-api conserve l'index, vérifie les genres et les identifiants de navigation,
mais ne fournit aucune représentation d'une région dépourvue de contour.
Le correctif de conservation dans la carte implique donc un contrat commun et une évolution
frontend ; ajouter seulement des valeurs nulles dans Gold casserait le chargement actuel.


## Reprise du 7 novembre — contrat commun et validation

Les compositions Natural Earth v5.1.2 des nations celtiques et des Balkans sont réservées
à l'affichage. La convention balkanique reste incomplète (Turquie européenne absente).
Le parquet a été régénéré avec `source_url` et `license`. Joseon reste sans contour.
Le replay des relations, comparé sur `(child_id, parent_id, extent, conflict)` aux preuves
précédentes, est identique : 5 938 full, 35 partial, 3 909 unknown. Les contours display
n'introduisent donc aucune nouvelle preuve d'inclusion. Le résumé chronométré copié dans
le répertoire de replay ne constitue pas une mesure de cette reprise.

Gold conserve Joseon et son genre Q99, sans fallback vers le parent ni feature/label dans
les entrées de tuiles. Le même index généré par le test Gold a traversé les validateurs de
frontière/navigation API puis le schéma frontend : identité, genre, coordonnées nulles et
navigation conservés. Les tests d'import PostgreSQL restent à exécuter en CI.

Validation locale de cette version : 1 046 tests pipelines réussis (21 exclus), couverture
93,03 %, Ruff et formatage conformes. Le test Gold renforcé sur l'absence de géométrie
inventée passe séparément. Frontend après le correctif du panneau : 545 tests réussis (86 fichiers), lint et
typecheck conformes. Le dernier build local
a échoué faute d’espace disque (`ENOSPC`) ; il reste à valider en CI.
API après intégration du dernier `origin/develop` : 711 tests réussis, 1 ignoré,
151 exclus, couverture 88,09 %. Mypy valide 457 fichiers API et 116 fichiers pipelines. La première tentative utilisait par erreur le pytest global et n'est pas retenue.

Les huit différences de `p706-anchor-differences.json` restent non acceptées. Les conflits
Paraná/Daghestan, le statut historique d'Extremadura, le classement Micronésie et les
origines concurrentes Corée/Pays-Bas/Mélanésie restent des décisions ouvertes ; aucune
correction de curation n'a été publiée. La PR pipelines reste draft. Aucun merge,
auto-merge, déploiement ou release n'est autorisé dans cette reprise.

La revue indépendante approfondie a identifié un défaut du panneau frontend : une région
sans aperçu « Music of… » ne proposait pas ses genres conservés. Le correctif expose
ces genres et ouvre la sélection explicite même sans aperçu ; le cas Pays basque est
couvert. La revue incrémentale confirme la résolution sans autre défaut identifié.
Les étapes footprint, géométrie et couverture du replay display ont également abouti.

## Analyse des écarts après intégration de develop

L'aperçu « music of Micronesia » révèle un défaut : la curation choisit explicitement
la région Q3359409, dont le nom correspond au titre, mais le filtre des homonymes sans
footprint retient seulement l'État Q702 et efface le rang de l'aperçu de la région.
Le choix explicite conserve désormais sa correspondance de nom, sans créer de footprint
ni de relation géographique. Un replay ciblé sur les anciennes entrées rétablit son
rang à 0 ; les sept autres écarts restent ouverts et non acceptés.

- Paraná et Daghestan : les polygones admin-1 débordent très légèrement de leurs pays
  respectifs (rapport des aires en coordonnées sources : 2,9e-10 et 7,0e-8). Les sources
  administratives affirment l'inclusion entière, mais le contrôle spatial actuel détecte
  un conflit. La distance maximale des sommets extérieurs au polygone parent est d'environ
  3,18e-6 degré pour Paraná et 6,01e-5 degré pour Daghestan, contre une grille source de
  1e-6 degré. Ces mesures descriptives ne justifient pas à elles seules une tolérance ou une
  inclusion ; la provenance et la précision des frontières doivent être examinées.
- Extremadura : Q5777 est classé région historique dans les anciennes entrées ; son P131
  vers l'Espagne reste inconnu sans contour historique indépendant. Déterminer si cette
  entité représente aussi la communauté actuelle avant de modifier sa classification.
- Pansori et gagok : Corée et Corée du Sud sont toutes deux des origines déclarées. Les
  anciens polygones disponibles couvrent spatialement la Corée du Sud par la Corée, mais
  aucune relation directe ne figure dans le graphe résolu ; l'égalité est départagée par
  QID. Vérifier les règles d'admissibilité des contours et la création d'une relation
  avant de conclure que cette couverture est une preuve exploitable.
- Electro house : Pays-Bas et Europe sont deux origines déclarées ; le contour national
  comprend des territoires hors d'Europe, donc l'intersection partielle ne prouve pas une
  inclusion entière. La sélection d'une origine unique reste une décision de modèle.
- Pacific reggae : Mélanésie, Polynésie et Nouvelle-Zélande sont des origines déclarées ;
  les anciens éléments ne prouvent pas une inclusion complète de la Mélanésie. Choisir
  entre plusieurs origines indépendantes reste une décision de modèle.

Ces observations utilisent les captures du 6 novembre et le modèle d'entité antérieur à
la fusion : les parquets conservés n'ont pas `federated_state`. Ils ne constituent pas un
replay complet après #358 ; les entrées Bronze nécessaires à une reconstruction complète
ne sont pas toutes dans ces captures. Aucune curation n'a été publiée.


## Résolutions après accord sur les suggestions

Le défaut des deux ancrages coréens était la collecte des candidats : aucune déclaration
ne proposait la paire Q884 → Q18097, bien que deux contours indépendants admissibles
prouvent l'inclusion. La découverte spatiale des pays intersectant les contours régionaux
composés fournit désormais cette paire au résolveur. Une intersection seule ne devient
jamais une inclusion entière ; les exclusions éditoriales restent respectées.

Le replay isolé `p706-approved-replay-20261107` conserve les captures précédentes intactes.
Sur les 2 616 ancrages, la comparaison au résultat antérieur au correctif Micronésie montre
exactement trois changements : rang 0 rétabli pour Micronésie, puis Corée du Sud Q884
rétablie pour pansori Q482981 et gagok Q846219. Aucun autre ancrage ne change. Les décisions
géographiques sont 6 038 full, 57 partial et 4 041 unknown sur 10 136 paires. Les nouveaux
candidats expliquent l'augmentation des décisions partielles et inconnues ; ces décisions
ne deviennent pas des arêtes d'inclusion entière.

Un second replay `p706-electro-curation-20261107`, ajoutant uniquement le delta local
`p706-curation/tradition_placement_overrides.csv`, place electro house Q627906 sur les
Pays-Bas Q55 par la règle override et conserve les 2 616 ancrages. Il ne modifie aucune
preuve géographique et aucune curation distante n'a été publiée.

Restent ouverts : Paraná et Daghestan (cohérence des sources de frontières), Extremadura
(classification administrative actuelle et contour des deux provinces), Pacific reggae
(plusieurs origines face à un contrat d'ancrage unique). Le dossier `p706-curation/`
précise les données et décisions nécessaires. Les anciens huit écarts du JSON historique
ne doivent pas être présentés comme huit défauts encore ouverts.

Ces replays utilisent toujours le catalogue d'entités capturé avant #358. Les fichiers
musicaux manquants dans le replay géographique (notamment les périodes et la curation)
proviennent de la capture musicale du 6 novembre, avec les mêmes limites temporelles
qu'indiquées plus haut. Ce n'est pas une reconstruction Bronze complète du dernier modèle.
La validation topologique et les géométries de couverture précédentes ne constituent pas
un nouveau replay de footprint/geometry/coverage après cette découverte de candidats.

## Appartenances culturelles transitives

Le contrat de curation retire `includes_admin_descendants` : toute relation administrative
actuelle entière, sans conflit et retenue dans le containment crée une appartenance
culturelle directe, sans qualification culturelle préalable du parent. Les parents
culturels explicites sont transitifs. Les exclusions sont appliquées avant propagation
aux descendants et bloquent la paire sur tous les chemins ; une appartenance indépendante
par un autre parent reste possible. Les liens indirects conservent un chemin justificatif
déterministe. La qualification `is_cultural` reste indépendante de ces liens.

Cette étape ne change ni les ancrages musicaux ni la composition des contours. En
particulier, la correction du contour culturel de la Polynésie reste un travail distinct.
Les deux listes API sont initialisées vides ; les CSV locaux ne sont pas publiés.

Validation locale du contrat culturel le 7 novembre : 1 105 tests unitaires passent
(21 désélectionnés), couverture globale 93 %, couverture du nouveau module culturel 98 %.
Ruff, formatage et mypy sur 118 fichiers sources passent. Les 27 tests culturels couvrent
notamment les cycles mixtes, les exceptions transitives, les chemins alternatifs, les
références inconnues et la provenance.

Un replay isolé sur les entrées conservées dans `cultural-membership-20261107`, avec les
deux deltas culturels locaux, écrit ses sorties dans
`.git/agent-evidence/cultural-membership-transitive-20261107/` : 5 503 liens administratifs
directs, un lien de curation et 577 liens transitifs ; aucun auto-lien. Hawaï conserve
les deux parents États-Unis et Polynésie. Ce replay valide le calcul culturel sur ces
captures ; il ne constitue pas une nouvelle ingestion ni une validation topologique.

## Validation finale des composants polynésiens

Le replay isolé `p706-polynesia-final-replay-20261107` reprend les entrées conservées du
replay administratif actuel, sans écraser les captures précédentes. Il remplace uniquement
la composition Q35942 puis recalcule relations, footprint, géométrie, couverture,
appartenances culturelles et ancrages. Les témoins Hawaï, Rapa Nui, îles du Nord et du Sud,
Chatham et Tahiti sont couverts ; les témoins continentaux américain et chilien, Fidji et
Campbell sont exclus. Le contour est explicitement incomplet : il ne prétend pas couvrir
tous les outliers polynésiens. Les classements statistiques Bronze restent inchangés.

Les 2 616 ancrages sont conservés, sans changement par rapport au replay administratif
actuel. Les appartenances comprennent 5 503 liens administratifs directs, deux liens
curatés et 599 liens transitifs. Le replay administratif précédent confirme les liens
Espagne → Extremadura → Badajoz/Cáceres et le retour de Q53677911 vers Extremadura.
Ce sont des replays sur captures enrichies, pas une extraction Bronze complète actuelle.

Validation locale finale du code a51c54a : 1 114 tests passent, 21 désélectionnés,
couverture globale 93 %, Ruff et mypy passent. La revue indépendante initiale couvre
le modèle culturel et les interactions géographiques. Sa reprise sur le dernier ajout
polynésien a échoué sur une limite d'usage ; cet ajout a été revu dans la session principale,
notamment les sélecteurs de composants, l'échec sur coupe de terre et la sémantique d'un
parent incomplet. Aucun défaut concret identifié. La source et les limites du périmètre
sont décrites dans `pipelines/geohistory/DESIGN.md`.

Paraná et Daghestan restent signalés comme conflits de source et exclus du containment.
Pacific reggae conserve ses origines et le contrat préexistant à ancrage unique ; aucune
nouvelle décision éditoriale principale n'est introduite. Ces limites restent explicites,
sans élargir la tolérance ni engager une migration multi-ancrage générale.
