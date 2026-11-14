from __future__ import annotations

import httpx
import pytest

from factslens.investigator.evidence import EvidenceGatherer, FetchRefused, extract_text, is_public

ADDRESSES = {
    "news.example.org": ["93.184.215.14"],
    "evil.example.org": ["169.254.169.254"],  # cloud metadata endpoint
    "intranet.example.org": ["10.0.0.5"],
    "mixed.example.org": ["93.184.215.14", "127.0.0.1"],
}


def resolver(host: str) -> list[str]:
    if host not in ADDRESSES:
        raise OSError("no such host")
    return ADDRESSES[host]


def gatherer(handler: object) -> EvidenceGatherer:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return EvidenceGatherer(client, resolver=resolver, rdap_base="https://rdap.test/domain/")


def test_is_public() -> None:
    assert is_public("news.example.org", resolver)
    for host in ("evil.example.org", "intranet.example.org", "mixed.example.org", "missing.example.org"):
        assert not is_public(host, resolver), host


def test_extract_text_skips_scripts_and_reads_meta() -> None:
    html = """<html><head><title> Big  Story </title><meta name="description" content="What happened">
    <script>var x = 'ignore me';</script></head><body><nav>Menu</nav><h1>Headline</h1>
    <p>First paragraph.</p><style>.x{}</style><p>Second &amp; last.</p></body></html>"""
    title, description, text = extract_text(html)
    assert (title, description) == ("Big Story", "What happened")
    assert text == "Headline First paragraph. Second & last."


def test_fetch_page_follows_safe_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, html="<title>New</title><p>Body text</p>")

    page = gatherer(handler).fetch_page("https://news.example.org/old")
    assert (
        page.final_url == "https://news.example.org/new" and page.title == "New" and page.text == "Body text"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example.org/latest/meta-data",
        "https://intranet.example.org/",
        "ftp://news.example.org/file",
        "https://news.example.org:8443/",
    ],
)
def test_fetch_page_refuses_unsafe_targets(url: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, html="secret")

    with pytest.raises(FetchRefused):
        gatherer(handler).fetch_page(url)
    assert calls == []


def test_redirect_to_private_address_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://evil.example.org/latest/meta-data"})

    with pytest.raises(FetchRefused, match="not a public"):
        gatherer(handler).fetch_page("https://news.example.org/x")


def test_non_html_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with pytest.raises(FetchRefused, match="content type"):
        gatherer(handler).fetch_page("https://news.example.org/a.pdf")


def test_domain_info_falls_back_to_the_registered_domain_and_caches() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        domain = request.url.path.rsplit("/", 1)[1]
        asked.append(domain)
        if domain != "news-pravda.com":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "events": [{"eventAction": "registration", "eventDate": "2024-05-23T09:23:12Z"}],
                "entities": [
                    {
                        "roles": ["registrar"],
                        "vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar"]]],
                    }
                ],
            },
        )

    tools = gatherer(handler)
    info = tools.domain_info("france.news-pravda.com")
    assert info is not None and info.domain == "news-pravda.com" and info.registrar == "Example Registrar"
    assert info.registered is not None and info.registered.year == 2024
    assert tools.domain_info("france.news-pravda.com") is info and asked == [
        "france.news-pravda.com",
        "news-pravda.com",
    ]
    assert tools.domain_info("not a domain") is None
