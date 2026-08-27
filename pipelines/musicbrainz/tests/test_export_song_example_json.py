import importlib.util
import json
from pathlib import Path

import polars as pl

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "export_song_example_json.py"
_spec = importlib.util.spec_from_file_location("export_song_example_json", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
export_song_example_json_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_song_example_json_module)

SONG_ROWS = [
    {"title": "Song A", "artist": "Artist X", "youtube_video_id": "aaaaaaaaaaa", "genre_name": "rock"},
    {"title": "Song B", "artist": "Artist Y", "youtube_video_id": "bbbbbbbbbbb", "genre_name": "jazz"},
]


def test_export_song_example_json_writes_flat_song_list(tmp_path: Path) -> None:
    silver_dir = tmp_path / "silver"
    silver_dir.mkdir()
    pl.DataFrame(SONG_ROWS).write_parquet(silver_dir / "3_song_example.parquet")
    output_path = tmp_path / "song_example.json"

    result = export_song_example_json_module.export_song_example_json(silver_dir, output_path)

    assert result == output_path
    assert json.loads(output_path.read_text()) == SONG_ROWS


def test_export_song_example_json_creates_output_dir(tmp_path: Path) -> None:
    silver_dir = tmp_path / "silver"
    silver_dir.mkdir()
    pl.DataFrame(SONG_ROWS).write_parquet(silver_dir / "3_song_example.parquet")
    output_path = tmp_path / "does" / "not" / "exist" / "song_example.json"

    export_song_example_json_module.export_song_example_json(silver_dir, output_path)

    assert output_path.is_file()
