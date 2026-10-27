import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

MAX_PASSES = 100
ORDER_SCHEMA = {"column_id": pl.String, "position": pl.Int32}
FRAGMENT_SCHEMA = {
    "entity_id": pl.String,
    "label": pl.String,
    "fragment": pl.Int32,
    "start_position": pl.Int32,
    "end_position": pl.Int32,
    "start_year": pl.Int32,
    "end_year": pl.Int32,
}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _cost(order: list[str], bands: dict[str, set[str]]) -> int:
    position = {column: index for index, column in enumerate(order)}
    total = 0
    for columns in bands.values():
        places = [position[c] for c in columns]
        total += max(places) - min(places) + 1 - len(places)
    return total


def _seriate(prior: list[str], bands: dict[str, set[str]]) -> list[str]:
    order = list(prior)
    cost = _cost(order, bands)
    for _ in range(MAX_PASSES):
        improved = False
        for index in range(len(order) - 1):
            order[index], order[index + 1] = order[index + 1], order[index]
            swapped = _cost(order, bands)
            if swapped < cost:
                cost, improved = swapped, True
            else:
                order[index], order[index + 1] = order[index + 1], order[index]
        if not improved:
            return order
    logger.warning("lane seriation stopped at the %d pass cap with cost %d", MAX_PASSES, cost)
    return order


def lane_order(silver_dir: Path) -> tuple[Path, Path]:
    lanes = pl.read_parquet(silver_dir / "6_genealogy_lane.parquet")
    band_rows = pl.read_parquet(silver_dir / "6_genealogy_band.parquet")
    entities = pl.read_parquet(silver_dir / "1_cultural_entity.parquet")
    longitude = {e: lon for e, lon in entities.select("entity_id", "longitude").rows() if lon is not None}

    bands: dict[str, set[str]] = {}
    for band, column in band_rows.select("entity_id", "column_id").rows():
        bands.setdefault(band, set()).add(column)
    column_lanes = lanes.group_by("column_id").agg("entity_id").sort("column_id")

    centroid: dict[str, float | None] = {
        column: _mean([longitude[lane] for lane in members if lane in longitude])
        for column, members in column_lanes.select("column_id", "entity_id").rows()
    }
    for column, value in list(centroid.items()):
        if value is None:
            centroid[column] = _mean(
                [longitude[b] for b, columns in bands.items() if column in columns and b in longitude]
            )
    known = [v for v in centroid.values() if v is not None]
    neutral = _mean(known)
    if neutral is None:
        raise ValueError("no lane or band has coordinates, cannot seed the west-to-east order")
    prior = sorted(centroid, key=lambda column: (centroid[column] if centroid[column] is not None else neutral, column))
    order = _seriate(prior, bands)
    position = {column: index for index, column in enumerate(order)}

    fragments = []
    labels = dict(band_rows.select("entity_id", "label").unique().rows())
    spans = {b: (s, e) for b, s, e in band_rows.select("entity_id", "start_year", "end_year").unique().rows()}
    for band, columns in sorted(bands.items()):
        places = sorted(position[c] for c in columns)
        runs: list[list[int]] = [[places[0]]]
        for place in places[1:]:
            if place == runs[-1][-1] + 1:
                runs[-1].append(place)
            else:
                runs.append([place])
        fragments.extend((band, labels[band], i, run[0], run[-1], *spans[band]) for i, run in enumerate(runs))

    order_path, fragment_path = silver_dir / "7_lane_order.parquet", silver_dir / "7_band_fragment.parquet"
    pl.DataFrame([(c, i) for i, c in enumerate(order)], schema=ORDER_SCHEMA, orient="row").write_parquet(order_path)
    pl.DataFrame(fragments, schema=FRAGMENT_SCHEMA, orient="row").write_parquet(fragment_path)
    logger.info("ordered %d columns, %d band fragments to %s", len(order), len(fragments), silver_dir)
    return order_path, fragment_path
