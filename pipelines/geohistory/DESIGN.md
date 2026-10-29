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

Admin units are the Natural Earth admin-1 polygons (reusing the entity of their Wikidata item when it already is
one) plus P131 intermediates holding more than one known child. Containment edges come from P131, P361, P30 and
P706 between place entities, with the Natural Earth country as fallback for admin-1 units; when two sources
claim opposite directions (Guayana Esequiba, the Koreas) the higher-priority source wins and a tie drops both with
a warning. Longer cycles raise. A membership override remove also removes the matching containment edge.
The footprint of an entity is itself if it has a Natural Earth polygon or is an admin unit fully tiled by such polygons (`self`), else every admin unit it contains (`cover`), else
its successors' footprint (`succession`), else the admin unit containing its coordinates (`point`, Glottolog or
Wikidata P625, first match in `ne_id` order). Anchoring drops a candidate that contains another candidate, and a
tradition without a start year inherits the earliest start of its nearest containing places. Admin-unit anchors are
lifted to the nearest non-admin container before lanes are built, so admin units never become lanes.

`8_entity_geometry` unions the polygons of an entity's footprint, dropping admin units already contained in another
one of the same footprint so a country and its province are not counted twice. An admin unit without a Natural
Earth polygon is tiled by its contained units' polygons. The union is `shapely.union_all` rather than
`coverage_union`, which rejects Natural Earth's not exactly noded neighbours; source polygons are repaired with
`make_valid` first (e.g. BRA-1294 self-intersects). An entity whose footprint has no polygon raises. Measured on the
staging Silver: about 43 s and 3 GB peak memory for 36k entities, so no optimisation.

## 9. Curation

Eight lists from grow-api (`curation` pipeline, read via `CURATION_BRONZE_DIR`), applied in Silver. Entity ids are
Glottocodes or QIDs.

- `entity_additions` lists QIDs that `wikidata` Bronze ingests, with their claims, as kind `curated` although no bulk class
  covers them. A curated item already covered by a bulk kind keeps that kind and is logged; a QID absent from Bronze
  (deleted or redirected) raises. Only QIDs: a Glottocode already comes from Glottolog.
- `entity_exclusions` removes the entity from `1_cultural_entity` and the crosswalk, so its edges, successions and
  dates disappear downstream. An id out of scope is logged.
- `entity_membership_overrides` runs after the source edges and before the cycle check, so it is the way to break a
  cycle. `remove` drops an edge (a missing edge is logged); `add` inserts one with source `curation` and raises
  if an end is not an entity. Any other `action` raises.
- `entity_time_span_overrides` wins over every sourced date (`start_source`/`end_source` = `curation`, precision
  null). An empty year keeps the sourced value.

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
"Greece" lands on ancient Greece), `P17`/`P276`/`P921`/`P361` when the value is a place entity, and for an overview the
label ("music of X" against entity labels and `wikidata_cultural_entity_alias`). A candidate that is a membership
ancestor of another candidate is dropped, so the most specific place wins (Morbihan over Bretagne over France) whatever
the property; among the remaining ones the first rule in the order above wins. A label matching several entities is
ambiguous: it adds no candidate and the overview is listed in `5_music_anchor_unplaced.csv`.
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

