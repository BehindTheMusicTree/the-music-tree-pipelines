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
inventée passe séparément. Frontend avant le dernier correctif du panneau : 544 tests réussis ; le test ciblé du
panneau corrigé passe (11 tests), lint et typecheck conformes. Le dernier build local
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
