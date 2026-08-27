# Schema

Data dictionary and lineage notes for `musicbrainz`. See [README.md#pipeline](README.md#pipeline) for the Bronze/Silver layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Bronze](#bronze)
  - [Silver](#silver)

## Bronze

Eleven MusicBrainz Postgres tables (`musicbrainz` schema), ingested as-is (`SELECT *`, no column filtering) to Parquet: `recording`, `tag`, `recording_tag`, `genre`, `url`, `l_recording_url`, `link`, `link_type`, `artist_credit`, `artist_credit_name`, `artist`.

`release` was considered but dropped: it has no FK to `recording` (MusicBrainz links them via `medium`/`track`, neither ingested here) and no FK to `genre` either, and no consumer needs it. Not added speculatively; revisit only if a concrete need for release-level data comes up (e.g. filtering by `release.status`).

**Recording ↔ artist correspondence**: `recording` has no direct FK to `artist` — MusicBrainz links them via `recording.artist_credit → artist_credit.id → artist_credit_name.artist_credit → artist_credit_name.artist → artist.id`. `artist_credit` is a display-name grouping (an `artist_credit` id can be shared by many recordings that credit the exact same artist(s) the same way); `artist_credit_name` is the join table, one row per artist within that credit, ordered by `position` (0-based) — a solo recording has exactly one `artist_credit_name` row, a collaboration/feature has several (e.g. position 0 = main artist, position 1 = featured artist, joined for display by `join_phrase`, e.g. `" feat. "`). These three tables were previously considered and dropped (no consumer existed for artist-level data at the time); now ingested because the Silver `3_song_example` step (see [Silver](#silver)) needs a display artist name per recording.

Column-level schema (names, types, meaning) is **not duplicated here** — see MusicBrainz's own official schema documentation instead, to avoid this doc drifting out of sync as their schema evolves: https://musicbrainz.org/doc/MusicBrainz_Database/Schema

**Deliberate deviation from the source schema**: `gid` columns (Postgres `UUID`) are ingested as `str`, not left as Python `uuid.UUID` objects. Polars can't map `uuid.UUID` to a native dtype (falls back to `Object`, which can't be written to Parquet), so `db.py`'s `connect()` registers a psycopg loader override for the `uuid` type at the connection level — every other column matches the source schema exactly. Anyone relying on the official MusicBrainz docs for this column should know it's a string here, not the native UUID type.

**Recording ↔ link correspondence**: MusicBrainz stores external links (YouTube, official homepage, streaming services, etc.) as a general-purpose `url` table (`id`, `gid`, `url`) plus a relationship table per entity-type pair — `l_recording_url` (`entity0` = `recording.id`, `entity1` = `url.id`) is the one relevant here. Each `l_recording_url` row's `link` FK points to the `link` table, which in turn FKs to `link_type` (the row that categorizes *what kind* of relationship it is — "free streaming", "streaming", "license", etc., via `link_type.name`); both are now ingested, giving a precise `link_type.name` value per row instead of matching on `url.url` substrings. The Silver `1_recording_link` step (see [Silver](#silver)) uses this join, replacing the earlier substring-based YouTube-only approach. Not every recording has a link of a given type (community-submitted data, same caveat as tags/genres).

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
| link_type     | Defines relationship kinds ("free streaming", "streaming", "license", ...) — `link.link_type` points here | not yet run locally |
| artist_credit | Display-name grouping for one or more credited artists — `recording.artist_credit` points here | not yet run locally |
| artist_credit_name | One row per artist within an `artist_credit`, ordered by `position` (`join_phrase` glues display strings together) | not yet run locally |
| artist        | An individual artist/performer (name, sort_name, ...) — `artist_credit_name.artist` points here | not yet run locally |

## Silver

Three steps built so far.

`1_recording_link` (`pipelines/musicbrainz/src/musicbrainz/silver/recording_link.py`) — a recording ↔ link correspondence, typed by `link_type.name` (e.g. "free streaming", "streaming", "license"), derived from the Bronze `l_recording_url`/`url`/`link`/`link_type` tables per the note in [Bronze](#bronze):

```sql
SELECT DISTINCT
  lru.entity0 AS recording_id,
  u.url       AS url,
  lt.name     AS link_type
FROM l_recording_url lru
JOIN url u        ON u.id = lru.entity1
JOIN link l       ON l.id = lru.link
JOIN link_type lt ON lt.id = l.link_type
```

- Deliberately many-to-many, one row per (recording, link, link type) — a recording can have zero, one, or several links of any given type (e.g. official video, live version, VEVO on YouTube), across any number of platforms (YouTube, Spotify, official homepage, ...); no "primary link" or platform filtering happens here.
- Replaces the earlier `1_recording_youtube_url` step, which matched `url.url` against `youtube.com`/`youtu.be` substrings before `link`/`link_type` were ingested at Bronze — that approach only ever surfaced YouTube and couldn't distinguish other platforms. `link_type.name` gives a precise, MusicBrainz-curated relationship kind instead of a URL-string heuristic, generalizing to every platform in one step.
- `.unique()`'d in code, not deduped by any business key — this only removes exact-duplicate rows, which shouldn't occur given `l_recording_url`'s own primary key, but is cheap insurance.
- Run via `uv run --package musicbrainz python -m musicbrainz.silver`, writing `SILVER_OUTPUT_DIR/1_recording_link.parquet`.

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

`3_song_example` (`pipelines/musicbrainz/src/musicbrainz/silver/song_example.py`) — a small, capped `(title, artist, youtube_video_id, genre_name)` example-song dataset, built for a downstream consumer (`the-music-tree-api`'s genre-tree demo/example-load endpoint) that needs real, MusicBrainz-derived song data to seed a demo, not a full export. Joins `1_recording_link`, `2_recording_genre`, and Bronze `genre`/`recording`/`artist_credit_name`/`artist`:

```sql
WITH youtube_video AS (
  -- url matched against youtube.com/youtu.be, one row kept per recording (see note below)
  SELECT recording_id, <video id extracted from url> AS youtube_video_id
  FROM recording_link
  WHERE url ~ 'youtube\.com|youtu\.be'
),
primary_genre AS (
  -- highest-weight genre kept per recording (see note below)
  SELECT recording_id, genre_id, weight
  FROM recording_genre
  QUALIFY row_number() OVER (PARTITION BY recording_id ORDER BY weight DESC, genre_id) = 1
),
primary_artist AS (
  -- position 0 = MusicBrainz's own primary-artist slot (see note below)
  SELECT acn.artist_credit, a.name AS artist_name
  FROM artist_credit_name acn
  JOIN artist a ON a.id = acn.artist
  WHERE acn.position = 0
)
SELECT r.name AS title, pa.artist_name AS artist, yv.youtube_video_id, g.name AS genre_name, pg.weight
FROM youtube_video yv
JOIN primary_genre pg ON pg.recording_id = yv.recording_id
JOIN genre g ON g.id = pg.genre_id
JOIN recording r ON r.id = yv.recording_id
JOIN primary_artist pa ON pa.artist_credit = r.artist_credit
QUALIFY row_number() OVER (PARTITION BY g.name ORDER BY pg.weight DESC) <= 5
```

- **YouTube-only, one video per recording**: only recordings with at least one link whose `url` matches `youtube.com`/`youtu.be` are candidates — `link_type.name` (from `1_recording_link`) doesn't distinguish platform (the same name, e.g. "streaming", is used across YouTube, Bandcamp, etc.), so the URL string itself is still what identifies YouTube, same as the retired `1_recording_youtube_url` step. A recording with several YouTube links keeps exactly one (the lexicographically first `url`, for determinism) — this step needs a single representative video per song, not every link.
- **Video id extraction** handles `youtu.be/<id>`, `youtube.com/watch?v=<id>`, `youtube.com/embed/<id>`, and `youtube.com/v/<id>`. A bare playlist/channel URL (e.g. `youtube.com/playlist?list=...`) carries no video id in any of those positions and is dropped, not defaulted to a null/garbage id — there's no video to point at.
- **Primary genre = highest `weight` per recording** (ties broken by lowest `genre_id`, for determinism) — `2_recording_genre` is deliberately many-to-many and leaves "pick one genre" as a consumer decision (see [Silver](#silver) above); this step is that consumer, since the output needs exactly one genre per song.
- **No genre-tree name filtering here**: this step emits the recording's matched `genre.name` as-is, unfiltered and untranslated against the downstream API's specific fixed genre-tree node names — matching that fixed list is the consuming repo's job (keeps this repo's Silver layer genre-tree-agnostic, consistent with its existing "no cross-pipeline joins yet" boundary).
- **Display artist = `artist_credit_name` position 0** — an `artist_credit` can have several `artist_credit_name` rows (collaborations, features); rather than concatenating every credited artist with its `join_phrase`, only the position-0 (primary) artist's name is used as the display string. Simpler, and sufficient for an example dataset; a full display-credit string (e.g. "Artist X feat. Artist Y") is left as a future enhancement if a consumer needs it.
- **Capped to 5 recordings per genre** (`RECORDINGS_PER_GENRE` in `song_example.py`), by `weight` descending — this is meant to seed a small demo dataset, not dump every matching recording from a multi-million-row sample DB.
- Run via `uv run --package musicbrainz python -m musicbrainz.silver`, writing `SILVER_OUTPUT_DIR/3_song_example.parquet`.
- **On-demand JSON export**: `scripts/export_song_example_json.py` reads `3_song_example.parquet` and writes a flat JSON array (`[{"title": ..., "artist": ..., "youtube_video_id": ..., "genre_name": ...}, ...]`) for a developer to manually copy/commit into the downstream API repo. Run with `uv run --package musicbrainz python scripts/export_song_example_json.py <output.json>` whenever the example song set needs refreshing — not a scheduled job, no `infrastructure` involvement.

`recording_genre_path` is not built yet (see [README.md#pipeline](README.md#pipeline)).
