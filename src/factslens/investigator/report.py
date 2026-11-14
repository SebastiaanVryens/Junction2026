"""The investigation report Nemotron must produce, and the grounding check we run on it.

The model fills this schema (enforced with the API's JSON-schema mode, then validated here).
Every claim must cite evidence ids from the pack it was shown (P = post, A = account,
I = image, V = video, W = web page, S = FactsLens signal); technique quotes must appear in the
cited post. Ungrounded items are removed and counted, never shown as findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

TechniqueLabel = Literal["loaded_language", "fear_appeal", "whataboutism", "fabricated_authority", "other"]
Verdict = Literal["staged", "organic", "insufficient_evidence"]

TECHNIQUE_NAMES = {
    "loaded_language": "Loaded language",
    "fear_appeal": "Fear appeal",
    "whataboutism": "Whataboutism",
    "fabricated_authority": "Fabricated authority",
    "other": "Other technique",
}
REF_RE = re.compile(r"^(P|A|I|V|W)\d{1,3}$|^S:[a-z_]{2,40}$")


class Technique(BaseModel):
    label: TechniqueLabel
    name: str = Field(description="Plain-language name of the technique")
    disarm_id: str | None = Field(default=None, description="DISARM technique id, only if certain, else null")
    quote: str = Field(description="Exact words from the cited post that show the technique")
    ref: str = Field(description="Id of the post the quote comes from, e.g. P2")
    explanation: str = Field(description="Why these words are this technique, for a non-expert reader")


class Claim(BaseModel):
    statement: str
    refs: list[str] = Field(
        description="Evidence ids supporting the statement, e.g. ['P1', 'A3', 'S:new_accounts']"
    )


class SourceAssessment(BaseModel):
    domain: str
    trust: Literal["low", "medium", "high", "unknown"]
    reasons: list[str]


class MediaFinding(BaseModel):
    ref: str = Field(description="I1, I2 or V1")
    observation: str = Field(
        description="What the image or video shows and whether it supports the post text"
    )


class Report(BaseModel):
    summary: str = Field(description="Two or three sentences for a moderator")
    narrative: str = Field(description="The story or claim the posts push, in neutral words")
    innocent_explanation: str = Field(
        description="The strongest ordinary explanation for this activity, considered before the verdict"
    )
    verdict: Verdict
    confidence: Literal["low", "medium", "high"]
    verdict_reasons: list[str]
    techniques: list[Technique]
    sources: list[SourceAssessment]
    media: list[MediaFinding]
    claims: list[Claim]
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
    cleaned = report.model_copy(update={"claims": claims, "techniques": techniques, "media": media})
    if cleaned.verdict == "staged" and not claims:
        cleaned = cleaned.model_copy(
            update={
                "verdict": "insufficient_evidence",
                "verdict_reasons": [*cleaned.verdict_reasons, "No grounded claim supports 'staged'."],
            }
        )
        g.problems.append("verdict 'staged' had no grounded claim; downgraded to insufficient_evidence")
    return cleaned, g
