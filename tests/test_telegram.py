from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from factslens.common.models import MediaKind, Platform
from factslens.ingest.telegram import (
    TelegramSource,
    message_event,
    parse_channel_page,
    pravda_source_channels,
)

# A real page: t.me/s/rybar_in_english, saved 14 Nov 2026
FIXTURE = Path(__file__).parent / "fixtures" / "telegram_channel.html"

PAGE = """
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="SomeChannel/101">
  <div class="tgme_widget_message_reply">
    <div class="tgme_widget_message_text">quoted older message</div></div>
  <a class="tgme_widget_message_photo_wrap"
     style="width:1px;background-image:url('https://cdn.example/p1.jpg')"></a>
  <div class="tgme_widget_message_text js-message_text" dir="auto">Breaking: <b>bridge</b> closed<br/>more at
    <a href="https://news.example/a">news</a> and
    <a href="https://t.me/SomeChannel/99">earlier</a> #Bridge #Alert</div>
  <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date">
    <time datetime="2026-11-14T07:28:20+00:00">07:28</time></a></div>
</div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="SomeChannel/102">
  <div class="tgme_widget_message_text">second</div><video src="https://cdn.example/v.mp4"></video>
  <time datetime="2026-11-14T07:30:00+00:00">07:30</time>
</div></div>
"""


def test_parses_text_links_media_time_and_skips_quoted_replies() -> None:
    first, second = parse_channel_page(PAGE)
    assert first.post == "SomeChannel/101" and first.channel == "somechannel" and first.number == 101
    assert first.text == "Breaking: bridge closed\nmore at news and earlier #Bridge #Alert"
    assert "quoted older message" not in first.text
    assert first.links == ["https://news.example/a", "https://t.me/SomeChannel/99"]
    assert first.photos == ["https://cdn.example/p1.jpg"]
    assert first.time == datetime(2026, 11, 14, 7, 28, 20, tzinfo=UTC)
    assert second.videos == ["https://cdn.example/v.mp4"]


def test_message_becomes_a_telegram_event() -> None:
    event = message_event(parse_channel_page(PAGE)[0])
    assert event.platform is Platform.TELEGRAM and event.author == "telegram:somechannel"
    assert event.id == "https://t.me/SomeChannel/101"
    assert event.links == ("https://news.example/a",)  # links back to the channel itself are dropped
    assert event.hashtags == ("bridge", "alert")
    assert [m.kind for m in event.media] == [MediaKind.IMAGE]


def test_real_page_fixture_parses() -> None:
    messages = parse_channel_page(FIXTURE.read_text(encoding="utf-8"))
    assert len(messages) >= 10
    assert all(m.channel == "rybar_in_english" and m.time is not None for m in messages)
    assert sum(bool(m.text) for m in messages) >= len(messages) - 3


def test_pravda_source_channels_takes_the_most_republished(tmp_path: Path) -> None:
    path = tmp_path / "telegram_channels.csv"
    path.write_text("channel,pravda_articles,pravda_domains\na,5,1\nb,50,3\nc,20,2\n", encoding="utf-8")
    assert pravda_source_channels(path, 2) == ["b", "c"]
    assert pravda_source_channels(tmp_path / "missing.csv", 2) == []


async def test_source_emits_new_recent_messages_once() -> None:
    now = datetime(2026, 11, 14, 7, 40, tzinfo=UTC)
    old = (now - timedelta(hours=5)).isoformat()
    page = (
        PAGE
        + f"""<div class="tgme_widget_message" data-post="SomeChannel/50">
      <div class="tgme_widget_message_text">very old</div><time datetime="{old}">x</time></div>"""
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page)))
    source = TelegramSource(["SomeChannel"], poll_seconds=0, client=client, clock=lambda: now.timestamp())
    seen: list[str] = []
    async for item in source.items(None):
        if item.event is None:
            if len(seen) >= 2 or source.polls >= 2:
                break
            continue
        seen.append(item.event.id)
    assert seen == ["https://t.me/SomeChannel/101", "https://t.me/SomeChannel/102"]  # old one skipped
