# Schema

Data dictionary and lineage notes for `musicbrainz`. See [README.md#pipeline](README.md#pipeline) for the Bronze/Silver layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Bronze](#bronze)
  - [Silver](#silver)

## Bronze

Eight MusicBrainz Postgres tables (`musicbrainz` schema), ingested as-is (`SELECT *`, no column filtering) to Parquet: `recording`, `tag`, `recording_tag`, `genre`, `url`, `l_recording_url`, `link`, `link_type`.

`release` and `artist` were both considered but dropped, for the same reason: neither is reachable from `recording` in this table set, and neither has a consumer in the Silver plan (recording → genre resolution only).

- `release` has no FK to `recording` (MusicBrainz links them via `medium`/`track`, neither ingested here) and no FK to `genre` either.
- `artist` has no FK to `recording` either — MusicBrainz links them via `recording.artist_credit → artist_credit → artist_credit_name → artist`, and none of `artist_credit`/`artist_credit_name` are ingested here.

Neither was added speculatively; revisit only if a concrete need for release- or artist-level data comes up (e.g. filtering by `release.status`, or an artist-level genre rollup in the final output).

Column-level schema (names, types, meaning) is **not duplicated here** — see MusicBrainz's own official schema documentation instead, to avoid this doc drifting out of sync as their schema evolves: https://musicbrainz.org/doc/MusicBrainz_Database/Schema

**Deliberate deviation from the source schema**: `gid` columns (Postgres `UUID`) are ingested as `str`, not left as Python `uuid.UUID` objects. Polars can't map `uuid.UUID` to a native dtype (falls back to `Object`, which can't be written to Parquet), so `db.py`'s `connect()` registers a psycopg loader override for the `uuid` type at the connection level — every other column matches the source schema exactly. Anyone relying on the official MusicBrainz docs for this column should know it's a string here, not the native UUID type.

**Recording ↔ YouTube link correspondence**: MusicBrainz stores external links (YouTube, official homepage, streaming services, etc.) as a general-purpose `url` table (`id`, `gid`, `url`) plus a relationship table per entity-type pair — `l_recording_url` (`entity0` = `recording.id`, `entity1` = `url.id`) is the one relevant here. Each `l_recording_url` row's `link` FK points to the `link` table, which in turn FKs to `link_type` (the row that categorizes *what kind* of relationship it is — "YouTube", "official homepage", etc., via `link_type.name`); both are now ingested, giving a precise `link_type.name = 'YouTube'`-style filter as an alternative to matching on `url.url` substrings. The current Silver `1_recording_youtube_url` step still uses the substring approach (see [Silver](#silver)) — switching it to a `link_type` join is a documented follow-up, not yet done. Not every recording has a YouTube link (community-submitted data, same caveat as tags/genres).

**No direct recording ↔ genre link in the source data**: `genre` (`id`, `gid`, `name`, `comment`, ...) is a flat reference list with no foreign key to `recording` at all — confirmed via `DESCRIBE SELECT * FROM genre` against the bronze output. The only recording-level link available is `recording_tag` (many-to-many: `recording`, `tag`, `count`) — free-text folksonomy tags, not curated genres. A recording routinely has dozens of tags (e.g. one sample-dataset recording has 50 tags matching known genre names simultaneously: rock, electronic, post-rock, pop, jazz, metal, ...). Associating a recording with a genre means matching a tag's name against `genre.name` — not built yet, this is exactly what the Silver `recording_genre` table (see [Silver](#silver)) is for. Once it exists, a recording can and typically will map to **multiple** genres, not one.

**`genre` is not a view or subset of `tag` — there's no FK or constraint linking them at all.** `tag` is the raw folksonomy vocabulary: any free-text string any user has ever applied to anything ("rock", "0stars", "my favorite"). `genre` is a separate, editorially curated table — MusicBrainz staff/style guidelines decide what's in it, and it carries its own metadata (`comment`) that `tag` doesn't have. Nothing enforces that every `genre.name` has a matching `tag` row, or vice versa — a genre can exist with zero recordings currently tagged that way, and a tag can be a popular genre-sounding string ("post-rock") without the curators having added it to `genre` yet. MusicBrainz's own site (confirmed via `musicbrainz.org/doc/Genre`) resolves this the same way we do: at render time, it checks whether a tag's name matches the current `genre` list and displays it as a genre badge instead of a plain tag — it's a runtime lookup, not a schema-level relationship. This is also why `recording_genre` (below) has to be re-derived any time `genre` or `recording_tag` changes, rather than being a one-time migration.

**Volumetrics** (MusicBrainz **sample** dataset used in dev/CI, not the full corpus — see [README.md#data-source](README.md#data-source)):

| Table         | Description                                                | Rows (last local run) |
| ------------- | ------------------------------------------------------------ | --------------------: |
| recording     | An individual track/recording (title, length, artist credit) |              2,901,075 |
| recording_tag | User-applied free-text tags linked to a recording             |              1,479,676 |
| tag           | The tag vocabulary itself (tag name, id) — `recording_tag` links recordings to these |       22,082 |
| genre         | MusicBrainz's flat genre list (id, name) — the source this whole pipeline reconstructs a hierarchy from |  2,164 |
| url           | General-purpose external-link table (id, gid, url) — YouTube links live here alongside every other link type | not yet run locally |
| l_recording_url | Many-to-many `recording` ↔ `url` relationship (`entity0`/`entity1`) — the join needed for a recording → YouTube-link correspondence | not yet run locally |
| link          | Relationship instance (begin/end dates, `link_type` FK) — `l_recording_url.link` points here | not yet run locally |
| link_type     | Defines relationship kinds ("YouTube", "official homepage", ...) — `link.link_type` points here | not yet run locally |

## Silver

Two steps built so far.

`1_recording_youtube_url` (`pipelines/musicbrainz/src/musicbrainz/silver/recording_youtube_url.py`) — a recording ↔ YouTube-URL correspondence, derived from the Bronze `url`/`l_recording_url` tables per the note in [Bronze](#bronze):

```sql
SELECT DISTINCT
  lru.entity0 AS recording_id,
  u.url       AS youtube_url
FROM l_recording_url lru
JOIN url u ON u.id = lru.entity1
WHERE u.url LIKE '%youtube.com%' OR u.url LIKE '%youtu.be%'
```

- Deliberately many-to-many, one row per (recording, YouTube URL) — a recording can have zero, one, or several YouTube links (official video, live version, VEVO, etc.); no "primary link" selection happens here.
- `.unique()`'d in code, not deduped by any business key — this only removes exact-duplicate rows, which shouldn't occur given `l_recording_url`'s own primary key, but is cheap insurance.
- Run via `uv run --package musicbrainz python -m musicbrainz.silver`, writing `SILVER_OUTPUT_DIR/1_recording_youtube_url.parquet`.

`2_recording_genre` (`pipelines/musicbrainz/src/musicbrainz/silver/recording_genre.py`) — a recording ↔ genre correspondence, matching how MusicBrainz's own UI resolves a genre badge (see the note in [Bronze](#bronze)):

```sql
SELECT
  rt.recording AS recording_id,
  g.id         AS genre_id,
  rt.count     AS weight
FROM recording_tag rt
JOIN tag   t ON t.id = rt.tag
JOIN genre g ON lower(t.name) = lower(g.name)
WHERE rt.count > 0
```

- `weight = recording_tag.count`, the tag's **net vote score** (upvotes minus downvotes), not a raw application count — confirmed via `musicbrainz.org/doc/MusicBrainz_Database/Schema`. `count > 0` is the same "community endorses this" threshold MusicBrainz's own site uses before showing a genre badge; `<= 0` means net-downvoted or nobody's voted, and is dropped.
- Matching is by `tag.name`, case-insensitively — `genre` and `tag` share no key, see the note in [Bronze](#bronze). `lower()` is a placeholder for exact-string equality; not yet verified against real data whether MusicBrainz tag names ever deviate from lowercase (check with `SELECT name FROM tag WHERE name != lower(name) LIMIT 5` once bronze is regenerated) — if none do, `lower()` on both sides is redundant but harmless.
- Deliberately many-to-many, one row per (recording, matched genre) — no dedup/top-N logic. A recording with 50 tags matching 6 different genre names produces 6 `recording_genre` rows; picking a single "primary" genre, if ever needed, is a decision for a consumer, not this table.
- Run via `uv run --package musicbrainz python -m musicbrainz.silver`, writing `SILVER_OUTPUT_DIR/2_recording_genre.parquet`.

`recording_genre_path` and non-YouTube link platforms (see the `link_type` note in [Bronze](#bronze)) are not built yet (see [README.md#pipeline](README.md#pipeline)).
