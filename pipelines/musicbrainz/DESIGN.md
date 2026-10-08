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
