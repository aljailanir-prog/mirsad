"""
Mirsad — Product Agent
======================

Scans tech and consumer product chatter for behavioural design — the
elegant (streaks done well, sensible defaults, helpful frictions) and the
predatory (dark patterns, deceptive cancellation flows, manipulative
notification design).

Sources are deliberately public and crowd-curated: Hacker News top + new,
plus Product Hunt's public RSS. Claude decides whether each item is
behavioural-design-relevant and names the pattern.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from common import (
    MECHANISMS, Post, append_post, ask_json, fetch_rss,
    load_posts, log, make_id, with_retry,
)

os.environ.setdefault("MIRSAD_AGENT", "product")

# Hacker News firebase endpoints
HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HN_STORIES_TO_SCAN = 60   # check the top N
MAX_NEW_PER_RUN = 5


SYSTEM_PROMPT = f"""You are the Product beat agent for Mirsad, a public observatory of
how human behaviour is being shaped in the wild.

You read a short item about a consumer/tech product, app, or service and decide
whether it describes a SPECIFIC behavioural-design choice — a default, a
friction, a notification regime, a streak, a cancellation flow, a dark
pattern, a disclosure, a manipulative onboarding step, etc.

Generic "we launched a new feature", VC news, hiring posts, or pure
infrastructure stories do NOT qualify. Only items where a real product
choice is shaping user behaviour qualify.

Tag mechanisms from this taxonomy:
{', '.join(MECHANISMS)}

Also set a "flag":
  - "elegant"    — the design supports the user's interest
  - "predatory"  — the design works against the user (a dark pattern or sludge)
  - "neutral"    — interesting but not clearly good or bad

Output JSON only:
{{
  "qualifies": true | false,
  "title_en": "8–14 word neutral title naming the product + pattern",
  "title_ar": "Arabic translation",
  "summary_en": "70–90 words. What the product does, the specific design choice, and the behavioural effect on the user. Plain English. Be concrete.",
  "summary_ar": "Arabic translation",
  "mechanisms": ["..."],
  "flag": "elegant | predatory | neutral",
  "verdict": "One short BeSci-flavoured line, max 15 words."
}}
"""


def fetch_hn_top(limit: int) -> list[dict]:
    try:
        ids = requests.get(HN_TOP, timeout=15).json()[:limit]
    except Exception as e:
        log(f"HN top fetch failed: {e}")
        return []
    out: list[dict] = []
    for i, sid in enumerate(ids):
        try:
            item = requests.get(HN_ITEM.format(sid), timeout=10).json()
        except Exception:
            continue
        if not item or item.get("type") != "story":
            continue
        url = item.get("url") or f"https://news.ycombinator.com/item?id={sid}"
        out.append({
            "title": item.get("title", "").strip(),
            "link": url,
            "summary": (item.get("text") or "")[:1000],
            "published": "",
            "source_name": "Hacker News",
        })
    return out


def fetch_producthunt() -> list[dict]:
    """Product Hunt's public feed. Best-effort — fine if it 404s."""
    entries = fetch_rss("https://www.producthunt.com/feed?category=undefined")
    for e in entries:
        e["source_name"] = "Product Hunt"
    return entries


def evaluate(entry: dict) -> dict | None:
    user = (
        f"Title: {entry['title']}\n\n"
        f"Source excerpt:\n{entry.get('summary','')[:900]}\n\n"
        f"Source URL: {entry['link']}\n\n"
        f"Return JSON per the schema."
    )
    try:
        return with_retry(lambda: ask_json(SYSTEM_PROMPT, user, max_tokens=1300))
    except Exception as e:
        log(f"LLM eval failed for {entry['link']}: {e}")
        return None


def run() -> None:
    seen = {p["id"] for p in load_posts()}
    added = 0

    candidates: list[dict] = []
    candidates.extend(fetch_hn_top(HN_STORIES_TO_SCAN))
    candidates.extend(fetch_producthunt())
    log(f"candidates collected: {len(candidates)}")

    for e in candidates:
        if added >= MAX_NEW_PER_RUN:
            break
        pid = make_id(e["link"])
        if pid in seen:
            continue

        verdict = evaluate(e)
        if not verdict or not verdict.get("qualifies"):
            continue

        mechs = [m for m in (verdict.get("mechanisms") or []) if m in MECHANISMS]
        flag = verdict.get("flag", "neutral")
        if flag not in ("elegant", "predatory", "neutral"):
            flag = "neutral"

        post = Post(
            id=pid,
            beat="product",
            title_en=verdict.get("title_en", e["title"])[:200],
            title_ar=verdict.get("title_ar", "")[:200],
            summary_en=verdict.get("summary_en", "")[:1200],
            summary_ar=verdict.get("summary_ar", "")[:1200],
            mechanisms=mechs,
            source_url=e["link"],
            source_name=e.get("source_name", "Web"),
            countries=[],
            published_at="",
            discovered_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            verdict=verdict.get("verdict", "")[:200],
            flag=flag,
        )
        if append_post(post):
            log(f"  + [{flag}] {post.title_en}")
            added += 1
            seen.add(pid)

    log(f"done. added {added} posts.")


if __name__ == "__main__":
    run()
