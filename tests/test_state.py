from datetime import datetime, timezone

from sakuramedia_subtitlecat.state import SubtitleCatFetchState


def test_fetch_state_persists_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "data" / "fetch_state.sqlite3"
    first = SubtitleCatFetchState(database_path)
    assert first.has_fetched("SSNI-888") is False

    first.mark_fetched("SSNI-888", datetime(2026, 8, 23, tzinfo=timezone.utc))

    second = SubtitleCatFetchState(database_path)
    assert second.has_fetched("SSNI-888") is True
