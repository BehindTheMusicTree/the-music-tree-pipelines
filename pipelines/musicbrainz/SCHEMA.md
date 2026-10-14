# Schema

Data dictionary and lineage notes for `musicbrainz`. See [README.md#pipeline](README.md#pipeline) for the Bronze/Silver layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [1. Bronze](#1-bronze)
  - [2. Silver](#2-silver)

## 1. Bronze

Twenty-four MusicBrainz Postgres tables (`musicbrainz` schema), ingested as-is (`SELECT *`, no column filtering) to Parquet: `recording`, `tag`, `recording_tag`, `genre`, `url`, `l_recording_url`, `l_recording_work`, `link`, `link_type`, `artist_credit`, `artist_credit_name`, `artist`, `artist_type`, `area`, `iso_3166_1`, `l_artist_artist`, `link_attribute`, `link_attribute_type`, `l_artist_url`, `release_group`, `release_group_primary_type`, `release_group_secondary_type`, `release_group_secondary_type_join`, `release_group_meta`.

`release_group` (and its type/meta tables) is ingested for artist discographies: it carries its own `artist_credit` FK and a first-release year in `release_group_meta`, so no release-level join is needed. `release`, `medium` and `track` are still not ingested: `release` has no FK to `recording` (MusicBrainz links them via `medium`/`track`) and no FK to `genre`, and no consumer needs release-level data (e.g. `release.status`).

**Artist tables**: `artist_type` names `artist.type`; `iso_3166_1` maps an `area` to its ISO country code (`area` itself is ingested for lineage, the code lookup needs only `iso_3166_1.area`). `l_artist_artist` + `link`/`link_type` ("member of band") + `link_attribute`/`link_attribute_type` give band memberships and the instruments/vocals played. `l_artist_url` + `link`/`link_type` + `url` give an artist's external links.

**Recording ↔ artist correspondence**: `recording` has no direct FK to `artist` — MusicBrainz links them via `recording.artist_credit → artist_credit.id → artist_credit_name.artist_credit → artist_credit_name.artist → artist.id`. `artist_credit` is a display-name grouping (an `artist_credit` id can be shared by many recordings that credit the exact same artist(s) the same way); `artist_credit_name` is the join table, one row per artist within that credit, ordered by `position` (0-based) — a solo recording has exactly one `artist_credit_name` row, a collaboration/feature has several (e.g. position 0 = main artist, position 1 = featured artist, joined for display by `join_phrase`, e.g. `" feat. "`). These three tables were previously considered and dropped (no consumer existed for artist-level data at the time); now ingested because the Silver `5_songs` step (see [2. Silver](#2-silver)) needs a display artist name per recording.

Column-level schema (names, types, meaning) is **not duplicated here** — see MusicBrainz's own official schema documentation instead, to avoid this doc drifting out of sync as their schema evolves: https://musicbrainz.org/doc/MusicBrainz_Database/Schema

**Deliberate deviation from the source schema**: `gid` columns (Postgres `UUID`) are ingested as `str`, not left as Python `uuid.UUID` objects. Polars can't map `uuid.UUID` to a native dtype (falls back to `Object`, which can't be written to Parquet), so `db.py`'s `connect()` registers a psycopg loader override for the `uuid` type at the connection level — every other column matches the source schema exactly. Anyone relying on the official MusicBrainz docs for this column should know it's a string here, not the native UUID type.

**Recording ↔ link correspondence**: MusicBrainz stores external links (YouTube, official homepage, streaming services, etc.) as a general-purpose `url` table (`id`, `gid`, `url`) plus a relationship table per entity-type pair — `l_recording_url` (`entity0` = `recording.id`, `entity1` = `url.id`) is the one relevant here. Each `l_recording_url` row's `link` FK points to the `link` table, which in turn FKs to `link_type` (the row that categorizes *what kind* of relationship it is — "free streaming", "streaming", "license", etc., via `link_type.name`); both are now ingested, giving a precise `link_type.name` value per row instead of matching on `url.url` substrings. The Silver `1_recording_link` step (see [2. Silver](#2-silver)) uses this join, replacing the earlier substring-based YouTube-only approach. Not every recording has a link of a given type (community-submitted data, same caveat as tags/genres).

**Recording ↔ work correspondence**: `l_recording_work` (`entity0` = `recording.id`, `entity1` = `work.id`) links a recording to the composition(s) it performs. A recording can have zero, one, or several works (a medley). The Silver `5_songs` step uses it to group recordings of the same song (see [2. Silver](#2-silver)); `work` itself isn't ingested, only its id is needed.

**No direct recording ↔ genre link in the source data**: `genre` (`id`, `gid`, `name`, `comment`, ...) is a flat reference list with no foreign key to `recording` at all — confirmed via `DESCRIBE SELECT * FROM genre` against the bronze output. The only recording-level link available is `recording_tag` (many-to-many: `recording`, `tag`, `count`) — free-text folksonomy tags, not curated genres. A recording routinely has dozens of tags (e.g. one sample-dataset recording has 50 tags matching known genre names simultaneously: rock, electronic, post-rock, pop, jazz, metal, ...). Associating a recording with a genre means matching a tag's name against `genre.name` — not built yet, this is exactly what the Silver `recording_genre` table (see [2. Silver](#2-silver)) is for. Once it exists, a recording can and typically will map to **multiple** genres, not one.

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
| artist_type   | Artist type names (Person, Group, Orchestra, ...) — `artist.type` points here | not yet run locally |
| area          | Geographic areas (countries, subdivisions, cities) — `artist.area` points here | not yet run locally |
| iso_3166_1    | ISO 3166-1 country code per country `area` | not yet run locally |
| l_artist_artist | Many-to-many `artist` ↔ `artist` relationships (membership, collaboration, ...) | not yet run locally |
| link_attribute | Attributes attached to a `link` (instrument, vocal, original, ...) | not yet run locally |
| link_attribute_type | Attribute type tree (`root` points at the top-level attribute, e.g. "instrument") | not yet run locally |
| l_artist_url  | Many-to-many `artist` ↔ `url` relationship | not yet run locally |
| release_group | An album/single/EP grouping all its releases, with an `artist_credit` | not yet run locally |
| release_group_primary_type | Primary type names (Album, Single, EP, ...) | not yet run locally |
| release_group_secondary_type | Secondary type names (Compilation, Live, Soundtrack, ...) | not yet run locally |
| release_group_secondary_type_join | Many-to-many `release_group` ↔ secondary type | not yet run locally |
| release_group_meta | Derived per-release-group data, incl. first release date | not yet run locally |

## 2. Silver

Nine steps built so far.

`1_recording_link` (`pipelines/musicbrainz/src/musicbrainz/silver/recording_link.py`) — a recording ↔ link correspondence, typed by `link_type.name` (e.g. "free streaming", "streaming", "license"), derived from the Bronze `l_recording_url`/`url`/`link`/`link_type` tables per the note in [1. Bronze](#1-bronze):

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

`2_recording_genre` (`pipelines/musicbrainz/src/musicbrainz/silver/recording_genre.py`) — a recording ↔ genre correspondence, matching how MusicBrainz's own UI resolves a genre badge (see the note in [1. Bronze](#1-bronze)):

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
- Matching is by `tag.name`, case-insensitively — `genre` and `tag` share no key, see the note in [1. Bronze](#1-bronze). `lower()` is a placeholder for exact-string equality; not yet verified against real data whether MusicBrainz tag names ever deviate from lowercase (check with `SELECT name FROM tag WHERE name != lower(name) LIMIT 5` once bronze is regenerated) — if none do, `lower()` on both sides is redundant but harmless.
- Deliberately many-to-many, one row per (recording, matched genre) — no dedup/top-N logic. A recording with 50 tags matching 6 different genre names produces 6 `recording_genre` rows; picking a single "primary" genre, if ever needed, is a decision for a consumer, not this table.
- Run via `uv run --package musicbrainz python -m musicbrainz.silver`, writing `SILVER_OUTPUT_DIR/2_recording_genre.parquet`.

`3_youtube_candidates` (`songs.py`, `youtube_candidates`) — `(recording_id, youtube_video_id, rank)`: every distinct YouTube video id (extracted as below) of every recording, ranked by lexicographic `url`.

`4_youtube_video_status` (`youtube_video_status.py`) — `(youtube_video_id, youtube_unplayable_reason, checked_at)`: one row per checked candidate video id, checked via the YouTube Data API `videos.list` (`part=status,contentDetails`, 50 ids per call, 1 quota unit each; key from `YOUTUBE_API_KEY`, sent as a header). `youtube_unplayable_reason` is null when playable, else the first matching rule: absent from the response → `not_found`; `status.embeddable` false → `not_embeddable`; `privacyStatus` is `private` → `private` (unlisted plays fine); `uploadStatus` not `processed` → `not_processed`. Region restrictions (`allowed` or `blocked`) are not flagged — they depend on the viewer's country, so they're left to the runtime playback fallback (`region_whitelisted` stays in the enum but isn't emitted). Rows from the previous run's parquet are kept; each run fetches at most `YOUTUBE_STATUS_MAX_BATCHES_PER_RUN` batches, never-checked ids first, then ids older than 7 days (stalest first). Candidates beyond the budget have no row yet. 5xx/transport errors retry with backoff; a `quotaExceeded` 403 fails the run with a clear message, keeping completed batches.

`5_songs` (`pipelines/musicbrainz/src/musicbrainz/silver/songs.py`) — `(musicbrainz_recording_id, title, artist, artist_musicbrainz_id, youtube_video_id, youtube_unplayable_reason, genre_name)`, one row per song: every recording with a primary artist, deduplicated into song groups. Built from `3_youtube_candidates` + `4_youtube_video_status`, `2_recording_genre`, and Bronze `genre`/`recording`/`l_recording_work`/`artist_credit_name`/`artist`.

| Column | Type | Meaning |
| --- | --- | --- |
| `musicbrainz_recording_id` | str | `recording.gid` of the group's lowest `recording.id`; unique. |
| `title` | str | That recording's `recording.name`. |
| `artist` | str | Its primary artist (`artist_credit_name` position 0) `artist.name`. |
| `artist_musicbrainz_id` | str | That artist's `artist.gid`. |
| `youtube_video_id` | str, nullable | 11-char video id: the first playable checked candidate across the group by `rank` (then `recording_id`), else the best-ranked checked one; null when no candidate is checked yet or the group has none. |
| `youtube_unplayable_reason` | str, nullable | The chosen video's reason; null when playable or when there's no video. |
| `genre_name` | str, nullable | MusicBrainz `genre.name` with the highest weight summed across the group after curated precedence (ties: lowest `genre_id`); null when no recording of the group has a genre. |

- **Song group**: a recording linked to exactly one work (`l_recording_work`) is grouped by `(work, primary artist)`; any other recording (no work, or several) by `(normalized title, primary artist)`, the title lowercased, trimmed, and whitespace-collapsed. See [DESIGN.md](DESIGN.md#song-groups).
- **YouTube-only**: `link_type.name` (from `1_recording_link`) doesn't distinguish platform, so a YouTube candidate is a link whose `url` matches `youtube.com`/`youtu.be`.
- **Video id extraction** handles `youtu.be/<id>`, `youtube.com/watch?v=<id>`, `youtube.com/embed/<id>`, and `youtube.com/v/<id>`, capturing exactly 11 characters. Shorter ids and bare playlist/channel URLs are dropped; a stray trailing character isn't captured.
- **Genre precedence**: see [DESIGN.md](DESIGN.md#genre-precedence). The genre name is emitted as-is; reconciling it against wikidata's canonical names happens in `pipelines/gold`.
- Run via `uv run --package musicbrainz python -m musicbrainz.silver`, writing `SILVER_OUTPUT_DIR/3_youtube_candidates.parquet`, `4_youtube_video_status.parquet`, and `5_songs.parquet`.
- **On-demand JSON export**: `scripts/export_songs_json.py` reads `5_songs.parquet` and writes a flat JSON array (`[{"musicbrainz_recording_id": ..., "title": ..., "artist": ..., "artist_musicbrainz_id": ..., "youtube_video_id": ..., "youtube_unplayable_reason": ..., "genre_name": ...}, ...]`) for a developer to manually copy/commit into the downstream API repo. Run with `uv run --package musicbrainz python scripts/export_songs_json.py <output.json>` whenever a fresh copy is needed — not a scheduled job, no `infrastructure` involvement.

`6_artists` (`artists.py`) — one row per Bronze `artist`:

| Column | Type | Meaning |
| --- | --- | --- |
| `musicbrainz_artist_id` | str | `artist.gid`. |
| `name` | str | `artist.name`. |
| `type` | str, nullable | `artist_type.name`, lowercased (`person`, `group`, `orchestra`, `choir`, `character`, `other`). |
| `country` | str, nullable | `iso_3166_1.code` of `artist.area` (lowest code when an area has several); null when the area isn't a country. ISO user-assigned codes (`AA`, `QM`–`QZ`, `XA`–`XZ`, `ZZ`, which MusicBrainz uses for regions such as `XE` Europe and `XW` Worldwide) are ignored, except `XK` (Kosovo). |
| `begin_year`, `end_year` | int, nullable | `artist.begin_date_year`, `artist.end_date_year`. |
| `ended` | bool | `artist.ended`. |

`7_artist_members` (`artist_members.py`) — `(group_musicbrainz_artist_id, member_musicbrainz_artist_id, begin_year, end_year, ended, roles)`, one row per `l_artist_artist` "member of band" link (artist → artist). `begin_year`, `end_year` and `ended` come from the `link`; `ended` is true for a past membership even when its end year is unknown. `roles` is the sorted list of the link's `link_attribute_type` names whose root is `instrument` or `vocal`; other attributes (original, additional, ...) are dropped; empty when none.

`8_artist_links` (`artist_links.py`) — `(musicbrainz_artist_id, kind, url)`, one row per `l_artist_url` link of a curated kind that hasn't ended (`link.ended` false). `kind` is `spotify` for any `open.spotify.com` URL, else mapped from `link_type.name` by `LINK_KINDS` (`official homepage` → `official`, `social network`/`myspace` → `social`, plus `wikipedia`, `wikidata`, `youtube`, `bandcamp`, `soundcloud`, `discogs`); unmapped types are dropped. See [DESIGN.md](DESIGN.md#artist-links).

`9_artist_release_groups` (`artist_release_groups.py`) — `(musicbrainz_artist_id, musicbrainz_release_group_id, title, primary_type, secondary_types, first_release_year)`, one row per (release group, credited artist): every `artist_credit_name` of the release group's credit, not only position 0. `primary_type` and `secondary_types` (sorted list, empty when none) are lowercased type names; `first_release_year` is `release_group_meta.first_release_date_year`.

Steps 6–9 run after `5_songs` in `python -m musicbrainz.silver`, unfiltered; scoping to song artists happens in `pipelines/gold`.

`recording_genre_path` is not built yet (see [README.md#pipeline](README.md#pipeline)).
