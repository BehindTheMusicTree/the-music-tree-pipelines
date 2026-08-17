def test_package_importable() -> None:
    import importlib

    importlib.import_module("musicbrainz")
