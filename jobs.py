"""SubtitleCat 手动与订阅影片定时任务。"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.plugins import PluginContext
from src.plugins.types import SubtitleImportStatus
from src.scheduler.contracts import JobDefinition

from .settings import SubtitleCatSettings
from .state import SubtitleCatFetchState
from .subtitlecat import SubtitleCatClient, SubtitleCatError, normalize_movie_number

_MANUAL_STAT_KEYS = (
    "source_matches",
    "imported",
    "duplicate",
    "movie_not_found",
    "invalid_format",
    "failed",
)

_SUBSCRIBED_STAT_KEYS = (
    "subscribed",
    "eligible",
    "fetched",
    "skipped_old",
    "source_matches",
    "imported",
    "duplicate",
    "movie_not_found",
    "invalid_format",
    "failed",
)


class FetchSubtitleParams(BaseModel):
    """手动抓取的唯一参数：宿主已有影片番号。"""

    movie_number: str = Field(min_length=1, max_length=64)

    @field_validator("movie_number")
    @classmethod
    def _normalize_number(cls, value: str) -> str:
        normalized = normalize_movie_number(value)
        if not normalized:
            raise ValueError("movie_number 不能为空")
        return normalized


def _new_stats(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_values(snapshot: Any) -> Mapping[str, Any]:
    values = getattr(snapshot, "values", None)
    return values if isinstance(values, Mapping) else {}


def _snapshot_movie_number(snapshot: Any, fallback: str) -> str:
    return str(_snapshot_values(snapshot).get("movie_number") or fallback)


def _parse_release_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            # 快照通常给 datetime；只读契约允许插件面对 date/字符串，取日期部分即可。
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _subtract_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _is_old_release(value: Any, *, now: datetime, months: int) -> bool:
    release_date = _parse_release_date(value)
    if release_date is None:
        # 没有发布时间时无法证明它是老片，保守地继续抓取。
        return False
    cutoff = _subtract_calendar_months(now.date(), months)
    return release_date <= cutoff


def _state_for(context: PluginContext) -> SubtitleCatFetchState:
    return SubtitleCatFetchState(context.data_dir / "fetch_state.sqlite3")


def _record_import_result(stats: dict[str, int], status: SubtitleImportStatus) -> None:
    if status == SubtitleImportStatus.IMPORTED:
        stats["imported"] += 1
    elif status == SubtitleImportStatus.DUPLICATE:
        stats["duplicate"] += 1
    elif status == SubtitleImportStatus.MOVIE_NOT_FOUND:
        stats["movie_not_found"] += 1
    elif status == SubtitleImportStatus.INVALID_FORMAT:
        stats["invalid_format"] += 1
    else:
        stats["failed"] += 1


def _import_subtitles(
    context: PluginContext,
    movie_number: str,
    subtitles: list[bytes],
    reporter,
    stats: dict[str, int],
    *,
    progress_current: int | None,
    progress_total: int,
    progress_prefix: str = "处理字幕",
) -> None:
    for index, content in enumerate(subtitles, start=1):
        result = context.import_subtitle(
            movie_number,
            content,
            f"{movie_number}-{index}.srt",
            language="zh-CN",
        )
        _record_import_result(stats, result.status)
        reporter.emit(
            current=index if progress_current is None else progress_current,
            total=progress_total,
            text=f"{progress_prefix} {index}/{len(subtitles)}",
            summary_patch=stats,
        )


def _iter_subscribed_movies(context: PluginContext):
    """按 Host API 3 的 id 游标遍历并筛出已订阅影片。"""

    after_id = 0
    while True:
        page = context.movies.list_page(after_id=after_id, limit=500)
        for snapshot in page.items:
            if _snapshot_values(snapshot).get("is_subscribed") is True:
                yield snapshot
        if page.next_cursor is None:
            return
        if page.next_cursor <= after_id:
            raise RuntimeError("宿主影片分页游标没有向前推进")
        after_id = page.next_cursor


def build_jobs(
    context: PluginContext,
    *,
    settings: SubtitleCatSettings,
) -> tuple[JobDefinition, ...]:
    """声明单部手动抓取与订阅影片定时抓取任务。"""

    def run_fetch(reporter, params: dict[str, Any]) -> dict[str, int]:
        parsed_params = FetchSubtitleParams.model_validate(params)
        movie_number = parsed_params.movie_number
        logger = context.get_task_logger("subtitlecat-fetch")
        stats = _new_stats(_MANUAL_STAT_KEYS)

        # 先查宿主影片，避免对不存在的番号发起外部请求。
        snapshots = context.movies.find_by_numbers([movie_number])
        if not snapshots:
            stats["movie_not_found"] = 1
            reporter.emit(
                current=0,
                total=0,
                text=f"影片不存在: {movie_number}",
                summary_patch=stats,
            )
            return stats

        canonical_movie_number = _snapshot_movie_number(snapshots[0], movie_number)
        logger.info("开始抓取中文字幕 movie_number={}", movie_number)
        try:
            with SubtitleCatClient(settings) as client:
                subtitles = client.fetch_chinese_subtitles(movie_number)
        except SubtitleCatError:
            logger.exception("SubtitleCat 抓取失败 movie_number={}", movie_number)
            raise

        stats["source_matches"] = len(subtitles)
        reporter.emit(
            current=0,
            total=len(subtitles),
            text=f"找到 {len(subtitles)} 份中文字幕",
            summary_patch=stats,
        )
        _import_subtitles(
            context,
            canonical_movie_number,
            subtitles,
            reporter,
            stats,
            progress_current=None,
            progress_total=len(subtitles),
        )
        _state_for(context).mark_fetched(canonical_movie_number, _utc_now())
        return stats

    def run_subscribed(reporter, _params: dict[str, Any]) -> dict[str, int]:
        logger = context.get_task_logger("subtitlecat-subscribed-fetch")
        stats = _new_stats(_SUBSCRIBED_STAT_KEYS)
        snapshots = list(_iter_subscribed_movies(context))
        total = len(snapshots)
        stats["subscribed"] = total
        reporter.emit(
            current=0,
            total=total,
            text=f"发现 {total} 部已订阅影片",
            summary_patch=stats,
        )
        if not snapshots:
            return stats

        state = _state_for(context)
        now = _utc_now()
        with SubtitleCatClient(settings) as client:
            for index, snapshot in enumerate(snapshots, start=1):
                values = _snapshot_values(snapshot)
                movie_number = _snapshot_movie_number(snapshot, "")
                if not movie_number:
                    stats["failed"] += 1
                    reporter.emit(
                        current=index,
                        total=total,
                        text="影片快照缺少番号",
                        summary_patch=stats,
                    )
                    continue

                if state.has_fetched(movie_number) and _is_old_release(
                    values.get("release_date"),
                    now=now,
                    months=settings.release_age_months,
                ):
                    stats["skipped_old"] += 1
                    reporter.emit(
                        current=index,
                        total=total,
                        text=f"跳过老片: {movie_number}",
                        summary_patch=stats,
                    )
                    continue

                stats["eligible"] += 1
                logger.info("开始抓取订阅影片中文字幕 movie_number={}", movie_number)
                try:
                    subtitles = client.fetch_chinese_subtitles(movie_number)
                except SubtitleCatError:
                    stats["failed"] += 1
                    logger.exception(
                        "SubtitleCat 抓取失败 movie_number={}", movie_number
                    )
                    reporter.emit(
                        current=index,
                        total=total,
                        text=f"抓取失败: {movie_number}",
                        summary_patch=stats,
                    )
                    continue

                stats["fetched"] += 1
                stats["source_matches"] += len(subtitles)
                reporter.emit(
                    current=index,
                    total=total,
                    text=f"{movie_number}: 找到 {len(subtitles)} 份中文字幕",
                    summary_patch=stats,
                )
                _import_subtitles(
                    context,
                    movie_number,
                    subtitles,
                    reporter,
                    stats,
                    progress_current=index,
                    progress_total=total,
                    progress_prefix=f"处理 {movie_number} 字幕",
                )
                # 只有外部抓取和字幕导入流程都正常返回，才把本片记为已抓取。
                # 空结果也算一次成功抓取，避免老片无字幕时每天重复访问来源。
                state.mark_fetched(movie_number, _utc_now())

        return stats

    return (
        JobDefinition(
            task_key="sakuramedia_subtitlecat_fetch",
            log_name="subtitlecat-fetch",
            cli_name="fetch-subtitlecat",
            cli_help="手动抓取单部影片的中文字幕",
            manual_only=True,
            params_schema=FetchSubtitleParams,
            handler=run_fetch,
        ),
        JobDefinition(
            task_key="sakuramedia_subtitlecat_fetch_subscribed",
            log_name="subtitlecat-subscribed-fetch",
            cli_name="fetch-subscribed-subtitlecat",
            cli_help="定时抓取所有已订阅影片的中文字幕",
            default_cron="0 3 * * *",
            handler=run_subscribed,
        ),
    )
