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
    recording_genre = pl.read_parquet(silver_dir / "2_recording_genre.parquet")

    # Only recordings that can become a song (one with a genre) are kept, so the YouTube status
    # check downstream doesn't spend API quota on videos that would never be exported.
    candidates = (
        recording_link.filter(pl.col("url").str.contains(_YOUTUBE_URL_PATTERN))
        .join(recording_genre.select("recording_id").unique(), on="recording_id", how="semi")
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


def songs(bronze_dir: Path, silver_dir: Path, output_dir: Path) -> Path:
    candidates = pl.read_parquet(silver_dir / "3_youtube_candidates.parquet")
    video_status = pl.read_parquet(silver_dir / "4_youtube_video_status.parquet")
    recording_genre = pl.read_parquet(silver_dir / "2_recording_genre.parquet")
    genre = pl.read_parquet(bronze_dir / "genre.parquet")
    recording = pl.read_parquet(bronze_dir / "recording.parquet")
    artist_credit_name = pl.read_parquet(bronze_dir / "artist_credit_name.parquet")
    artist = pl.read_parquet(bronze_dir / "artist.parquet")

    candidate_status = candidates.join(video_status, on="youtube_video_id", how="left")
    unchecked = candidate_status.filter(pl.col("checked_at").is_null())
    if not unchecked.is_empty():
        raise ValueError(f"{unchecked.height} YouTube candidate(s) missing from 4_youtube_video_status.parquet")

    # First playable candidate by rank; a recording with no playable candidate keeps its rank-1
    # video and that video's reason, so it is flagged downstream rather than dropped.
    youtube_video = (
        candidate_status.sort(pl.col("recording_id"), pl.col("youtube_unplayable_reason").is_not_null(), pl.col("rank"))
        .unique(subset="recording_id", keep="first", maintain_order=True)
        .select("recording_id", "youtube_video_id", "youtube_unplayable_reason")
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
        .select("title", "artist_name", "youtube_video_id", "youtube_unplayable_reason", "genre_name")
        .rename({"artist_name": "artist"})
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "5_songs.parquet"
    result.write_parquet(output_path)
    logger.info("wrote %d rows to %s", result.height, output_path)
    return output_path
