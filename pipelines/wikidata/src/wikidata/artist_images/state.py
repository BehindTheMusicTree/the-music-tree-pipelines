"""Immutable image-state generations and ordered replacement checkpoints."""

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from wikidata.artist_images.publish import _sync_directory, _write_json

_TABLE_KEYS = {"registry": "item_id", "claims": "item_id", "commons": "image_file"}


@dataclass(frozen=True)
class ImageState:
    generation: str
    sequence: int
    tables: dict[str, pl.DataFrame]
    provenance: dict


def _validate_state(state: ImageState) -> None:
    if set(state.tables) != set(_TABLE_KEYS) or type(state.sequence) is not int or state.sequence < 0:
        raise ValueError("Invalid image state tables or sequence")
    for name, key in _TABLE_KEYS.items():
        frame = state.tables[name]
        if key not in frame.columns or frame.schema[key] != pl.String or frame[key].null_count():
            raise ValueError(f"Invalid image state key: {name}")
        if name != "claims" and frame[key].n_unique() != frame.height:
            raise ValueError(f"Duplicate image state key: {name}")
    if not set(state.tables["claims"]["item_id"]) <= set(state.tables["registry"]["item_id"]):
        raise ValueError("Image claims reference unregistered QIDs")


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError(f"Invalid image state manifest: {path}")
    return value


def _write_tables(directory: Path, tables: dict[str, pl.DataFrame], manifest: dict) -> None:
    directory.mkdir()
    artifacts = {}
    for name, frame in tables.items():
        if name not in _TABLE_KEYS:
            raise ValueError(f"Unknown image state table: {name}")
        path = directory / f"{name}.parquet"
        frame.write_parquet(path)
        with path.open("rb") as source:
            os.fsync(source.fileno())
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        artifacts[name] = {"sha256": digest, "rowCount": frame.height}
    _write_json(directory / "manifest.json", {**manifest, "schemaVersion": 1, "artifacts": artifacts})
    _sync_directory(directory)


def _read_tables(directory: Path, manifest: dict) -> dict[str, pl.DataFrame]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts or not set(artifacts) <= set(_TABLE_KEYS):
        raise ValueError("Invalid image state artifacts")
    tables = {}
    for name, metadata in artifacts.items():
        path = directory / f"{name}.parquet"
        if path.is_symlink() or not isinstance(metadata, dict):
            raise ValueError("Invalid image state artifact")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != metadata.get("sha256"):
            raise ValueError("Image state checksum mismatch")
        frame = pl.read_parquet(path)
        if type(metadata.get("rowCount")) is not int or frame.height != metadata["rowCount"]:
            raise ValueError("Image state row count mismatch")
        tables[name] = frame
    return tables


def compact_state(root: Path, state: ImageState) -> ImageState:
    """Commit a new base and pointer; caller holds the single-writer lock."""
    _validate_state(state)
    root.mkdir(parents=True, exist_ok=True)
    bases = root / "base"
    bases.mkdir(exist_ok=True)
    generation = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=".base-", dir=root) as temporary:
        candidate = Path(temporary) / generation
        _write_tables(
            candidate,
            state.tables,
            {
                "generation": generation,
                "sequence": state.sequence,
                "provenance": state.provenance,
            },
        )
        _read_tables(candidate, _read_json(candidate / "manifest.json"))
        os.rename(candidate, bases / generation)
        _sync_directory(bases)
        pointer = root / f".current-{generation}.json"
        try:
            _write_json(pointer, {"schemaVersion": 1, "generation": generation})
            os.replace(pointer, root / "current.json")
            _sync_directory(root)
        finally:
            pointer.unlink(missing_ok=True)
    return ImageState(generation, state.sequence, state.tables, state.provenance)


def load_state(root: Path) -> ImageState:
    """Replay committed checkpoints after the base watermark; ignore private temps."""
    pointer = _read_json(root / "current.json")
    generation = pointer.get("generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise ValueError("Invalid image state generation")
    directory = root / "base" / generation
    if directory.is_symlink():
        raise ValueError("Invalid image state base path")
    manifest = _read_json(directory / "manifest.json")
    sequence = manifest.get("sequence")
    if manifest.get("generation") != generation or type(sequence) is not int or sequence < 0:
        raise ValueError("Invalid image state base")
    tables = _read_tables(directory, manifest)
    if set(tables) != set(_TABLE_KEYS) or not isinstance(manifest.get("provenance"), dict):
        raise ValueError("Incomplete image state base")
    state = ImageState(generation, sequence, tables, manifest["provenance"])
    _validate_state(state)
    checkpoints = root / "checkpoints"
    if checkpoints.exists():
        for path in sorted(checkpoints.iterdir()):
            if path.name.startswith("."):
                continue
            if not re.fullmatch(r"[0-9]{12}", path.name) or path.is_symlink():
                raise ValueError("Invalid checkpoint path")
            if int(path.name) <= state.sequence:
                continue
            batch = _read_json(path / "manifest.json")
            if batch.get("sequence") != state.sequence + 1 or batch["sequence"] != int(path.name):
                raise ValueError("Image state checkpoint sequence gap")
            replacements = _read_tables(path, batch)
            state = _apply(state, replacements, batch.get("covered"), batch["sequence"])
    return state


def _apply(state: ImageState, replacements: dict[str, pl.DataFrame], covered: object, sequence: int) -> ImageState:
    if not isinstance(covered, dict) or set(covered) != set(replacements):
        raise ValueError("Invalid checkpoint coverage")
    tables = dict(state.tables)
    for name, frame in replacements.items():
        key = _TABLE_KEYS[name]
        ids = covered[name]
        if not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids):
            raise ValueError("Invalid checkpoint identities")
        if len(set(ids)) != len(ids) or frame.schema != tables[name].schema:
            raise ValueError("Invalid checkpoint schema or duplicate identities")
        if frame[key].null_count() or not set(frame[key]) <= set(ids):
            raise ValueError("Checkpoint rows outside coverage")
        if name != "claims" and (frame.height != len(ids) or frame[key].n_unique() != frame.height):
            raise ValueError("Incomplete checkpoint entity results")
        tables[name] = pl.concat([tables[name].filter(~pl.col(key).is_in(ids)), frame])
    updated = ImageState(state.generation, sequence, tables, state.provenance)
    _validate_state(updated)
    return updated


def checkpoint_state(root: Path, state: ImageState, replacements: dict[str, pl.DataFrame], covered: dict) -> ImageState:
    """Durably replace only explicitly covered keys, including zero-row claims."""
    sequence = state.sequence + 1
    updated = _apply(state, replacements, covered, sequence)
    checkpoints = root / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    target = checkpoints / f"{sequence:012d}"
    if target.exists():
        raise FileExistsError("Checkpoint already committed; reload state before retrying")
    with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=checkpoints) as temporary:
        candidate = Path(temporary) / "batch"
        _write_tables(candidate, replacements, {"sequence": sequence, "covered": covered})
        os.rename(candidate, target)
        _sync_directory(checkpoints)
    return updated
