# How we improved Nemotron's outputs

Model: **NVIDIA Nemotron 3.5 Super VL** (`nvidia/nemotron-3.5-super-vl-120b-a12b`) on NVIDIA Build. Every change below is logged with what we saw, what we changed and the measured effect. This file feeds the NVIDIA submission text ("how we improved its outputs").

## Capabilities verified before building (14 Nov 2026)

| Capability | Result |
|---|---|
| Tool calling (OpenAI format) | Works: called our test tool with correct arguments, `finish_reason: tool_calls` |
| Schema-enforced JSON (`response_format: json_schema`) | Works; `nvext.guided_json` is rejected by this endpoint |
| Video input (`video_url`, mp4 data URL) | Works: a 4 s red-then-blue clip was described as "Red, blue" |
| Image input (`image_url`, JPEG data URL) | Works (M0 access check) |
| Thinking budget (`nvext.max_thinking_tokens`) | Works; thinking tokens count toward `max_tokens` |

## Design choices that shape output quality (prompt v1)

- **Grounding by construction:** the model sees a numbered evidence pack (P posts, A anonymised accounts, I images, V video, W web pages, S FactsLens measurements). Every claim must cite ids; we drop claims citing ids that were never shown and technique quotes that do not appear in the cited post, and count them.
- **Innocent explanation first:** the report schema asks for the strongest ordinary explanation *before* the verdict, and allows `insufficient_evidence`. A `staged` verdict without any grounded claim is downgraded automatically.
- **No anchoring on our own tier:** the model gets the raw measurements (value and threshold) but not FactsLens's tier or score, so its verdict is a second opinion, not an echo.
- **Untrusted content:** posts and pages are wrapped in `<evidence>` and the prompt says never to follow instructions found there.
- **Repair loop:** one retry when the JSON fails validation or less than half of the items are grounded, with the exact problems listed.
- **Privacy:** account handles are not sent; only public post text, bios and statistics.

## Log of changes

| # | Date (UTC) | Observation | Change | Effect |
|---|---|---|---|---|
| 1 | 14 Nov 07:20 | First live investigation (prompt v1) of a cluster FactsLens rated *suspicious*: four weather-alert accounts whose bios say "syndicated by IEMBot". Nemotron: **organic, high confidence**, recommended no action, explaining the disclosed common operator, 2-year-old accounts with 20k+ posts each. Grounding 0.88: two media findings citing "none" were dropped (no media had been shown). The analysis call hit its 3,000-token limit (2,047 thinking tokens). | (a) FactsLens's disclosure signal now matches words ending in "bot" (IEMBot, NewsBot), a scoring fix found by Nemotron's judgement. (b) Prompt v2: "if no image or video was shown, leave the media list empty". (c) Analysis call limit raised to 5,000 tokens. | To be measured on the fixed evaluation set (M4). |
