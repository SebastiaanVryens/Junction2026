"""Nemotron job 3: generate synthetic influence campaigns to test FactsLens.

    python -m factslens.eval.redteam --per-difficulty 4 --out eval/campaigns/redteam-v1.json

Safety: campaigns are about a fictional country (Velmora) and use only reserved `.example`
domains; they exist only as files and are replayed offline through the detector (never posted,
never sent to Kafka's live topic). Synthetic accounts get DIDs that cannot exist on Bluesky.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from factslens.common.logging import configure_logging
from factslens.common.models import Event, EventKind, Platform
from factslens.common.settings import get_settings
from factslens.detector.scoring import Profile
from factslens.investigator.llm import ChatClient

log = logging.getLogger("factslens.eval.redteam")

Difficulty = Literal["simple", "varied", "evasive"]
DIFFICULTIES: tuple[Difficulty, ...] = ("simple", "varied", "evasive")
PROMPT_VERSION = "redteam-v1"

DIFFICULTY_BRIEF = {
    "simple": (
        "SIMPLE: 8 to 12 accounts created within the same few days (account age 2 to 10 days), "
        "tiny follower counts, empty or one-line bios. All accounts post the same text (copy-paste, "
        "some copies cut off at different lengths) within the first 5 minutes, all with the same link."
    ),
    "varied": (
        "VARIED: 8 to 12 accounts aged 5 to 60 days with short template-like bios. Posts paraphrase "
        "the same message but keep key phrases and hashtags; timing spread over 30 minutes with "
        "jitter; two or three variants of the link (tracking parameters, a second page)."
    ),
    "evasive": (
        "EVASIVE: 8 to 12 accounts aged 90 to 700 days with believable, different personal bios and "
        "normal follower counts. Every post is written differently (own words, personal framing, "
        "some in another European language), spread over 90 minutes, links via different pages or "
        "none. The only shared thing is the narrative. Try hard to avoid looking coordinated."
    ),
}

SYSTEM_PROMPT = """You generate synthetic test data for FactsLens, a detector of coordinated \
inauthentic behaviour. The data is used only offline to measure the detector; it is never posted.

Hard rules:
- Everything is about the FICTIONAL country of Velmora (capital Ostrava Nova, fictional towns, \
fictional politicians and organisations). Never mention real countries, real people, real \
organisations, real events or real ethnic or religious groups.
- Links use only the reserved domain suffix .example (e.g. https://velmora-today.example/...).
- No instructions for real-world harm, no hate against real groups.
- The campaign pushes one misleading narrative about Velmora's public affairs (e.g. a staged \
water-plant scandal), using manipulation techniques such as loaded language, fear appeals, \
whataboutism or fabricated authority, so the investigator has something to name."""


class Persona(BaseModel):
    bio: str
    account_age_days: int = Field(ge=0, le=5000)
    followers: int = Field(ge=0)
    follows: int = Field(ge=0)
    has_avatar: bool


class CampaignPost(BaseModel):
    persona: int = Field(ge=0, description="index into personas")
    minute: float = Field(ge=0, le=180, description="minutes after the campaign starts")
    text: str = Field(max_length=300)
    lang: str = Field(description="ISO 639-1 language code")
    link: str | None = None

    @field_validator("link")
    @classmethod
    def reserved_domain_only(cls, value: str | None) -> str | None:
        if value is not None and not value.split("/")[2].endswith(".example"):
            raise ValueError("links must use a .example domain")
        return value


class Campaign(BaseModel):
    difficulty: Difficulty
    narrative: str
    tactics: list[str] = Field(description="evasion tactics used, in plain words")
    techniques: list[Literal["loaded_language", "fear_appeal", "whataboutism", "fabricated_authority"]]
    personas: list[Persona] = Field(min_length=5, max_length=15)
    posts: list[CampaignPost] = Field(min_length=5, max_length=40)


class CampaignSet(BaseModel):
    prompt_version: str
    model: str
    generated_at: datetime
    campaigns: list[Campaign]


def generate(chat: ChatClient, difficulty: Difficulty, index: int) -> Campaign:
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "campaign", "schema": Campaign.model_json_schema()},
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Write campaign number {index + 1} at difficulty {difficulty}. "
            f"{DIFFICULTY_BRIEF[difficulty]} Use a different narrative and different personas than "
            "a typical example would; vary the theme (health, energy, elections, transport, housing). "
            "Every persona must post at least once. Return JSON only.",
        },
    ]
    for attempt in range(2):
        response = chat.chat(
            messages,
            purpose=f"redteam:{difficulty}",
            response_format=schema,
            max_tokens=6000,
            thinking_tokens=1024,
        )
        try:
            campaign = Campaign.model_validate_json(response.content)
        except ValidationError as error:
            if attempt == 1:
                raise
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:6])
            messages += [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": f"Fix these problems and return the whole JSON: {problems}"},
            ]
            continue
        if campaign.difficulty != difficulty:
            campaign = campaign.model_copy(update={"difficulty": difficulty})
        return campaign
    raise RuntimeError("unreachable")


# --- turning a campaign into stream events and profiles ------------------------------------


def account_id(campaign_index: int, persona: int) -> str:
    # 'synthetic' cannot occur in a real did:plc (base32 alphabet excludes 'y'), so no collision.
    return f"did:plc:synthetic{campaign_index:03d}x{persona:03d}"


def to_events(campaign: Campaign, campaign_index: int, start_us: int) -> list[Event]:
    events = []
    for n, post in enumerate(campaign.posts):
        persona = min(post.persona, len(campaign.personas) - 1)
        author = account_id(campaign_index, persona)
        events.append(
            Event(
                id=f"at://{author}/app.bsky.feed.post/syn{campaign_index:03d}p{n:03d}",
                platform=Platform.BLUESKY,
                kind=EventKind.POST,
                author=author,
                time_us=start_us + int(post.minute * 60_000_000),
                text=post.text,
                langs=(post.lang,),
                links=(post.link,) if post.link else (),
                synthetic=True,
            )
        )
    return sorted(events, key=lambda e: e.time_us)


def to_profiles(campaign: Campaign, campaign_index: int, at: datetime) -> dict[str, Profile]:
    return {
        account_id(campaign_index, i): Profile(
            available=True,
            handle=f"synthetic{campaign_index}-{i}.example",
            created_at=at - timedelta(days=p.account_age_days),
            followers=p.followers,
            follows=p.follows,
            has_avatar=p.has_avatar,
            description=p.bio,
        )
        for i, p in enumerate(campaign.personas)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate red-team campaigns with Nemotron")
    parser.add_argument("--per-difficulty", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path("eval/campaigns/redteam-v1.json"))
    args = parser.parse_args()
    settings = get_settings()
    chat = ChatClient.from_settings(settings, logger=lambda r: log.info("model call", extra=r.__dict__))
    campaigns: list[Campaign] = []
    for difficulty in DIFFICULTIES:
        for index in range(args.per_difficulty):
            try:
                campaign = generate(chat, difficulty, index)
            except Exception as error:
                log.warning(
                    "campaign generation failed", extra={"difficulty": difficulty, "error": str(error)[:300]}
                )
                continue
            campaigns.append(campaign)
            log.info("campaign generated", extra={"difficulty": difficulty, "posts": len(campaign.posts)})
    result = CampaignSet(
        prompt_version=PROMPT_VERSION,
        model=chat.model,
        generated_at=datetime.now().astimezone(),
        campaigns=campaigns,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result.model_dump(mode="json"), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"{len(campaigns)} campaigns written to {args.out}")


if __name__ == "__main__":
    configure_logging()
    main()
