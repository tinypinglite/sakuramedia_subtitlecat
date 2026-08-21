# SakuraMedia SubtitleCat 中文字幕插件

这个插件通过 SubtitleCat 为 SakuraMedia 的已有影片抓取中文字幕，使用宿主的字幕导入能力完成去重、落盘和登记。

当前版本只支持：

- 手动抓取单部影片；
- 每天定时处理所有已订阅影片；
- 语言固定为 `zh-CN`，匹配 SubtitleCat 的 `download_zh-CN` 下载入口；
- 影片必须已经存在于 SakuraMedia 媒体库；
- 抓取状态保存在插件 `data/fetch_state.sqlite3`，插件升级时由宿主保留；
- 已抓取过且发布时间达到配置月数的老片会跳过，发布时间较新的影片会继续周期性抓取。

## 安装

将插件根目录直接打成 zip，根目录需要包含 `manifest.json`，然后从 SakuraMedia 的插件管理页安装并启用。安装或更新后重启 API 和 APS 服务。

## 使用

任务名：`sakuramedia_subtitlecat_fetch`

CLI 示例：

```bash
uv run python -m src.start.commands aps fetch-subtitlecat \
  --params-json '{"movie_number":"SSNI-888"}'
```

也可以在任务中心通过同一个任务 key 手动触发，参数为：

```json
{
  "movie_number": "SSNI-888"
}
```

定时任务：`sakuramedia_subtitlecat_fetch_subscribed`，默认每天 03:00 执行。
也可以手动执行无参数 CLI：

```bash
uv run python -m src.start.commands aps fetch-subscribed-subtitlecat
```

## 配置

在前端「系统设置 → 插件」中点击本插件，按 JSON 编辑并保存：

```json
{
  "request_timeout_seconds": 20,
  "request_retries": 2,
  "release_age_months": 3
}
```

保存后需重启 api 与 aps（或整个容器）才会生效。

`release_age_months` 控制定时任务的停止窗口：影片已经成功抓取过，且发布时间早于或等于当前时间往前推 N 个日历月时跳过；未抓取过、没有发布时间或仍在窗口内的影片继续处理。

定时任务默认每天 03:00 执行；如需覆盖执行时间，使用宿主插件任务的 cron 配置机制。
