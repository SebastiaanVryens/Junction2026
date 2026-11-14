"""The investigation report Nemotron must produce, and the grounding check we run on it.

The model fills this schema (enforced with the API's JSON-schema mode, then validated here).
Every claim must cite evidence ids from the pack it was shown (P = post, A = account,
I = image, V = video, W = web page, Q = news search, N = news article, S = FactsLens signal);
technique quotes must appear in the cited post. Ungrounded items are removed and counted, never
shown as findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field


def capped(limit: int) -> Any:
    """A list of at most `limit` items. The cap is in the JSON schema, so constrained decoding has
    to close the list; longer lists are trimmed rather than rejected. Without it the model once
    looped inside a list until it ran out of tokens (14 Nov 2026)."""
    return (Field(max_length=limit), BeforeValidator(lambda v: v[:limit] if isinstance(v, list) else v))


TechniqueLabel = Literal["loaded_language", "fear_appeal", "whataboutism", "fabricated_authority", "other"]
Verdict = Literal["staged", "organic", "insufficient_evidence"]

TECHNIQUE_NAMES = {
    "loaded_language": "Loaded language",
    "fear_appeal": "Fear appeal",
    "whataboutism": "Whataboutism",
    "fabricated_authority": "Fabricated authority",
    "other": "Other technique",
}
REF_RE = re.compile(r"^(P|A|I|V|W|Q|N)\d{1,3}$|^S:[a-z_]{2,40}$")
NewsFinding = Literal[
    "independently_reported", "framed_differently", "debunked", "no_coverage_found", "not_checked"
]  # fmt: skip
NEEDS_ARTICLE = frozenset({"independently_reported", "framed_differently", "debunked"})


class Technique(BaseModel):
    label: TechniqueLabel
    name: str = Field(description="Plain-language name of the technique")
    disarm_id: str | None = Field(default=None, description="DISARM technique id, only if certain, else null")
    quote: str = Field(description="Exact words from the cited post that show the technique")
    ref: str = Field(description="Id of the post the quote comes from, e.g. P2")
    explanation: str = Field(description="Why these words are this technique, for a non-expert reader")


class Claim(BaseModel):
    statement: str
    refs: Annotated[list[str], *capped(8)] = Field(
        description="Evidence ids supporting the statement, e.g. ['P1', 'A3', 'S:new_accounts']"
    )


class SourceAssessment(BaseModel):
    domain: str
    trust: Literal["low", "medium", "high", "unknown"]
    reasons: Annotated[list[str], *capped(4)]


class MediaFinding(BaseModel):
    ref: str = Field(description="I1, I2 or V1")
    observation: str = Field(
        description="What the image or video shows and whether it supports the post text"
    )


class NewsCheck(BaseModel):
    finding: NewsFinding = Field(
        description="independently_reported: independent outlets tell the same story as the posts; "
        "framed_differently: outlets report the underlying event, but the posts distort, exaggerate or "
        "reframe it; debunked: outlets show the claim is false; no_coverage_found: searches ran and no "
        "independent outlet reports it; not_checked: you did not search"
    )
    explanation: str = Field(
        description="Which outlets report it and how; whether those outlets are independent of the "
        "narrative's beneficiary"
    )
    refs: Annotated[list[str], *capped(8)] = Field(
        description="N ids of the articles, or the Q id of a search that found nothing"
    )


class Report(BaseModel):
    summary: str = Field(description="Two or three sentences for a moderator")
    narrative: str = Field(description="The story or claim the posts push, in neutral words")
    innocent_explanation: str = Field(
        description="The strongest ordinary explanation for this activity, considered before the verdict"
    )
    verdict: Verdict
    confidence: Literal["low", "medium", "high"]
    verdict_reasons: Annotated[list[str], *capped(5)]
    techniques: Annotated[list[Technique], *capped(6)]
    sources: Annotated[list[SourceAssessment], *capped(5)]
    media: Annotated[list[MediaFinding], *capped(5)]
    claims: Annotated[list[Claim], *capped(8)]
    news_check: NewsCheck
    recommended_action: Literal["no_action", "monitor", "human_review", "escalate"]


@dataclass
class Grounding:
    claims: int = 0
    claims_grounded: int = 0
    techniques: int = 0
    techniques_grounded: int = 0
    media: int = 0
    media_grounded: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        total = self.claims + self.techniques + self.media
        return (
            (self.claims_grounded + self.techniques_grounded + self.media_grounded) / total if total else 1.0
        )


def _norm(text: str) -> str:
    quotes = " .,'\"" + "".join(map(chr, (0x201C, 0x201D, 0x2018, 0x2019, 0x2026)))  # typographic quotes, …
    return re.sub(r"\s+", " ", text.casefold()).strip(quotes)


def ground(report: Report, valid_refs: set[str], post_texts: dict[str, str]) -> tuple[Report, Grounding]:
    """Keep only items backed by evidence the model was actually shown."""
    g = Grounding()
    claims = []
    for claim in report.claims:
        g.claims += 1
        refs = [r for r in claim.refs if r in valid_refs]
        if refs:
            g.claims_grounded += 1
            claims.append(claim.model_copy(update={"refs": refs}))
        else:
            g.problems.append(
                f"claim without valid evidence ids: {claim.statement[:80]!r} cited {claim.refs}"
            )
    techniques = []
    for technique in report.techniques:
        g.techniques += 1
        source = post_texts.get(technique.ref, "")
        if technique.ref in valid_refs and _norm(technique.quote) and _norm(technique.quote) in _norm(source):
            g.techniques_grounded += 1
            techniques.append(technique)
        else:
            g.problems.append(f"technique {technique.label}: quote not found in {technique.ref}")
    media = []
    for finding in report.media:
        g.media += 1
        if finding.ref in valid_refs:
            g.media_grounded += 1
            media.append(finding)
        else:
            g.problems.append(f"media finding cites {finding.ref}, which was not shown")
    news = report.news_check
    if news.finding != "not_checked":
        g.claims += 1
        refs = [r for r in news.refs if r in valid_refs and r[0] in "NQ"]
        articles = [r for r in refs if r[0] == "N"]
        if articles if news.finding in NEEDS_ARTICLE else refs:
            g.claims_grounded += 1
            news = news.model_copy(update={"refs": refs})
        else:
            g.problems.append(
                f"news check {news.finding!r} cites no search or article that was run: {news.refs}"
            )
            news = news.model_copy(update={"finding": "not_checked", "refs": []})
    cleaned = report.model_copy(
        update={"claims": claims, "techniques": techniques, "media": media, "news_check": news}
    )
    if cleaned.verdict == "staged" and not claims:
        cleaned = cleaned.model_copy(
            update={
                "verdict": "insufficient_evidence",
                "verdict_reasons": [*cleaned.verdict_reasons, "No grounded claim supports 'staged'."],
            }
        )
        g.problems.append("verdict 'staged' had no grounded claim; downgraded to insufficient_evidence")
    return cleaned, g
