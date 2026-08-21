"""SubtitleCat 搜索、中文下载与字幕内容校验。"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from typing_extensions import Self

from .settings import SubtitleCatSettings

SUBTITLECAT_BASE_URL = "https://subtitlecat.com/"
CHINESE_DOWNLOAD_ANCHOR_ID = "download_zh-CN"
USER_AGENT = "SakuraMedia-SubtitleCat/0.1"

_MOVIE_NUMBER_PATTERN = re.compile(
    r"(?<![A-Z0-9])((?:FC2(?:[-_ ]?PPV)?|[A-Z]{2,6})[-_ ]?\d{2,6})(?!\d)",
    re.IGNORECASE,
)


class SubtitleCatError(RuntimeError):
    """SubtitleCat 请求或响应内容不可用。"""


class _LinkParser(HTMLParser):
    """只提取指定 HTML 范围内的链接，避免依赖 lxml。"""

    def __init__(
        self,
        *,
        ancestor_class: str | None = None,
        anchor_id: str | None = None,
    ) -> None:
        super().__init__()
        self._ancestor_class = ancestor_class
        self._anchor_id = anchor_id
        self._scope_depth = 0
        self._open_tags: list[tuple[str, bool]] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        attributes = dict(attrs)
        class_names = (attributes.get("class") or "").split()
        is_scope = (
            self._ancestor_class is not None
            and self._ancestor_class in class_names
        )
        self._open_tags.append((normalized_tag, is_scope))
        if is_scope:
            self._scope_depth += 1

        in_scope = self._ancestor_class is None or self._scope_depth > 0
        if normalized_tag != "a" or not in_scope:
            return
        if self._anchor_id is not None and attributes.get("id") != self._anchor_id:
            return
        href = attributes.get("href")
        if href:
            self.links.append(href)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index][0] != normalized_tag:
                continue
            removed = self._open_tags[index:]
            del self._open_tags[index:]
            self._scope_depth -= sum(1 for _, is_scope in removed if is_scope)
            return


def _collect_links(
    html: str,
    *,
    ancestor_class: str | None = None,
    anchor_id: str | None = None,
) -> list[str]:
    parser = _LinkParser(
        ancestor_class=ancestor_class,
        anchor_id=anchor_id,
    )
    parser.feed(html)
    parser.close()
    return list(dict.fromkeys(parser.links))


def normalize_movie_number(value: str) -> str:
    """统一人工输入和链接中的番号分隔符。"""

    normalized = re.sub(r"[-_\s]+", "-", value.strip().upper())
    if normalized.startswith("FC2PPV"):
        suffix = normalized.removeprefix("FC2PPV").lstrip("-")
        normalized = f"FC2-PPV-{suffix}" if suffix else "FC2-PPV"
    return normalized


def _numbers_equal(left: str, right: str) -> bool:
    return left == right or left.replace("-", "") == right.replace("-", "")


def _extract_movie_numbers(value: str) -> list[str]:
    return [normalize_movie_number(match.group(1)) for match in _MOVIE_NUMBER_PATTERN.finditer(value)]


def _subtitle_bytes(response: httpx.Response) -> bytes:
    """将响应转为 UTF-8，并拒绝明显不是字幕的页面。"""

    text = response.text.lstrip("\ufeff")
    if not text.strip() or "-->" not in text:
        raise SubtitleCatError("SubtitleCat 返回内容不是有效的 SRT 字幕")
    return text.encode("utf-8")


class SubtitleCatClient:
    """SubtitleCat 单部影片客户端。"""

    def __init__(
        self,
        settings: SubtitleCatSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or SubtitleCatSettings()
        client_kwargs = {
            "headers": {"User-Agent": USER_AGENT},
            "timeout": self.settings.request_timeout_seconds,
            "follow_redirects": True,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.Client(**client_kwargs)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        attempts = self.settings.request_retries + 1
        for attempt in range(attempts):
            try:
                response = self._client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                response = exc.response
                if response is not None and response.status_code < 500:
                    raise SubtitleCatError(
                        f"SubtitleCat 请求失败: HTTP {response.status_code}"
                    ) from exc
                last_error = exc
            except httpx.RequestError as exc:
                last_error = exc

            if attempt < attempts - 1:
                time.sleep(0.5 * (2**attempt))

        raise SubtitleCatError(f"SubtitleCat 请求失败: {url}") from last_error

    def fetch_chinese_subtitles(self, movie_number: str) -> list[bytes]:
        """抓取一部影片所有搜索结果中的简体中文字幕。"""

        number = normalize_movie_number(movie_number)
        search_response = self._get(
            urljoin(SUBTITLECAT_BASE_URL, "index.php"),
            params={"search": number},
        )
        detail_links = _collect_links(
            search_response.text,
            ancestor_class="subtitles",
        )
        matching_detail_links = [
            urljoin(str(search_response.url), link)
            for link in detail_links
            if any(
                _numbers_equal(candidate, number)
                for candidate in _extract_movie_numbers(link)
            )
        ]

        subtitles: list[bytes] = []
        seen_download_links: set[str] = set()
        for detail_link in matching_detail_links:
            detail_response = self._get(detail_link)
            download_links = _collect_links(
                detail_response.text,
                anchor_id=CHINESE_DOWNLOAD_ANCHOR_ID,
            )
            for download_link in download_links:
                absolute_download_link = urljoin(str(detail_response.url), download_link)
                if absolute_download_link in seen_download_links:
                    continue
                seen_download_links.add(absolute_download_link)
                subtitles.append(_subtitle_bytes(self._get(absolute_download_link)))
        return subtitles
