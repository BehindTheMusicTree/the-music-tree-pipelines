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

Ethnic groups (~80k Wikidata items) are not ingested in bulk: they are too broad and mostly undated. They enter on
demand through curation.

## 5. Multiple Glottocodes

An item can carry several Glottocodes (e.g. Limburgish `limb1263`/`limb1265`), so `wikidata_cultural_entity` is
keyed on `item_id|kind|glottocode`, not `item_id|kind`.

## 6. Silver entities

One entity per Glottocode (`entity_id` = glottocode) and one per QID for archaeological cultures and historical
countries. A Wikidata item carrying a Glottocode attaches to it through the crosswalk instead of becoming an entity
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

