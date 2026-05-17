"""
Mirsad — Policy Agent
=====================

Scans publications from government nudge units, ministries, regulators,
and behavioural-science institutions for real-world choice-architecture
moves. For each new item it asks Claude to:

  1. Decide whether it counts as a behavioural intervention.
  2. Identify which BeSci mechanism(s) are at work.
  3. Write a tight 70–90 word explainer in English (+ Arabic).
  4. Tag any countries involved.

Runs on its own schedule (every 12h in CI). No human in the loop.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from common import (
    BEATS, MECHANISMS, Post, append_post, ask_json, fetch_rss,
    load_posts, log, make_id, with_retry,
)

os.environ.setdefault("MIRSAD_AGENT", "policy")

# Curated feeds. Add or remove freely; the agent doesn't care.
# Each entry: (display source name, RSS url)
SOURCES: list[tuple[str, str]] = [
    ("Behavioural Insights Team",  "https://www.bi.team/feed/"),
    ("ideas42",                    "https://www.ideas42.org/feed/"),
    ("Behavioral Scientist",       "https://behavioralscientist.org/feed/"),
    ("BehavioralEconomics.com",    "https://www.behavioraleconomics.com/feed/"),
]

# Keep work units small per run — we don't need to drown the API.
MAX_NEW_PER_RUN = 6


SYSTEM_PROMPT = f"""You are the Policy beat agent for Mirsad, a public observatory
of how choice architecture is being used in the world.

You read a short post from a behavioural-science source and decide whether it
describes a real policy, programme, or institutional intervention that shapes
human behaviour. Marketing fluff, generic explainers, and "what is a nudge"
think-pieces do NOT qualify. A government changing a default, mandating a
disclosure, redesigning a form, running a field trial, or sending a behavioural
SMS DOES qualify.

If it qualifies, classify the mechanism(s) used from this fixed taxonomy:
{', '.join(MECHANISMS)}

Output JSON only with this exact shape:
{{
  "qualifies": true | false,
  "title_en": "8–14 word neutral title",
  "title_ar": "Arabic translation of the title",
  "summary_en": "70–90 words. What was done, by whom, where, and why it works behaviourally. Plain English.",
  "summary_ar": "Arabic translation of the summary",
  "mechanisms": ["..."],
  "countries": ["ISO English country names, [] if not applicable"],
  "verdict": "One short BeSci-flavoured line. Max 15 words."
}}
"""


def evaluate(entry: dict) -> dict | None:
    user = (
        f"Title: {entry['title']}\n\n"
        f"Source excerpt:\n{entry['summary']}\n\n"
        f"Source URL: {entry['link']}\n\n"
        f"Return JSON per the schema."
    )
    try:
        return with_retry(lambda: ask_json(SYSTEM_PROMPT, user, max_tokens=1400))
    except Exception as e:
        log(f"LLM eval failed for {entry['link']}: {e}")
        return None


def run() -> None:
    seen = {p["id"] for p in load_posts()}
    added = 0

    for source_name, url in SOURCES:
        if added >= MAX_NEW_PER_RUN:
            break
        log(f"fetching {source_name} — {url}")
        entries = fetch_rss(url)
        log(f"  found {len(entries)} entries")

        for e in entries:
            if added >= MAX_NEW_PER_RUN:
                break
            pid = make_id(e["link"])
            if pid in seen:
                continue

            verdict = evaluate(e)
            if not verdict or not verdict.get("qualifies"):
                continue

            # Filter mechanisms to known taxonomy
            mechs = [m for m in (verdict.get("mechanisms") or []) if m in MECHANISMS]

            post = Post(
                id=pid,
                beat="policy",
                title_en=verdict.get("title_en", e["title"])[:200],
                title_ar=verdict.get("title_ar", "")[:200],
                summary_en=verdict.get("summary_en", "")[:1200],
                summary_ar=verdict.get("summary_ar", "")[:1200],
                mechanisms=mechs,
                source_url=e["link"],
                source_name=source_name,
                countries=verdict.get("countries", [])[:5],
                published_at=_iso_date(e.get("published", "")),
                discovered_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                verdict=verdict.get("verdict", "")[:200],
            )
            if append_post(post):
                log(f"  + {post.title_en}")
                added += 1
                seen.add(pid)

    log(f"done. added {added} posts.")


def _iso_date(raw: str) -> str:
    """Best-effort RSS date → YYYY-MM-DD. Empty string if we can't parse."""
    if not raw:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


if __name__ == "__main__":
    run()
