from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from gold.curation_csv import ITEM_ID_PATTERN, read_curation_csv

_REC = "musicbrainz_recording_id"


@dataclass(frozen=True)
class Disambiguation:
    """`candidates` (`key, item_id, is_default`): the genres an ambiguous tag's genre key may mean. `hints`
    (`key, item_id, context_item_id`): a song carrying `context_item_id` resolves the key to `item_id`, already
    expanded to every genre under the curated context genre."""

    candidates: pl.DataFrame
    hints: pl.DataFrame

    @property
    def keys(self) -> pl.Series:
        return self.candidates.get_column("key").unique()

    def with_automatic(self, automatic: pl.DataFrame) -> "Disambiguation":
        """Adds `automatic` candidates (`key, item_id, is_default`) for the keys not curated."""
        return Disambiguation(
            pl.concat([self.candidates, automatic.join(self.candidates, on="key", how="anti")]), self.hints
        )


def load_curated_disambiguation(
    disambiguation_path: Path,
    context_path: Path,
    item_ids: set[str],
    taken_keys: pl.Series,
    closure: pl.DataFrame,
    genre_key: Callable[[pl.Expr], pl.Expr],
) -> Disambiguation:
    """`manual_genre_disambiguation.csv` + `manual_genre_disambiguation_context.csv`, keyed by genre key. Raises
    on an unknown item, a key an earlier cascade step other than `normalized` already resolves (`taken_keys`:
    lowercase labels, alias and non-genre keys), a key with fewer than 2 candidates or several defaults, or a hint
    on an unlisted candidate."""
    candidates = read_curation_csv(
        disambiguation_path,
        required=("musicbrainz_genre_name", "item_id", "reason"),
        key=("musicbrainz_genre_name", "item_id"),
        patterns={"item_id": ITEM_ID_PATTERN},
    ).select(
        key=genre_key(pl.col("musicbrainz_genre_name")),
        item_id="item_id",
        is_default=pl.col("is_default").fill_null("").str.strip_chars().str.to_lowercase() == "true",
    )
    name = disambiguation_path.name
    unknown = sorted(set(candidates["item_id"]) - item_ids)
    if unknown:
        raise ValueError(f"{name} references item_id(s) not found in the canonical hierarchy: {unknown}")
    taken = sorted(candidates.filter(pl.col("key").is_in(taken_keys.implode()))["key"].unique())
    if taken:
        raise ValueError(f"{name} has genre key(s) already resolved by a label, alias or non-genre entry: {taken}")
    if candidates.select("key", "item_id").is_duplicated().any():
        raise ValueError(f"{name} lists a candidate twice for one genre key (spellings differing by separator)")
    per_key = candidates.group_by("key").agg(count=pl.len(), defaults=pl.col("is_default").sum())
    single = sorted(per_key.filter(pl.col("count") < 2)["key"])
    if single:
        raise ValueError(f"{name} has genre key(s) with fewer than 2 candidates, use manual_genre_alias.csv: {single}")
    several_defaults = sorted(per_key.filter(pl.col("defaults") > 1)["key"])
    if several_defaults:
        raise ValueError(f"{name} has genre key(s) with several defaults: {several_defaults}")

    hints = read_curation_csv(
        context_path,
        required=("musicbrainz_genre_name", "item_id", "context_item_id", "reason"),
        key=("musicbrainz_genre_name", "item_id", "context_item_id"),
        patterns={"item_id": ITEM_ID_PATTERN, "context_item_id": ITEM_ID_PATTERN},
    ).select(key=genre_key(pl.col("musicbrainz_genre_name")), item_id="item_id", context_item_id="context_item_id")
    unknown_context = sorted(set(hints["context_item_id"]) - item_ids)
    if unknown_context:
        raise ValueError(
            f"{context_path.name} references context_item_id(s) not found in the canonical hierarchy: {unknown_context}"
        )
    unlisted = hints.join(candidates, on=["key", "item_id"], how="anti")
    if not unlisted.is_empty():
        raise ValueError(
            f"{context_path.name} has hint(s) on a candidate not in {name}: {unlisted.select('key', 'item_id').rows()}"
        )
    under_context = hints.join(closure, left_on="context_item_id", right_on="loser").select(
        "key", "item_id", context_item_id="winner"
    )
    return Disambiguation(candidates, pl.concat([hints, under_context]).unique())


def most_precise(items: pl.DataFrame, group: list[str], closure: pl.DataFrame) -> pl.DataFrame:
    """`items` minus every `item_id` beaten, in `closure`, by another `item_id` of its `group`."""
    beaten = items.join(closure, left_on="item_id", right_on="loser").join(
        items.select(*group, winner="item_id"), on=[*group, "winner"], how="semi"
    )
    return items.join(beaten.select(*group, "item_id").unique(), on=[*group, "item_id"], how="anti")


def disambiguate(
    ambiguous_tags: pl.LazyFrame,
    context: pl.LazyFrame,
    disambiguation: Disambiguation,
    closure: pl.DataFrame,
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """`ambiguous_tags` (`musicbrainz_recording_id, genre_name, key, weight`) against the song's `context`
    (`musicbrainz_recording_id, context_item_id`, its unambiguously resolved genres). Returns the picks
    (`musicbrainz_recording_id, genre_name, item_id, weight`) and the ignored tags
    (`musicbrainz_recording_id, genre_name`): a tag whose candidate, or a genre under it, is already on the song
    adds nothing. A candidate fits when one of the song's genres is under it or under one of its hints; the most
    precise fitting candidate is picked when it is the only one left, else the default."""
    rules = closure.lazy()
    tag = [_REC, "genre_name"]
    pairs = ambiguous_tags.select(*tag, "key").join(disambiguation.candidates.lazy(), on="key")

    on_song = pl.concat(
        [
            context.select(_REC, item_id="context_item_id"),
            context.join(rules, left_on="context_item_id", right_on="winner").select(_REC, item_id="loser"),
        ]
    )
    ignored = pairs.join(on_song, on=[_REC, "item_id"], how="semi").select(tag).unique()
    pairs = pairs.join(ignored, on=tag, how="anti")

    under_candidate = pairs.join(rules, left_on="item_id", right_on="winner").join(
        context, left_on=[_REC, "loser"], right_on=[_REC, "context_item_id"], how="semi"
    )
    hinted = pairs.join(disambiguation.hints.lazy(), on=["key", "item_id"]).join(
        context, on=[_REC, "context_item_id"], how="semi"
    )
    fits = pl.concat([under_candidate.select(*tag, "item_id"), hinted.select(*tag, "item_id")]).unique().collect()
    precise = most_precise(fits, tag, closure)
    single_fit = precise.join(
        precise.group_by(tag).len().filter(pl.col("len") == 1).select(tag), on=tag, how="semi"
    ).lazy()
    defaults = pairs.filter(pl.col("is_default")).join(single_fit, on=tag, how="anti").select(*tag, "item_id")
    picks = pl.concat([single_fit, defaults]).join(ambiguous_tags.select(*tag, "weight"), on=tag)
    return picks, ignored
