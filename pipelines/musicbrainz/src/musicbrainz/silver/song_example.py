import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# Recordings per matched genre kept in the example set — this is a small demo fixture, not a
# full export, so only the top-weighted recordings per genre are retained.
RECORDINGS_PER_GENRE = 5

# `link_type.name` (e.g. "free streaming", "streaming") doesn't distinguish platform — the same
# name is used for YouTube, Bandcamp, etc. (see SCHEMA.md#bronze) — so a YouTube video still has
# to be identified by matching `url.url` itself, same as the retired `1_recording_youtube_url` step.
_YOUTUBE_URL_PATTERN = r"(?:youtube(?:-nocookie)?\.com|youtu\.be)"

# Captures the video id out of the URL shapes MusicBrainz actually stores for YouTube links:
# `youtu.be/<id>`, `youtube.com/watch?v=<id>`, `youtube.com/embed/<id>`, `youtube.com/v/<id>`.
# A bare channel/playlist URL (no video id in any of those positions) doesn't match and is
# dropped — there's no video to point a "song example" at.
_VIDEO_ID_PATTERN = r"(?:[?&]v=|youtu\.be/|/embed/|/v/)([A-Za-z0-9_-]{6,})"


def song_example(bronze_dir: Path, silver_dir: Path, output_dir: Path) -> Path:
    recording_link = pl.read_parquet(silver_dir / "1_recording_link.parquet")
    recording_genre = pl.read_parquet(silver_dir / "2_recording_genre.parquet")
    genre = pl.read_parquet(bronze_dir / "genre.parquet")
    recording = pl.read_parquet(bronze_dir / "recording.parquet")
    artist_credit_name = pl.read_parquet(bronze_dir / "artist_credit_name.parquet")
    artist = pl.read_parquet(bronze_dir / "artist.parquet")

    youtube_video = (
        recording_link.filter(pl.col("url").str.contains(_YOUTUBE_URL_PATTERN))
        .with_columns(pl.col("url").str.extract(_VIDEO_ID_PATTERN, 1).alias("youtube_video_id"))
        .drop_nulls("youtube_video_id")
        .sort("url")
        .unique(subset="recording_id", keep="first", maintain_order=True)
        .select("recording_id", "youtube_video_id")
    )

    primary_genre = (
        recording_genre.sort(["weight", "genre_id"], descending=[True, False])
        .unique(subset="recording_id", keep="first", maintain_order=True)
        .join(genre.select(pl.col("id").alias("genre_id"), pl.col("name").alias("genre_name")), on="genre_id")
        .select("recording_id", "genre_name", "weight")
    )

    # An `artist_credit` can carry several `artist_credit_name` rows (collaborations, features).
    # `position == 0` is MusicBrainz's own primary-artist slot, so it's used as the single
    # display artist here rather than concatenating every credited artist and its `join_phrase`.
    primary_artist_name = (
        artist_credit_name.filter(pl.col("position") == 0)
        .join(artist.select(pl.col("id").alias("artist"), pl.col("name").alias("artist_name")), on="artist")
        .select(pl.col("artist_credit"), "artist_name")
    )

    recording_title_artist = recording.select(
        pl.col("id").alias("recording_id"), pl.col("name").alias("title"), pl.col("artist_credit")
    ).join(primary_artist_name, on="artist_credit", how="inner")

    result = (
        youtube_video.join(primary_genre, on="recording_id", how="inner")
        .join(recording_title_artist, on="recording_id", how="inner")
        .sort("weight", descending=True)
        .group_by("genre_name", maintain_order=True)
        .head(RECORDINGS_PER_GENRE)
        .select("title", "artist_name", "youtube_video_id", "genre_name")
        .rename({"artist_name": "artist"})
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "3_song_example.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
