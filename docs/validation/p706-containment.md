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
peak RSS 1,527,861,248 bytes on macOS. These numbers must be refreshed after final edits.
Geometry uses cached normalized references and spatial indexes; coverage does not form a
global pairwise product. Replay makes no HTTP calls after the cached inputs are materialized.

The 37 sensitive regions are listed in `p706-sensitive-regions.json` with changed admin IDs
and geometry sources. Ten lack a reliable drawn boundary: Iberian Peninsula, Caucasus,
Balkans, Celtic nations, Central Europe, Joseon, Middle East, Mediterranean Basin, Pacific
Islands and Lesser Antilles. Point footprints may still locate them; they never become
region boundaries. The four fragile footprints Q1239, Q28901162, Q7569750 and Q779924 have
no independent reference boundary and must remain explicitly missing or incomplete.
The reports expose these gaps; the decision about retaining regions with missing contours
is pending. Each remaining anchor difference still requires an individual explanation.

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

The complete unit suite after the P131 contradiction fix passed 1,031 tests with 93.17%
coverage. Full validation is being refreshed after restoring city placement. Ruff/format
and mypy previously passed (208 Python files and 115 source files respectively). Earlier
MusicBrainz integration tests were skipped because the local sample database was absent;
PR CI must establish integration and combined coverage. Previous grow-api validation logged
661 passed, 1 skipped, 128 deselected and 88.52% coverage; this is prior evidence, not a fresh
validation of a changed grow-api checkout.

The city replay retains all 2,616 anchors and reduces the differences from 24 to 14.
`p706-anchor-differences.json` records every remaining difference, labels, outgoing relation
decisions and the concrete explanation. New York City, London, Galicia and Catalonia are
restored. Remaining broader-country fallbacks caused by administrative geometry conflicts,
and the Korea/Netherlands/Melanesia competing-origin changes, require business acceptance or
additional boundary work. They are not silently accepted by this report.
