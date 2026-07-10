def test_package_importable() -> None:
    import importlib

    importlib.import_module("musicbrainz_to_the_music_tree_api")
