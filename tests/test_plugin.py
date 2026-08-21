from pathlib import Path

from sakuramedia_subtitlecat.plugin import PLUGIN_ID, register

from src.plugins import HOST_API_VERSION, PluginContext


def test_register_uses_current_host_api_and_manual_job() -> None:
    registration = register(
        PluginContext(
            plugin_id=PLUGIN_ID,
            settings={},
            data_dir=Path("/tmp/sakuramedia-subtitlecat-test"),
        )
    )

    assert registration.plugin_id == PLUGIN_ID
    assert registration.host_api_version == HOST_API_VERSION
    assert [job.task_key for job in registration.jobs] == [
        "sakuramedia_subtitlecat_fetch",
        "sakuramedia_subtitlecat_fetch_subscribed",
    ]
    assert registration.jobs[1].default_cron == "0 3 * * *"
