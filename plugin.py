"""SakuraMedia SubtitleCat 插件入口。"""

from __future__ import annotations

from src.plugins import HOST_API_VERSION, PluginContext, PluginRegistration

from .jobs import build_jobs
from .settings import SubtitleCatSettings

__version__ = "0.2.0"
PLUGIN_ID = "sakuramedia_subtitlecat"
DISPLAY_NAME = "SakuraMedia SubtitleCat 中文字幕"


def register(context: PluginContext) -> PluginRegistration:
    """声明手动与订阅影片定时任务；加载阶段不访问外部网站。"""

    settings = SubtitleCatSettings.model_validate(context.settings)
    return PluginRegistration(
        plugin_id=PLUGIN_ID,
        display_name=DISPLAY_NAME,
        version=__version__,
        host_api_version=HOST_API_VERSION,
        jobs=build_jobs(context, settings=settings),
    )
