from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import sakuramedia_subtitlecat.jobs as jobs_module
from sakuramedia_subtitlecat.jobs import FetchSubtitleParams, build_jobs
from sakuramedia_subtitlecat.settings import SubtitleCatSettings
from sakuramedia_subtitlecat.state import SubtitleCatFetchState

from src.plugins.types import SubtitleImportStatus


class _Reporter:
    def __init__(self) -> None:
        self.events = []

    def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


class _Logger:
    def info(self, *args, **kwargs) -> None:
        del args, kwargs

    def exception(self, *args, **kwargs) -> None:
        del args, kwargs


class _Movies:
    def __init__(self, found: bool) -> None:
        self.found = found

    def find_by_numbers(self, numbers):
        del numbers
        return (
            [SimpleNamespace(values={"movie_number": "SSNI-888"})] if self.found else []
        )


class _Context:
    def __init__(self, data_dir: Path, found: bool = True) -> None:
        self.movies = _Movies(found)
        self.data_dir = data_dir
        self.imports = []

    def get_task_logger(self, name):
        assert name == "subtitlecat-fetch"
        return _Logger()

    def import_subtitle(self, movie_number, content, filename, language=None):
        self.imports.append((movie_number, content, filename, language))
        status = (
            SubtitleImportStatus.IMPORTED
            if len(self.imports) == 1
            else SubtitleImportStatus.DUPLICATE
        )
        return SimpleNamespace(status=status)


class _Client:
    def __init__(self, settings) -> None:
        assert settings.request_retries == 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def fetch_chinese_subtitles(self, movie_number):
        assert movie_number == "SSNI-888"
        return [b"one --> two", b"three --> four"]


def test_params_normalize_number() -> None:
    assert FetchSubtitleParams(movie_number=" ssni 888 ").movie_number == "SSNI-888"


def test_manual_job_imports_only_subtitlecat_results(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(jobs_module, "SubtitleCatClient", _Client)
    context = _Context(data_dir=tmp_path)
    job = build_jobs(
        context,
        settings=SubtitleCatSettings(request_retries=0),
    )[0]

    assert job.manual_only is True
    assert job.params_schema is FetchSubtitleParams
    reporter = _Reporter()
    stats = job.handler(reporter, {"movie_number": "ssni-888"})

    assert stats == {
        "source_matches": 2,
        "imported": 1,
        "duplicate": 1,
        "movie_not_found": 0,
        "invalid_format": 0,
        "failed": 0,
    }
    assert [item[3] for item in context.imports] == ["zh-CN", "zh-CN"]
    assert reporter.events[-1]["current"] == 2


def test_manual_job_skips_network_when_movie_does_not_exist(
    monkeypatch, tmp_path
) -> None:
    class _UnexpectedClient:
        def __init__(self, settings) -> None:
            raise AssertionError("不存在的影片不应访问 SubtitleCat")

    monkeypatch.setattr(jobs_module, "SubtitleCatClient", _UnexpectedClient)
    context = _Context(data_dir=tmp_path, found=False)
    job = build_jobs(context, settings=SubtitleCatSettings())[0]

    stats = job.handler(_Reporter(), {"movie_number": "SSNI-888"})

    assert stats["movie_not_found"] == 1
    assert context.imports == []


def _movie_snapshot(movie_number: str, release_date: str, *, subscribed: bool = True):
    return SimpleNamespace(
        values={
            "movie_number": movie_number,
            "release_date": release_date,
            "is_subscribed": subscribed,
        }
    )


class _ScheduledMovies:
    def __init__(self, snapshots) -> None:
        self.snapshots = snapshots

    def list_page(self, *, after_id: int, limit: int):
        assert after_id == 0
        assert limit == 500
        return SimpleNamespace(items=tuple(self.snapshots), next_cursor=None)


class _ScheduledContext:
    def __init__(self, data_dir: Path, snapshots) -> None:
        self.data_dir = data_dir
        self.movies = _ScheduledMovies(snapshots)
        self.imports = []

    def get_task_logger(self, name):
        assert name == "subtitlecat-subscribed-fetch"
        return _Logger()

    def import_subtitle(self, movie_number, content, filename, language=None):
        self.imports.append((movie_number, content, filename, language))
        return SimpleNamespace(status=SubtitleImportStatus.DUPLICATE)


class _EmptyClient:
    calls = []

    def __init__(self, settings) -> None:
        assert settings.release_age_months == 3

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def fetch_chinese_subtitles(self, movie_number):
        self.calls.append(movie_number)
        return []


def test_scheduled_job_skips_only_fetched_old_subscriptions(
    monkeypatch, tmp_path
) -> None:
    fixed_now = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(jobs_module, "_utc_now", lambda: fixed_now)
    monkeypatch.setattr(jobs_module, "SubtitleCatClient", _EmptyClient)
    _EmptyClient.calls = []

    snapshots = [
        _movie_snapshot("OLD-001", "2026-01-01"),
        _movie_snapshot("NEW-001", "2026-08-01"),
        _movie_snapshot("NEVER-001", "2025-01-01"),
        _movie_snapshot("UNSUB-001", "2025-01-01", subscribed=False),
    ]
    context = _ScheduledContext(tmp_path, snapshots)
    state = SubtitleCatFetchState(tmp_path / "fetch_state.sqlite3")
    state.mark_fetched("OLD-001", fixed_now)
    state.mark_fetched("NEW-001", fixed_now)

    jobs = build_jobs(
        context,
        settings=SubtitleCatSettings(request_retries=0, release_age_months=3),
    )
    scheduled_job = next(
        job
        for job in jobs
        if job.task_key == "sakuramedia_subtitlecat_fetch_subscribed"
    )
    stats = scheduled_job.handler(_Reporter(), {})

    assert _EmptyClient.calls == ["NEW-001", "NEVER-001"]
    assert stats == {
        "subscribed": 3,
        "eligible": 2,
        "fetched": 2,
        "skipped_old": 1,
        "source_matches": 0,
        "imported": 0,
        "duplicate": 0,
        "movie_not_found": 0,
        "invalid_format": 0,
        "failed": 0,
    }
    assert state.has_fetched("NEVER-001") is True
