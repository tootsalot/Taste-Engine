"""Named cross-media reasons from recorded item identities and direct links only."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict

from taste import activity, themes

SOURCE_IDS = {**themes.IDS, "artist": ("lastfm", "lastfm_artist_key")}


def source_items(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Recorded identities only; source names and titles never establish identity."""
    kinds = {identity: kind for kind, identity in SOURCE_IDS.items()}
    return {
        (kinds[source, id_type], key): item
        for item, source, id_type, key in conn.execute(
            "SELECT item_id, source, id_type, external_id FROM core_item_external_ids"
        )
        if (source, id_type) in kinds
    }


def dismissed_keys(conn: sqlite3.Connection, identities: dict) -> set[tuple[str, str]]:
    """A mixed dismissal covers the same recorded item, never related adaptations."""
    dismissed = {tuple(row) for row in conn.execute("SELECT kind, item_key FROM rec_dismissed")}
    items = {identities[key] for key in dismissed if key in identities}
    return dismissed | {key for key, item in identities.items() if item in items}


def load(conn: sqlite3.Connection, identities: dict | None = None) -> dict[tuple[str, str], str]:
    """Build the mixed list's title reasons once, without matching names or people.

    A source ID must map to a core item. A rated item in another medium must be
    that same recorded item or a direct neighbour in core_item_links. Genres,
    shared people, MAL's source_material, and paths through other items add no link.
    Hidden adult items keep their ratings but supply neither a title nor an entry.
    """
    hidden = activity.hidden_items(conn)
    identities = source_items(conn) if identities is None else identities
    rated_kinds: dict[tuple[int, str], set[str]] = defaultdict(set)
    for (kind, _), item in identities.items():
        if kind != "artist":
            rated_kinds[item, SOURCE_IDS[kind][0]].add(kind)

    ratings: dict[int, list[tuple[str, str, float, float]]] = defaultdict(list)
    for item, source, media, title, score, scale in conn.execute(
        "SELECT r.item_id, r.source, i.media_type, i.title, r.raw_score, r.scale_max "
        "FROM core_ratings r JOIN core_items i ON i.item_id = r.item_id"
    ):
        if item in hidden:
            continue
        options = rated_kinds[item, source]
        kind = next(iter(options)) if len(options) == 1 else themes.MEDIA.get(media)
        if kind not in options:
            continue
        maximum = 5 if kind == "book" else 10
        minimum = 1 if kind == "anime" else 0.5
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not minimum <= score <= maximum
            or scale != maximum
        ):
            continue
        ratings[item].append((kind, title, score, maximum))

    neighbours: dict[int, set[int]] = defaultdict(set)
    for parent, child in conn.execute("SELECT parent_item_id, child_item_id FROM core_item_links"):
        neighbours[parent].add(child)
        neighbours[child].add(parent)

    result = {}
    for key, item in identities.items():
        if item in hidden:
            continue
        kind, _ = key
        choices = [
            (other, title, score, maximum, target)
            for target in neighbours[item] | {item}
            for other, title, score, maximum in ratings[target]
            if other != kind
        ]
        if not choices:
            continue
        other, title, score, maximum, _ = min(
            choices, key=lambda value: (-value[2] / value[3], value[0], value[1], value[4])
        )
        unit = "/5 stars" if other == "book" else "/10"
        result[key] = (
            f'Linked to "{title}", which you rated {score:g}{unit} in {themes.LABELS[other]}.'
        )
    return result
