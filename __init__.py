"""SakuraMedia SubtitleCat 字幕插件。"""

from .plugin import register
from .settings import SubtitleCatSettings

__all__ = ["SubtitleCatSettings", "register"]
