import re

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify_genre_name(name: str) -> str:
    slug = _SLUG_NON_ALNUM.sub("-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"genre name {name!r} slugifies to an empty id")
    return slug
