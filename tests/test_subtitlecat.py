import httpx
import pytest
from sakuramedia_subtitlecat.settings import SubtitleCatSettings
from sakuramedia_subtitlecat.subtitlecat import (
    SubtitleCatClient,
    SubtitleCatError,
)

SEARCH_HTML = """
<div class="other"><a href="/subtitles/SSNI-999">wrong</a></div>
<div class="subtitles">
  <table><tbody>
    <tr><td><a href="/subtitles/SSNI-888-001">match</a></td></tr>
    <tr><td><a href="/subtitles/ABP-123">other</a></td></tr>
  </tbody></table>
</div>
"""

DETAIL_HTML = """
<div class="sub-single">
  <span><a id="download_en" href="/download/en.srt">English</a></span>
  <span><a id="download_zh-CN" href="/download/zh.srt">中文</a></span>
</div>
"""

SRT_CONTENT = "1\n00:00:01,000 --> 00:00:02,000\n你好\n"


def test_fetch_chinese_subtitles_filters_movie_and_language() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/index.php":
            assert request.url.params["search"] == "SSNI-888"
            return httpx.Response(200, text=SEARCH_HTML)
        if request.url.path == "/subtitles/SSNI-888-001":
            return httpx.Response(200, text=DETAIL_HTML)
        if request.url.path == "/download/zh.srt":
            return httpx.Response(200, text=SRT_CONTENT)
        return httpx.Response(404)

    with SubtitleCatClient(
        SubtitleCatSettings(request_retries=0),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.fetch_chinese_subtitles("ssni 888")

    assert result == [SRT_CONTENT.encode("utf-8")]


def test_fetch_rejects_html_instead_of_importing_it_as_subtitle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/index.php":
            return httpx.Response(
                200,
                text='<div class="subtitles"><a href="/subtitles/SSNI-888"></a></div>',
            )
        if request.url.path == "/subtitles/SSNI-888":
            return httpx.Response(
                200,
                text='<a id="download_zh-CN" href="/download/zh.srt"></a>',
            )
        return httpx.Response(200, text="<html>blocked</html>")

    with SubtitleCatClient(
        SubtitleCatSettings(request_retries=0),
        transport=httpx.MockTransport(handler),
    ) as client, pytest.raises(SubtitleCatError, match="不是有效的 SRT"):
        client.fetch_chinese_subtitles("SSNI-888")
