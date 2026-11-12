"""Stable public asset ids and installation helpers for isolated tests.

The test suite must not depend on a developer's ignored ``userdata/`` tree.
These ids intentionally have no relationship with any deployment role names.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path


TEST_CHAR_ID = "fixture_character"
TEST_PEER_CHAR_ID = "fixture_peer"
TEST_THIRD_CHAR_ID = "fixture_third"
TEST_CHAR_IDS = (TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID)
TEST_CHAR_NAME = "Fixture Companion"

_FIXTURE_ROOT = Path(__file__).parent
PUBLIC_CHARACTER_CARDS = _FIXTURE_ROOT / "characters" / "cards"
PUBLIC_CHARACTER_ASSETS = _FIXTURE_ROOT / "characters" / "authored"
PUBLIC_DREAM_WORLDS = _FIXTURE_ROOT / "dream_worlds"


@lru_cache(maxsize=1)
def public_character_package():
    """Immutable bytes shared per worker; writable copies stay function scoped."""
    return tuple((char_id, (PUBLIC_CHARACTER_CARDS / f"{char_id}.json").read_bytes(),
                  tuple((asset.name, asset.read_bytes())
                        for asset in sorted((PUBLIC_CHARACTER_ASSETS / char_id).glob("*"))
                        if asset.is_file())) for char_id in TEST_CHAR_IDS)


def install_public_character_cards(paths) -> tuple[str, ...]:
    """Install tracked public role cards into the active test sandbox."""
    for char_id, card, assets in public_character_package():
        target = paths.character_card_write_path(char_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(card)
        if assets:
            target_assets = paths.user_authored_character_dir(char_id=char_id)
            target_assets.mkdir(parents=True, exist_ok=True)
            for name, content in assets:
                (target_assets / name).write_bytes(content)
    return TEST_CHAR_IDS


def install_public_dream_worlds(target: Path) -> Path:
    """Copy the tracked public dream-world package into a test sandbox."""
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PUBLIC_DREAM_WORLDS, target)
    return target
