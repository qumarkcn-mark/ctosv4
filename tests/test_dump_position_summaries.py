from pathlib import Path

import pytest

from server.scripts.dump_position_summaries import resolve_db_path


def test_resolve_db_path_uses_explicit_existing_file(tmp_path):
    db_path = tmp_path / "ctos.db"
    db_path.write_text("", encoding="utf-8")

    assert resolve_db_path(str(db_path)) == db_path.resolve()


def test_resolve_db_path_raises_when_missing(tmp_path):
    missing = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        resolve_db_path(str(missing))


def test_resolve_db_path_uses_first_existing_candidate(tmp_path):
    first = tmp_path / "missing.db"
    second = tmp_path / "ctos.db"
    second.write_text("", encoding="utf-8")

    assert resolve_db_path(candidates=[first, second]) == second
