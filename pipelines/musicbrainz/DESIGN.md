# musicbrainz — design notes

Rationale behind the musicbrainz pipeline's derivations. Columns and types live in [SCHEMA.md](SCHEMA.md).

## Song groups

`5_songs` keeps every recording with a primary artist, collapsed into songs, because the goal is a
reference classifying all music: a song without a checked video or a genre is still exported, with
nulls. MusicBrainz holds many recordings of one song (album, single, remaster, live), so one row per
recording would flood the catalog with duplicates.

- A recording linked to exactly one work is grouped by `(work, primary artist)`: the work is the
  composition, the artist keeps covers apart.
- Any other recording (no work, or a medley of several) falls back to `(normalized title, primary
  artist)`, the title lowercased, trimmed and whitespace-collapsed. Grouping a medley under one of
  its works would merge unrelated songs.
- The group's lowest `recording.id` is its representative (stable across runs): its `gid`, title
  and artist are emitted. Videos and genre weights are pooled across the group, so a video or tag on
  any version counts.

## YouTube status budget

The YouTube Data API's default quota is 10,000 units a day, one unit per 50-id `videos.list` call.
`YOUTUBE_STATUS_MAX_BATCHES_PER_RUN` caps calls per run (required, no default), spending them on
never-checked ids first, then on the stalest ids past the 7-day TTL. The full catalog is covered over
several daily runs; songs whose candidates aren't checked yet export without a video until then.

## Curated video override

`manual_recording_youtube_video.csv` used to be applied in Gold, after Silver had picked and checked a
video, so a curated video was never checked for playability. It is applied here instead: each curated
video is a `pinned` rank-0 candidate, `youtube_video_status` spends budget on pinned ids first, and
`songs` always keeps the curated video (or no video, when its id is empty), with the curated
`unplayable_reason` winning over the checked one. `songs_only` reads the same CSV but checks nothing, so a
newly curated video ships with a null reason until the next full Silver run.

## Genre precedence

`5_songs` keeps one genre per song. Picking by `recording_genre.weight` alone lets broad tags
win systematically, because they collect more votes than precise ones (staging data: pop beat pop
rock 659 times, rock beat classic rock 330 times, and reggae/dub beat ska on all 13 ska recordings,
leaving ska with no songs).

`manual_genre_precedence.csv` (curated in grow-the-music-tree-api's `genre_precedence` list, pulled
into `CURATION_BRONZE_DIR` by the `curation` pipeline) lists `(musicbrainz_genre_name,
over_musicbrainz_genre_name, reason)` rules: when a song carries both genres, the more precise
`musicbrainz_genre_name` wins. Names are MusicBrainz `genre.name` values.

Before the weight pick, per song (weights summed across its recordings):

- Rules apply transitively (ska over reggae, reggae over dub, so ska over dub too).
- Every genre beaten by another genre on the same song is dropped.
- Each surviving winner's weight becomes the max of its own weight and the weights of the genres it
  beat, so the precise genre also beats unrelated genres the broad one would have beaten.
- The usual rule then applies: highest weight, ties go to the lowest `genre_id`.

The run fails fast on a self-pair, on a cycle of any length, on a blank name, and on a name absent
from Bronze `genre.parquet`. grow-api accepts reversed and longer cycles and untrimmed names (such as
`"ska "`), so the pipeline is the guard.

## First release year

The MusicBrainz mirror holds years from 20 to 3036. `common.quality_checks.FIRST_RELEASE_YEAR_RANGE`
(1000-2100) is the single definition of a valid `first_release_year`, shared by `5_songs`, `9_artist_release_groups` and `gold`.

- `5_songs` drops out-of-range recording years before taking the per-song `min()`: it picks among
  the group's recordings, so one corrupt early year (e.g. 20) can't erase a song's valid one.
- `9_artist_release_groups` nulls an out-of-range year in place and keeps the row: a release group
  has a single year, and dropping the row would remove a real album from the discography. Nulls sort
  last within the artist.

## Artist links

`8_artist_links` keeps the link kinds an artist page shows (official site, encyclopedias, the main
listening and social platforms) and drops the rest (lyrics sites, VIAF, blogs, purchase links...),
which would be noise. Spotify has no link type of its own (it's filed under the generic streaming
types shared by every platform), so it's matched by URL.

## Artist scope

Silver steps 6–9 cover every MusicBrainz artist, so they don't depend on the song set. `gold`'s
artist export scopes them to what an artist page links to: song artists, the groups they're members
of, and the members of all of those. Special-purpose artists (Various Artists, `[unknown]`, ...) are
left out: they're placeholders credited on huge numbers of releases, not artists with a page.

An ended link (`link.ended`) is dropped from `8_artist_links`: a lapsed domain or closed account no
longer points at the artist. An ended membership is kept with `ended` set, since a past member
belongs on the band's page.
