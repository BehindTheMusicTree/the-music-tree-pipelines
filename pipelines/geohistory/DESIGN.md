# Design

Rationale for `geohistory`, the cultural-entity backbone of the music-traditions genealogy (time on Y, cultural
areas on X, dated cultural-entity lanes as the background). Column definitions are in [SCHEMA.md](SCHEMA.md).

## 1. Ontology

Cultural entities form an overlapping, dated DAG: leaves are the lanes drawn on the chart, enclosing entities are
the bands behind them. An entity can belong to several enclosing entities and can succeed or be replaced by
others over time (`P1365`/`P1366`).

## 2. Sources

- **Glottolog** gives the language-family backbone (parent chain, macroarea, coordinates). Its parent is not in
  `languages.csv`: it comes from the `classification` parameter of `values.csv`, kept raw in Bronze and split by
  Silver.
- **Wikidata** gives dates and succession, and covers entities Glottolog does not (archaeological cultures,
  historical countries). It is joined to Glottolog through `P1394`. Its Bronze lives in `wikidata`.
- Genres link to modern states (`P495`), not to cultures, so tradition → culture links are curated, not derived.

## 3. Dates

Priority: curated > Wikidata > dates carried by the traditions placed on an entity. A living entity extends to
1950. Wikidata dates stay raw literals in Bronze because BCE years do not parse as timestamps.

## 4. Scope

Modern countries (`Q6256`, ~670 items) and cultural regions (`Q3502482`, ~330) are ingested in bulk: they are
small, datable, and the countries are what genres link to through `P495`. Civilizations (`Q8432`, ~110) too.
Ethnic groups (~80k) and historical regions (~113k) are too broad and mostly undated, so only those with at least 30
sitelinks are kept (~990 and ~4,200): Wikipedia coverage is a measurable proxy for notability. A macro region
(Caribbean, Southeast Asia...) has no class: it is any item that at least two countries or historical countries
are part of, located in, or on the continent of, so the envelopes follow the lanes already ingested. Anything
else enters on demand through the `entity_additions` curation list (§9). Archaeological sites (`Q839954`, ~75k) are not ingested wholesale: they are places, mostly undated and without a tradition of their own, and no sitelink threshold separates the useful ones.

## 5. Multiple Glottocodes

An item can carry several Glottocodes (e.g. Limburgish `limb1263`/`limb1265`), so `wikidata_cultural_entity` is
keyed on `item_id|kind|glottocode`, not `item_id|kind`.

## 6. Silver entities

One entity per Glottocode (`entity_id` = glottocode) and one per QID for every Wikidata kind. An item matching
several kinds keeps the most specific: archaeological culture, civilization, historical country, country,
historical region, ethnic group, cultural region, macro region. A Wikidata item carrying a Glottocode attaches to it through the crosswalk instead of becoming an entity
of its own. Glottocodes absent from Glottolog are left out with a warning. Categories `Bookkeeping`,
`Sign_Language` and `Artificial_Language` are excluded with their descendants: pseudo-families or languages with
no musical tradition. Dialects are kept; the minimal cut chooses the lanes later. Glottolog coordinates win over
Wikidata P625.

A `city` entity is a Wikidata human settlement with a coordinate that a genre's place claim targets
(`wikidata_city_candidate.parquet`; a curated `entity_additions` row may also give an item kind `city`). It ranks below every other kind, so
an item that is already a country, region or other entity keeps that kind. Only the settlements that genres claim exist:
there is no bulk city import, so the map never carries a city nobody anchors a genre on.

## 7. Membership and succession

The parent of a languoid is the last element of its `classification`. Wikidata `P361` adds edges, except between
two languoids, where Glottolog is authoritative. A cycle raises. Succession merges `P1365` (replaces) and `P1366`
(replaced by) into one predecessor → successor edge.

## 8. Time span

Start: min `P571`, else min `P580`, else the dates of the entity's period (`P2348`), else Glottolog's first year of
documentation. End mirrors it with max `P576`/`P582` and the last year of documentation. Periods are ingested as
`time_period` Bronze items and never become entities. Years follow Wikidata: BCE is negative, there is no year 0.
A null end means alive; the 1950 ceiling is applied in Gold. An entity starting after it ends is logged and kept
for curation.

## 8.1 Admin units, containment and footprints

Whether an admin unit is a federated state (`federated_state`, what gold always draws on the region map, genre or not)
is read from the unit itself: its Wikidata item is `P31/P279*` of federated state (`Q107390`). The country-level
`P122` (basic form of government) was rejected as the signal because it is missing for the United States and Russia.
The bulk `federated_state` rows are no entity kind (they would compete with the admin unit of the same QID); they only
set the flag, and the `federated_state_overrides` curation list (`add`/`remove`) corrects the few misses and false
positives, such as a French or Spanish unit typed as a federated state.

Admin units are the Natural Earth admin-1 polygons (reusing the entity of their Wikidata item when it already is
one; the sole unit of an unsubdivided territory, which Natural Earth leaves without one, takes its country's
item, so the British Virgin Islands are `Q25305` rather than `ne:VGB+00?`), the Natural Earth countries whose item is
no Wikidata entity (dependencies such as Jersey `Q785`, Hong Kong `Q8646` or the US Virgin Islands `Q11703`, labelled
with their item's label, then their NE name, and holding their admin-1 units) plus P131 intermediates holding more than
one known child. Containment edges come from P131, P361, P30 and
P706 between place entities, with the Natural Earth country as fallback for admin-1 units and, for a city without any such parent, the admin unit holding its coordinate (`point`, lowest priority); when two sources
claim opposite directions (Guayana Esequiba, the Koreas) the higher-priority source wins and a tie drops the
greater `(parent, child)` edge. Any longer cycle, including one that crosses `2_entity_membership`, drops its
lowest-priority containment edge (membership edges are pinned) with a warning, so the union of membership and
containment is acyclic and consumers (anchoring, lanes) can walk it without cycle handling. A membership override remove also removes the matching containment edge.
The footprint of an entity is itself if it has a Natural Earth polygon or is an admin unit fully tiled by such polygons (`self`), else every admin unit it contains (`cover`), else
its successors' footprint (`succession`), else the admin unit containing its coordinates (`point`, Glottolog or
Wikidata P625, first admin polygon in `ne_id` order that intersects the point, so a point on a border still matches).
A city gets a `point` footprint like any entity with coordinates. `8_entity_geometry` is only materialized for entities Gold can draw (countries, admin units and the four zone kinds). Anchoring drops a candidate that contains another candidate, and a
tradition without a start year inherits the earliest start of its nearest containing places. Admin-unit anchors are
lifted to the nearest non-admin container before lanes are built, so admin units never become lanes.

`8_entity_geometry` unions the polygons of an entity's footprint, dropping admin units already contained in another
one of the same footprint so a country and its province are not counted twice. An admin unit without a Natural
Earth polygon is tiled by its contained units' polygons. The union is `shapely.union_all` rather than
`coverage_union`, which rejects Natural Earth's not exactly noded neighbours; source polygons are repaired with
`make_valid` first (e.g. BRA-1294 self-intersects). An entity whose footprint has no polygon raises. Measured on the
staging Silver: about 43 s and 3 GB peak memory for 36k entities, so no optimisation.

## 9. Curation

Nine lists from grow-api (`curation` pipeline, read via `CURATION_BRONZE_DIR`), applied in Silver. Entity ids are
Glottocodes or QIDs.

- `entity_additions` lists QIDs that `wikidata` Bronze ingests, with their claims, as kind `curated` although no bulk class
  covers them. A curated item already covered by a bulk kind keeps that kind and is logged; a QID absent from Bronze
  (deleted or redirected) raises. Only QIDs: a Glottocode already comes from Glottolog. An optional `entity_kind` (`cultural_region`, `macro_region` or `city`, anything else raises) gives the item that kind instead of `curated`, so it is drawn (from its membership children, see `entity_footprint`) and becomes a map zone; blank keeps `curated`.
- `entity_exclusions` removes the entity from `1_cultural_entity` and the crosswalk, so its edges, successions and
  dates disappear downstream. An id out of scope is logged.
- `entity_membership_overrides` runs after the source edges and before the cycle check, so it is the way to break a
  cycle. `remove` drops an edge (a missing edge is logged); `add` inserts one with source `curation` and raises
  if an end is not an entity. Any other `action` raises.
- `entity_time_span_overrides` wins over every sourced date (`start_source`/`end_source` = `curation`, precision
  null). An empty year keeps the sourced value.
- `region_overrides` forces the region (`americas`, `europe`, `africa`, `asia`, `oceania` or `religions`) of an entity,
  for the transcontinental cases a continent vote cannot settle (Ottoman, Abbasid, Byzantine). An unknown region or a
  duplicated entity raises; an entity out of scope is logged.

- `tradition_additions` / `tradition_exclusions` add or drop a tradition by QID; a QID in both raises.
- `tradition_placement_overrides` sets the anchor (entity id or QID) and/or the start and end years of a tradition;
  an unknown entity or a row with nothing set raises.
- `tradition_link_overrides` adds or removes a `lineage`/`association` link; self links, unknown kinds or actions, and
  an `add` toward a non-tradition raise.

Header drift in a list raises. Succession overrides are not curated yet.

## 10. Tradition detection and anchoring

A tradition is a genre (`P31` music genre) or a "music of X" overview whose start is at most 1950, plus the curated
additions minus the exclusions. An overview also needs an anchor. Genres with neither date nor anchor are dropped
silently (most of Wikidata's genres), whereas a dated tradition without anchor is kept, logged and written to
`5_tradition_unplaced.csv` so it never blocks the daily run. Anchoring itself is `5_music_anchor` (genres, overviews and curated additions), which `5_tradition`
only filters by date.

The anchor is the placement override when set; otherwise it is picked among every candidate entity: `P2596` culture, `P1269` facet of, `P495`
country of origin lifted along `3_entity_succession` to the entity valid at the tradition's date (an old genre of
"Greece" lands on ancient Greece), `P740` location of formation and `P17`/`P276`/`P921`/`P361` when the value is a place entity (a city included, which is more specific than its admin unit and country, so Detroit wins over Michigan and the United States), and for an overview the
label ("music of X" against entity labels and `wikidata_cultural_entity_alias`). A candidate that is a membership
ancestor of another candidate is dropped, so the most specific place wins (Morbihan over Bretagne over France) whatever
the property; among the remaining ones the first rule in the order above wins. A label match with no
footprint is dropped while a placeable match remains: with no geometry it cannot be drawn, so it cannot make the label
ambiguous (the footprint-less Duchy of Brittany no longer blocks "music of Brittany"). An admin and zones whose footprint
is exactly that admin are the same place and collapse to the admin. A label still matching several entities is
ambiguous: it adds no candidate (a city is never a label match: "music of Paris" does not anchor on a city) and the overview is listed in `5_music_anchor_unplaced.csv`.
An overview whose label resolves to its anchor "names" it and is ranked (`overview_rank`); several overviews can be
anchored on one place because an overview that did not resolve its own place falls back to the country through
`P17`/`P495`, so only the ones naming the anchor are candidates for the region's overview. When every place the
label matches has no footprint ("music of China" names only the civilization), an anchor picked by an override
or a culture claim (`P2596`/`P1269`: the People's Republic) is named too, ranked after those named by label.
Start is the override, else `P571`/`P580`, else the anchor's start; end is `P576`/`P582`, else 1950.

Links: lineage from `P155`/`P156` and the genre tree's `P279`, association from `P737`, both between two traditions.
A lineage cycle raises; break it with a `tradition_link_overrides` remove.

## 11. Lanes and bands

Entities anchoring a tradition form E. A lane is an element of E without descendant in E. A band is an ancestor
(`2_entity_membership`) covering at least two lanes, or an element of E having descendants in E; a non-E ancestor
with the same lane set as one of its children is dropped as redundant. Spans come from `4_entity_time_span`, else the
envelope of the entity's traditions, and are cut at 1950. Lanes linked by succession whose spans do not overlap share
one X column.

## 12. Seriation

Columns are ordered west to east by centroid longitude (a column without coordinates uses its bands'), then
refined by adjacent swaps while they reduce the sum over bands of (extent - column count), capped at 100 passes. The
result is deterministic. A band whose columns end up non-contiguous is split into fragments, no column duplicated.


## Column regions

A column's region is decided in this order, the first rule that applies to any of its lane entities winning:

1. `region_overrides`, then entities of kind `religion` (bulk `P31` in religion, "religion or world view", "Christian denominational family" or "major religious group"), which go to `religions`. `religion` takes precedence over `ethnic_group` when an item has both kinds (Wikidata models denominations as subclasses of ethnic group), while still ranking below country, civilization, historical region and archaeological culture.
2. The continent vote: each admin unit an entity covers votes for its country's continent, weighted by the unit's
   `area_km2` (Bronze, spherical equal-area), so a few small European units do not outvote Siberia. Ties go to the lowest
   `region_rank`.
3. For entities with no vote, the Wikidata `P625` point: the containing Natural Earth country's continent, else the
   nearest country within 2 degrees (straits, island groups).

A column no rule places raises and names the column: add a `region_overrides` row rather than shipping an `other` band.

Known limitation: the nearest-country fallback searches within a fixed tolerance in degrees and does not wrap across the
antimeridian, so a point just across ±180° (Fiji, Chukotka) finds no country on the other side. The area vote covers
such entities whenever they have a footprint; add a `region_overrides` row for the rest.

### Independent island identity

The physical-region layer (`ne_10m_geography_regions_polys`) supplies source-linked QIDs and explicitly distinguishes
simple islands from island groups. Simple islands can identify a land component only when the Wikidata coordinate
and the physical-label polygon's interior representative point lie inside the same unique component. Coastline
geometry comes from the union of Natural Earth administrative land, not the label polygon. Island groups are not
reduced to a single component. Missing identity, missing coordinates or inconsistent identity leave the boundary
unresolved. The pinned v5.1.2 source splits Flores into two features sharing one identity; ingestion unions shards
only when their source identity metadata agrees, and rejects conflicting metadata.

Explicit full member decisions can construct composed reference boundaries. These unions remain incomplete unless
exhaustiveness is independently declared; absence from an incomplete union supplies no negative evidence. Every
union retains its relation dependencies, preventing it from proving the relation used to build it. Members with
incompatible temporal reference periods cannot form a shared reference boundary.

Geographic candidates are resolved before source priority and cycle handling. Only
full decisions enter containment. Place-to-place cultural membership is excluded from
anchor specificity and genealogy ancestry; those consumers use the resolved graph.
Point footprints remain available to locate anchors but never define a drawn region's
boundary. Independent reference boundaries take precedence over administrative tiling.
Coverage is clipped for map discovery and never feeds back into containment.

A city's coordinate can locate that city inside a mapped admin-1 unit when no resolved
claim places it. This `city_point` fallback is restricted to cities and does not construct
a boundary for any country or region.

Independent composed boundaries use pinned Natural Earth `admin_0_map_units`, rather
than sovereign country polygons: the latter merge overseas territories with their
metropole. The Bronze composition artifact retains each map unit's NE_ID and name,
source version, definition, geometry and completeness. Caribbean means Natural Earth's
`SUBREGION=Caribbean`, including its island territories and excluding mainland coastal
regions. Kingdom of the Netherlands uses `SOV_A3=NL1`; its European and Caribbean units
remain distinct in the source and are united only for the Kingdom's own boundary.

The listed continental and statistical subregional conventions use Natural Earth's
`CONTINENT`/`SUBREGION` assignments. These are explicit administrative groupings, not
physical watershed or tectonic boundaries; for example, Russia follows the source's
Europe classification. Korea, Scandinavia, British Isles and Nordic countries use the
explicit map-unit selections in `composed_boundaries.py`. The Nordic selection includes
Greenland, the Faroes, Åland and Norway's northern dependencies, following the territory
scope described by [Nordic cooperation](https://www.norden.org/en/information/official-nordic-co-operation).
Missing any configured composition fails ingestion. Source definitions are never inferred
from existing containment or cultural membership.

A current city's, country's or admin unit's best-rank P131 path can supply administrative
evidence even when a polygon is absent. Every original statement must be unscoped and
current: an explicit start year must precede the run's year and an explicit end year must
follow it, at year precision or better. Same-year transitions, point-in-time qualifiers,
unsupported dates and known historical entities anywhere in the path are excluded.
Uncatalogued intermediates are accepted only through such P131 statements; all original
statement IDs and qualifiers remain in the evidence. The report records the evaluation year.
Other dated geographic claims remain unknown against today's reference boundaries. Conflicting
geometric evidence remains a conflict requiring review rather than being erased by
source priority.

Independent borders are compared on the source coordinate grid. When polygons overlap,
an excursion entirely within one grid unit of the parent is treated as rounding uncertainty;
no overlap-area percentage is used. Anything farther outside remains partial. The parent is
clipped around the child's bounds before buffering, so this tolerance does not offset an
entire country's detailed coast for each subdivision. Disjoint polygons and boundary-only
contact cannot establish full containment through this tolerance.

### Contours manuels sourcés

Le catalogue versionné `silver/manual_boundaries.geojson` contient des `Feature` GeoJSON
WGS84 de type Polygon ou MultiPolygon. Chaque entrée exige `wikidata_id`, `source_url`
(HTTPS), `license`, `version`, `definition`, `components` (identifiants des composants de la
source, liste vide pour un contour direct), `complete`, `usage`, `start_year` et `end_year`.
Les années nulles indiquent une frontière sans période historique déclarée. Le chargeur
rejette les doublons, les polygones invalides, les périodes inversées et les métadonnées
manquantes. Une entrée ne remplace jamais implicitement une référence existante.

`usage: display` autorise la matérialisation cartographique mais conserve
`boundary_kind: display` : le résolveur et les compositions de preuves l'excluent, même si
`complete` vaut true. `usage: containment` exige un contour complet ; les contrôles de
période et de circularité s'appliquent ensuite comme aux autres références indépendantes.
Les géométries dessinées peuvent servir au découpage cartographique, dont les intersections
ne remontent jamais dans le graphe d'inclusion. La provenance des entrées effectivement
rattachées est conservée dans `2_manual_boundary_provenance.json`.

Le catalogue est initialement vide : les pistes documentaires n'établissent pas encore des
contours réutilisables ni des conventions culturelles acceptées. Les régions concernées
restent dans les rapports de frontières manquantes. Ce contrat ne résout pas à lui seul leur
absence dans l'index Gold ; ce dernier et ses consommateurs doivent encore représenter une
région sans géométrie, avec ses genres attachés à leur région d'origine.

### Compositions d'affichage celtiques et balkaniques

Bronze charge les unités cartographiques et subdivisions Natural Earth à la même version.
La convention celtique compose les six nations, avec toute l'Irlande et les cinq départements
bretons historiques ; la convention balkanique compose onze pays entiers, sans la Turquie
européenne, et reste incomplète. Les composants absents font échouer l'ingestion.
`usage=display` demeure une frontière d'affichage, y compris pour une entité classée
historique : elle ne devient jamais une référence indépendante de preuve.
Les sources et sélecteurs sont détaillés dans `docs/validation/p706-boundary-sources.md`.
