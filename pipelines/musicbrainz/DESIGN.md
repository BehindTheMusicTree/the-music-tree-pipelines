# musicbrainz — design notes

Rationale behind the musicbrainz pipeline's derivations. Columns and types live in [SCHEMA.md](SCHEMA.md).

## Genre precedence

`5_songs` keeps one genre per recording. Picking by `recording_genre.weight` alone lets broad tags
win systematically, because they collect more votes than precise ones (staging data: pop beat pop
rock 659 times, rock beat classic rock 330 times, and reggae/dub beat ska on all 13 ska recordings,
leaving ska with no songs).

`manual_genre_precedence.csv` (curated in grow-the-music-tree-api's `genre_precedence` list, pulled
into `CURATION_BRONZE_DIR` by the `curation` pipeline) lists `(musicbrainz_genre_name,
over_musicbrainz_genre_name, reason)` rules: when a recording carries both genres, the more precise
`musicbrainz_genre_name` wins. Names are MusicBrainz `genre.name` values.

Before the weight pick, per recording:

- Rules apply transitively (ska over reggae, reggae over dub, so ska over dub too).
- Every genre beaten by another genre on the same recording is dropped.
- Each surviving winner's weight becomes the max of its own weight and the weights of the genres it
  beat, so the precise genre also beats unrelated genres the broad one would have beaten.
- The usual rule then applies: highest weight, ties go to the lowest `genre_id`.

The run fails fast on a self-pair, on a cycle of any length, on a blank name, and on a name absent
from Bronze `genre.parquet`. grow-api accepts reversed and longer cycles and untrimmed names (such as
`"ska "`), so the pipeline is the guard.
