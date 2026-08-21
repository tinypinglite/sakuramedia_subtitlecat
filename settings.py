"""SubtitleCat 插件配置。"""

from pydantic import BaseModel, ConfigDict, Field


class SubtitleCatSettings(BaseModel):
    """SubtitleCat 请求与定时补抓配置。"""

    model_config = ConfigDict(extra="forbid")

    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    request_retries: int = Field(default=2, ge=0, le=3)
    # 已经抓过的影片，发布时间达到这个月数后不再由定时任务重复抓取。
    release_age_months: int = Field(default=3, ge=1, le=120)
