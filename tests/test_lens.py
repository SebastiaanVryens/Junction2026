"""The Lens: reading a link, the evidence pack, grounding, and Nemotron's two-step report."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from test_investigator import NOW, FakeGatherer, ScriptedChat, reply, search_call

from factslens.enrich.lists import KnownLists
from factslens.investigator import lens
from factslens.investigator.evidence import Page

APPVIEW = "https://public.api.bsky.app/xrpc"


def bluesky_transport(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("resolveHandle"):
        return httpx.Response(200, json={"did": "did:plc:abc"})
    if path.endswith("getPostThread"):
        assert request.url.params["uri"] == "at://did:plc:abc/app.bsky.feed.post/3kxyz"
        post = {
            "author": {"handle": "newsbot.bsky.social", "displayName": "News Bot"},
            "record": {
                "text": "BREAKING: they are hiding the truth! Read https://example-news.test/a",
                "createdAt": "2026-11-14T10:00:00Z",
                "facets": [{"features": [{"uri": "https://example-news.test/a?x=1&y=2"}]}],
            },
            "embed": {"images": [{"fullsize": "https://cdn.bsky.app/img/full/1.jpg"}]},
            "likeCount": 3,
            "repostCount": 40,
        }
        return httpx.Response(200, json={"thread": {"post": post}})
    if path.endswith("getProfile"):
        return httpx.Response(
            200, json={"createdAt": "2026-11-10T00:00:00Z", "followersCount": 2, "followsCount": 900}
        )
    return httpx.Response(404)


def test_a_bluesky_link_is_read_with_its_author_links_and_pictures() -> None:
    http = httpx.Client(transport=httpx.MockTransport(bluesky_transport))
    target = lens.read_link(
        "https://bsky.app/profile/newsbot.bsky.social/post/3kxyz",
        FakeGatherer(),
        KnownLists(),
        http,  # type: ignore[arg-type]
    )
    assert target.kind == "bluesky" and target.author == "@newsbot.bsky.social"
    assert target.post_id == "at://did:plc:abc/app.bsky.feed.post/3kxyz"
    assert target.author_id == "did:plc:abc"
    assert target.images == ["https://cdn.bsky.app/img/full/1.jpg"]
    assert target.profile["followers"] == 2 and target.profile["follows"] == 900
    assert target.engagement["repost"] == 40
    # both spellings of the link are looked up; the website is matched, platforms are not
    assert "https://example-news.test/a?x=1&amp;y=2" in target.match_links
    assert target.match_domains == ["example-news.test"]


def test_an_article_is_read_as_a_website_and_is_itself_a_link_to_look_up() -> None:
    lists = KnownLists(domains={"example-news.test": "viginum_portal_kombat"})
    target = lens.read_link("https://example-news.test/story", FakeGatherer(), lists)  # type: ignore[arg-type]
    assert target.kind == "article" and target.author == "example-news.test"
    assert target.profile["on_propaganda_lists"] == ["viginum_portal_kombat"]
    # the fake domain was registered 12 days before NOW; age_days counts to the real today
    assert target.profile["age_days"] == (datetime.now(UTC) - (NOW - timedelta(days=12))).days
    assert target.match_links == ["https://example-news.test/story"]
    assert target.author_id is None


def test_a_consent_page_is_not_mistaken_for_the_article() -> None:
    class Gated(FakeGatherer):
        def fetch_page(self, url: str) -> Page:
            return Page(url, "https://myprivacy.dpgmedia.be/consent?x=1", 200, "Privacy", "", "Cookies")

    url = "https://www.hln.be/rode-duivels/rudi-garcia-komt-terug-op-balogun-gate~a0c8e13d/"
    target = lens.read_link(url, Gated(), KnownLists())  # type: ignore[arg-type]
    assert target.author == "hln.be" and target.profile["website"] == "hln.be"
    assert target.text == "rudi garcia komt terug op balogun gate"
    assert "myprivacy.dpgmedia.be" in target.profile["gate"]
    assert "could not be read" in lens.build_pack(target, [], []).text


def test_headlines_are_read_from_addresses() -> None:
    assert lens.headline_from_url(
        "https://x.be/sport/voetbal/tielemans-wordt-gerespecteerd/164551552.html"
    ) == ("tielemans wordt gerespecteerd")
    assert lens.headline_from_url("https://x.com/a/74-20256883") == ""
    assert lens._site("news.bbc.co.uk") == "bbc.co.uk" and lens._site("www.hln.be") == "hln.be"


def test_an_article_gets_its_country_and_national_editions() -> None:
    article = lens.Target(
        url="https://www.hln.be/sport/x",
        kind="article",
        text="Rudi Garcia over de Balogun-gate",
        author="hln.be",
        profile={"platform": "website", "website": "hln.be", "registered": "1999-01-01", "age_days": 9800},
    )
    pack = lens.build_pack(article, [], [])
    assert "country by domain: Belgium, news editions nl-BE, fr-BE" in pack.text


@pytest.mark.parametrize(
    "url",
    ["not a link", "ftp://example.com/x", "https://example.com/" + "a" * 700],
)
def test_bad_input_gets_a_plain_message(url: str) -> None:
    with pytest.raises(lens.LensError, match="full link"):
        lens.read_link(url, FakeGatherer(), KnownLists())  # type: ignore[arg-type]


def test_internal_addresses_are_refused() -> None:
    with pytest.raises(lens.LensError, match="cannot be read"):
        lens.read_link("https://blocked.internal/x", FakeGatherer(), KnownLists())  # type: ignore[arg-type]


def test_post_urls_are_recognised_per_platform() -> None:
    assert lens.BLUESKY_POST.match("https://bsky.app/profile/a.bsky.social/post/3kabc")
    assert lens.MASTODON_POST.match("https://mastodon.social/@someone/113456789")
    assert lens.MASTODON_POST.match("https://example.org/users/x/statuses/42")
    assert lens.TELEGRAM_POST.match("https://t.me/rybar/12345")
    assert not lens.TELEGRAM_POST.match("https://t.me/rybar")


def target() -> lens.Target:
    return lens.Target(
        url="https://bsky.app/profile/newsbot.bsky.social/post/3kxyz",
        kind="bluesky",
        text="BREAKING: they are hiding the truth about the fire!",
        author="@newsbot.bsky.social",
        post_id="at://did:plc:abc/app.bsky.feed.post/3kxyz",
        profile={"platform": "Bluesky", "handle": "newsbot.bsky.social", "age_days": 4, "followers": 2},
    )


MATCH = {
    "id": "text:1", "kind": "text", "label": "they are hiding the truth", "tier": "suspicious",
    "accounts": 12, "verdict": "staged", "how": ["the same author"],
    "campaign": {"id": 3, "title": "Fire hoax push", "verdict": "coordinated"},
    "stories": [{"id": 9, "title": "The fire", "status": "contested"}],
}  # fmt: skip


def test_the_pack_numbers_our_network_matches_and_pictures() -> None:
    pack = lens.build_pack(target(), [MATCH], ["data:image/jpeg;base64,AAAA"])
    assert "C1: matched by the same author; text cluster of 12 accounts" in pack.text
    assert "Nemotron verdict staged" in pack.text and "campaign 'Fire hoax push'" in pack.text
    assert "contested story 'The fire'" in pack.text
    assert pack.refs >= {"T1", "A1", "C1", "I1"}
    assert pack.attachments[1]["image_url"]["url"].startswith("data:image/jpeg")
    empty = lens.build_pack(target(), [], [])
    assert "do not appear in our network" in empty.text and "C1" not in empty.refs


def lens_report(**overrides: Any) -> str:
    report = {
        "behind_it": [{"statement": "A four-day-old account in a staged swarm.", "refs": ["A1", "C1"]}],
        "claims": [],
        "techniques": [
            {
                "name": "Fear appeal",
                "quote": "hiding the truth",
                "quote_in_english": "",
                "explanation": "Suggests a cover-up.",
            }
        ],
        "pictures": [],
        "reasons": ["new account", "part of a staged group"],
        "caution": "likely_coordinated_or_misleading",
        "confidence": "medium",
        "in_one_sentence": "A new account in a coordinated group pushes this; check before sharing.",
        "what_you_can_do": ["Look for the story on an outlet you trust."],
    }
    return json.dumps(report | overrides)


def test_grounding_drops_invented_ids_and_quotes_and_downgrades_the_verdict() -> None:
    pack = lens.build_pack(target(), [], [])
    report = lens.LensReport.model_validate_json(
        lens_report(
            behind_it=[{"statement": "Part of a swarm.", "refs": ["C7"]}],
            techniques=[
                {"name": "Loaded language", "quote": "traitors", "quote_in_english": "", "explanation": "x"}
            ],
            claims=[{"claim": "A fire", "explanation": "x", "finding": "debunked", "refs": []}],
        )
    )
    cleaned, dropped = lens.ground(report, pack)
    assert cleaned.behind_it == [] and cleaned.techniques == []
    assert cleaned.claims[0].finding == "not_checked"
    assert cleaned.caution == "insufficient_evidence"
    assert any("downgraded" in d for d in dropped) and "C7" in dropped


def test_comma_joined_ids_are_split_and_kept() -> None:
    pack = lens.build_pack(target(), [MATCH], [])
    report = lens.LensReport.model_validate_json(
        lens_report(behind_it=[{"statement": "x", "refs": ["A1, C1", "'C1',"]}])
    )
    cleaned, _ = lens.ground(report, pack)
    assert cleaned.behind_it[0].refs == ["A1", "C1", "C1"]
    assert cleaned.caution == "likely_coordinated_or_misleading"


def test_examine_searches_the_news_then_writes_a_grounded_report() -> None:
    claim = {"claim": "A fire", "explanation": "Two outlets report it.", "finding": "confirmed_as_posted",
             "refs": ["N1"]}  # fmt: skip
    chat = ScriptedChat(
        [
            reply(tool_calls=[search_call("fire hiding truth")]),
            reply("Analysis: a new account, a staged group, a real fire."),
            reply(lens_report(claims=[claim])),
        ]
    )
    gatherer = FakeGatherer()
    pack = lens.build_pack(target(), [MATCH], [])
    result = lens.examine(chat, gatherer, KnownLists(), pack)  # type: ignore[arg-type]
    assert result.error is None and result.report is not None
    assert gatherer.searches == [("fire hiding truth", "en")]
    assert result.report.claims[0].finding == "confirmed_as_posted"
    assert result.report.techniques[0].quote == "hiding the truth"
    assert result.calls == 3 and "N1" in result.news and "Q1" in result.searches
    assert chat.requests[-1]["response_format"]["json_schema"]["name"] == "lens"


def test_a_failing_model_is_reported_not_raised() -> None:
    class Broken:
        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
            raise RuntimeError("NVIDIA is down")

    result = lens.examine(Broken(), FakeGatherer(), KnownLists(), lens.build_pack(target(), [], []))  # type: ignore[arg-type]
    assert result.report is None and result.error and "NVIDIA is down" in result.error


def test_ids_in_sentences_become_words_and_empty_items_go() -> None:
    pack = lens.build_pack(target(), [MATCH], [])
    report = lens.LensReport.model_validate_json(
        lens_report(
            behind_it=[{"statement": "The account (A1) is in C1 (12 accounts).", "refs": ["A1", "C1"]}],
            reasons=["A1 is new", ".", ": C1 is staged"],
            what_you_can_do=[".", "Check T1 elsewhere."],
        )
    )
    cleaned, _ = lens.ground(report, pack)
    assert cleaned.behind_it[0].statement == "The account is in group 1 (12 accounts)."
    assert cleaned.reasons == ["The author is new", "Group 1 is staged"]
    assert cleaned.what_you_can_do == ["Check the text elsewhere."]
    assert lens.plain("A1 was registered in 2019", article=True) == "The website was registered in 2019"


def test_a_translation_is_shown_only_for_text_in_another_language() -> None:
    assert lens.looks_english("They are hiding the truth about the fire in the city, and this is why.")
    assert not lens.looks_english("Возвращение к корням: британцы забирают украинские БЭКи?")
    assert not lens.looks_english("Hallitus esitti budjetin, ja oppositio arvosteli sitä voimakkaasti.")


def test_the_report_needs_at_least_one_reason() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        lens.LensReport.model_validate_json(lens_report(reasons=[]))


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://x.com/NATO", "This is a X profile or page, not a post"),
        ("https://www.instagram.com/natoofficial/", "This is a Instagram profile or page"),
        ("https://bsky.app/profile/bsky.app", "a Bluesky profile, not a post"),
        ("https://t.me/rybar", "a Telegram channel, not a post"),
        ("https://mastodon.social/@Gargron", "a profile page, not a post"),
    ],
)
def test_links_we_cannot_check_get_a_clear_answer_before_any_work(url: str, message: str) -> None:
    with pytest.raises(lens.LensError, match=message):
        lens.read_link(url, FakeGatherer(), KnownLists())  # type: ignore[arg-type]


def test_a_missing_page_is_not_analysed() -> None:
    class Missing(FakeGatherer):
        def fetch_page(self, url: str) -> Page:
            return Page(url, url, 404, "Post Not Found", "", "The post you're looking for does not exist")

    with pytest.raises(lens.LensError, match="does not exist or is not public"):
        lens.read_link("https://example-news.test/gone", Missing(), KnownLists())  # type: ignore[arg-type]


def test_posts_on_those_sites_are_still_posts() -> None:
    assert lens.BLUESKY_POST.match("https://bsky.app/profile/bsky.app/post/3kabc")
    lens.not_a_post("https://t.me/rybar/84675")  # no error: a post
    lens.not_a_post("https://mastodon.social/@Gargron/113456789")


def walled_transport(request: httpx.Request) -> httpx.Response:
    if request.url.host == "publish.twitter.com":
        html = (
            "<blockquote><p>NATO troops arrive in Finland tonight</p>"
            "&mdash; Example (@example) November 14, 2026</blockquote>"
        )
        return httpx.Response(200, json={"author_name": "Example", "html": html})
    return httpx.Response(404)


def test_an_x_post_is_read_from_its_official_embed() -> None:
    http = httpx.Client(transport=httpx.MockTransport(walled_transport))
    target = lens.read_link("https://x.com/example/status/123", FakeGatherer(), KnownLists(), http)  # type: ignore[arg-type]
    assert target.kind == "preview" and target.author == "Example"
    assert target.text == "NATO troops arrive in Finland tonight"
    assert "https://x.com/example/status/123" in target.match_links  # swarms that shared it are found


def test_an_instagram_post_is_read_from_its_link_preview_and_a_login_page_is_refused() -> None:
    class Preview(FakeGatherer):
        def fetch_page(self, url: str) -> Page:
            if "threads" in url:
                return Page(url, url, 200, "Threads &#x2022; Log in", "Join Threads to share ideas", "")
            title = "Just An Egg on Instagram: &quot;World record&quot;"
            return Page(url, url, 200, "Instagram", "Let's set a world record", "",
                        image="https://cdn.example/egg.jpg", og_title=title)  # fmt: skip

    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    target = lens.read_link("https://www.instagram.com/p/BsOGulcndj-/", Preview(), KnownLists(), http)  # type: ignore[arg-type]
    assert target.author == "Just An Egg" and target.text == "Let's set a world record"
    assert target.images == ["https://cdn.example/egg.jpg"]
    with pytest.raises(lens.LensError, match="only to logged-in readers"):
        lens.read_link("https://www.threads.net/@zuck/post/Cu1", Preview(), KnownLists(), http)  # type: ignore[arg-type]


def test_a_linkedin_post_is_read_from_the_words_in_its_link() -> None:
    class Nothing(FakeGatherer):
        def fetch_page(self, url: str) -> Page:
            return Page(url, url, 404, "", "", "")

    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    url = "https://www.linkedin.com/posts/janedoe_nato-is-preparing-for-war-activity-7100000000000000000-abcd"
    target = lens.read_link(url, Nothing(), KnownLists(), http)  # type: ignore[arg-type]
    assert target.author == "janedoe" and target.text == "nato is preparing for war"


def test_a_generic_page_is_not_mistaken_for_the_post() -> None:
    class Generic(FakeGatherer):
        def fetch_page(self, url: str) -> Page:
            return Page(url, url, 200, "Facebook", "", "")

    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(lens.LensError, match="Facebook shows this post only to logged-in readers"):
        lens.read_link("https://www.facebook.com/zuck/posts/1", Generic(), KnownLists(), http)  # type: ignore[arg-type]


def test_a_facebook_post_is_read_from_the_words_in_its_link() -> None:
    class Nothing(FakeGatherer):
        def fetch_page(self, url: str) -> Page:
            return Page(url, url, 200, "Facebook", "", "")

    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    url = "https://www.facebook.com/yleuutiset/videos/nato-troops-arrive-in-finland-tonight/1234567890/"
    target = lens.read_link(url, Nothing(), KnownLists(), http)  # type: ignore[arg-type]
    assert target.author == "yleuutiset" and target.text == "nato troops arrive in finland tonight"
    assert "the words in the post's link" in target.profile["note"]
    with pytest.raises(lens.LensError, match="only to logged-in readers"):  # no words in this link
        lens.read_link("https://www.facebook.com/share/p/1AbCdEfGh/", Nothing(), KnownLists(), http)  # type: ignore[arg-type]
