import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# `link_type.name` (e.g. "free streaming", "streaming") doesn't distinguish platform — the same
# name is used for YouTube, Bandcamp, etc. (see SCHEMA.md#1-bronze) — so a YouTube video still has
# to be identified by matching `url.url` itself, same as the retired `1_recording_youtube_url` step.
_YOUTUBE_URL_PATTERN = r"(?:youtube(?:-nocookie)?\.com|youtu\.be)"

# Captures the video id out of the URL shapes MusicBrainz actually stores for YouTube links:
# `youtu.be/<id>`, `youtube.com/watch?v=<id>`, `youtube.com/embed/<id>`, `youtube.com/v/<id>`.
# A YouTube video id is always exactly 11 characters — anchoring the count here (rather than
# `{6,}`) both drops URLs with a truncated/malformed id (fewer than 11 valid characters) and stops
# a stray trailing character (e.g. a `-` MusicBrainz's own data appends) from being swallowed into
# the captured id. A bare channel/playlist URL (no video id in any of those positions) doesn't
# match and is dropped — there's no video to point a song at.
_VIDEO_ID_PATTERN = r"(?:[?&]v=|youtu\.be/|/embed/|/v/)([A-Za-z0-9_-]{11})"


def youtube_candidates(silver_dir: Path, output_dir: Path) -> Path:
    recording_link = pl.read_parquet(silver_dir / "1_recording_link.parquet")

    candidates = (
        recording_link.filter(pl.col("url").str.contains(_YOUTUBE_URL_PATTERN))
        .with_columns(pl.col("url").str.extract(_VIDEO_ID_PATTERN, 1).alias("youtube_video_id"))
        .drop_nulls("youtube_video_id")
        .sort("recording_id", "url")
        .unique(subset=["recording_id", "youtube_video_id"], keep="first", maintain_order=True)
        .with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.UInt32).over("recording_id").alias("rank"))
        .select("recording_id", "youtube_video_id", "rank")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "3_youtube_candidates.parquet"
    candidates.write_parquet(output_path)
    logger.info("wrote %d rows to %s", candidates.height, output_path)
    return output_path


def _precedence_closure(manual_genre_precedence_path: Path, genre: pl.DataFrame) -> pl.DataFrame:
    rules = pl.read_csv(
        manual_genre_precedence_path,
        schema={"musicbrainz_genre_name": pl.Utf8, "over_musicbrainz_genre_name": pl.Utf8, "reason": pl.Utf8},
    ).select(winner="musicbrainz_genre_name", loser="over_musicbrainz_genre_name")

    if rules.null_count().sum_horizontal().item():
        raise ValueError(f"{manual_genre_precedence_path.name} has row(s) with a blank genre name")

    # grow-api accepts names with stray whitespace or typos, so an exact match against Bronze is the guard.
    unknown = sorted((set(rules["winner"]) | set(rules["loser"])) - set(genre["name"]))
    if unknown:
        raise ValueError(f"{manual_genre_precedence_path.name} names genre(s) absent from genre.parquet: {unknown}")

    # Transitive, so a winner also beats whatever the genres it beats beat (ska > reggae > dub => ska > dub).
    closure = rules.unique()
    while True:
        extended = pl.concat(
            [closure, closure.join(closure, left_on="loser", right_on="winner").select("winner", loser="loser_right")]
        ).unique()
        if extended.height == closure.height:
            break
        closure = extended

    # grow-api doesn't reject reversed or longer cycles, so the pipeline is the guard.
    cyclic = sorted(closure.filter(pl.col("winner") == pl.col("loser"))["winner"])
    if cyclic:
        raise ValueError(f"{manual_genre_precedence_path.name} has a self-pair or cycle involving: {cyclic}")

    name_to_id = genre.select(pl.col("name"), pl.col("id"))
    return (
        closure.join(name_to_id, left_on="winner", right_on="name")
        .join(name_to_id, left_on="loser", right_on="name", suffix="_loser")
        .select(winner_id="id", loser_id="id_loser")
    )


def _apply_genre_precedence(song_genre: pl.LazyFrame, closure: pl.LazyFrame) -> pl.LazyFrame:
    beaten = song_genre.join(closure, left_on="genre_id", right_on="loser_id").join(
        song_genre.select("song_id", winner_id="genre_id"),
        on=["song_id", "winner_id"],
        how="semi",
    )
    inherited = beaten.group_by("song_id", "winner_id").agg(pl.col("weight").max().alias("beaten_weight"))
    return (
        song_genre.join(beaten.select("song_id", "genre_id").unique(), on=["song_id", "genre_id"], how="anti")
        .join(inherited, left_on=["song_id", "genre_id"], right_on=["song_id", "winner_id"], how="left")
        .with_columns(pl.max_horizontal("weight", "beaten_weight").alias("weight"))
        .drop("beaten_weight")
    )


def _song_recordings(bronze_dir: Path) -> pl.LazyFrame:
    """One row per recording with a primary artist: `(recording_id, song_id, musicbrainz_recording_id, title,
    artist)`, `song_id` being the lowest `recording.id` of its song group."""
    # `position == 0` is MusicBrainz's own primary-artist slot (collaborations/features carry more rows).
    primary_artist = (
        pl.scan_parquet(bronze_dir / "artist_credit_name.parquet")
        .filter(pl.col("position") == 0)
        .join(pl.scan_parquet(bronze_dir / "artist.parquet").select(artist="id", artist_name="name"), on="artist")
        .select("artist_credit", artist_id="artist", artist_name="artist_name")
    )
    single_work = (
        pl.scan_parquet(bronze_dir / "l_recording_work.parquet")
        .group_by(recording_id="entity0")
        .agg(pl.col("entity1").unique())
        .filter(pl.col("entity1").list.len() == 1)
        .select("recording_id", work_id=pl.col("entity1").list.first())
    )
    recordings = (
        pl.scan_parquet(bronze_dir / "recording.parquet")
        .select(recording_id="id", musicbrainz_recording_id="gid", title="name", artist_credit="artist_credit")
        .join(primary_artist, on="artist_credit")
        .join(single_work, on="recording_id", how="left")
        # A recording linked to exactly one work is that work's performance by its primary artist; any other
        # (no work, or a medley of several) falls back to its normalized title. The null half of each key keeps
        # the two kinds of group apart.
        .with_columns(
            normalized_title=pl.when(pl.col("work_id").is_null()).then(
                pl.col("title").str.to_lowercase().str.strip_chars().str.replace_all(r"\s+", " ")
            )
        )
    )
    return recordings.select(
        "recording_id",
        song_id=pl.col("recording_id").min().over("artist_id", "work_id", "normalized_title"),
        musicbrainz_recording_id="musicbrainz_recording_id",
        title="title",
        artist="artist_name",
    )


def songs(bronze_dir: Path, silver_dir: Path, output_dir: Path, manual_genre_precedence_path: Path) -> Path:
    genre = pl.read_parquet(bronze_dir / "genre.parquet")
    closure = _precedence_closure(manual_genre_precedence_path, genre).lazy()

    song_recordings = _song_recordings(bronze_dir).cache()
    recording_song = song_recordings.select("recording_id", "song_id")

    # Unchecked candidates (beyond this run's YouTube quota budget) aren't eligible yet: the song goes out without
    # a video until a later run checks them. First playable candidate across the group by rank, else the best-ranked
    # checked one with its reason, so it's flagged downstream rather than dropped.
    youtube_video = (
        pl.scan_parquet(silver_dir / "3_youtube_candidates.parquet")
        .join(pl.scan_parquet(silver_dir / "4_youtube_video_status.parquet"), on="youtube_video_id")
        .join(recording_song, on="recording_id")
        .sort("song_id", pl.col("youtube_unplayable_reason").is_not_null(), "rank", "recording_id")
        .unique(subset="song_id", keep="first", maintain_order=True)
        .select("song_id", "youtube_video_id", "youtube_unplayable_reason")
    )

    # manual_genre_precedence.csv (curated in grow-the-music-tree-api): broad tags (pop, rock) carry more votes than
    # precise ones (pop rock, ska), so a song tagged with both drops the broad one and its weight passes to the
    # precise winner before the highest-weight pick. Weights are summed across the song's recordings first.
    song_genre = (
        pl.scan_parquet(silver_dir / "2_recording_genre.parquet")
        .join(recording_song, on="recording_id")
        .group_by("song_id", "genre_id")
        .agg(pl.col("weight").sum())
    )
    primary_genre = (
        _apply_genre_precedence(song_genre, closure)
        .sort(["weight", "genre_id"], descending=[True, False])
        .unique(subset="song_id", keep="first", maintain_order=True)
        .join(genre.lazy().select(genre_id="id", genre_name="name"), on="genre_id")
        .select("song_id", "genre_name")
    )

    result = (
        song_recordings.filter(pl.col("recording_id") == pl.col("song_id"))
        .join(youtube_video, on="song_id", how="left")
        .join(primary_genre, on="song_id", how="left")
        .select(
            "musicbrainz_recording_id",
            "title",
            "artist",
            "youtube_video_id",
            "youtube_unplayable_reason",
            "genre_name",
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "5_songs.parquet"
    result.sink_parquet(output_path, engine="streaming")
    counts = (
        pl.scan_parquet(output_path)
        .select(
            songs=pl.len(),
            without_video=pl.col("youtube_video_id").null_count(),
            without_genre=pl.col("genre_name").null_count(),
        )
        .collect()
        .row(0, named=True)
    )
    logger.info(
        "wrote %d songs to %s, %d without a video, %d without a genre",
        counts["songs"],
        output_path,
        counts["without_video"],
        counts["without_genre"],
    )
    return output_path
