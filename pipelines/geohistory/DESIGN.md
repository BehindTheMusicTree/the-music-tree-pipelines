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

## 9. Curation

Four lists from grow-api (`curation` pipeline, read via `CURATION_BRONZE_DIR`), applied in Silver. Entity ids are
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

Header drift in a list raises. Succession overrides are not curated yet.
