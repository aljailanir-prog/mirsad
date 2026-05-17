"""
Mirsad — Evidence Agent
=======================

Scans recent preprints and working papers for behavioural-science findings
that have plausible public-policy or consumer-product relevance. Lab-only
demonstrations with no real-world tether get skipped. Each surviving paper
gets translated into an 80-word lay summary anyone can read.

Primary source: arXiv API (well-behaved, no key). The taxonomy is wide on
purpose — economics, computational social science, quantitative biology /
neuroscience, and a behavioural keyword search.
"""

from __future__ import annotations

import os
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from common import (
    MECHANISMS, Post, append_post, ask_json,
    load_posts, log, make_id, with_retry,
)

os.environ.setdefault("MIRSAD_AGENT", "evidence")

ARXIV_API = "https://export.arxiv.org/api/query"

# Each entry: a search query, returning at most 20 most recent.
QUERIES = [
    'all:"behavioural science" OR all:"behavioral science"',
    'all:"nudge" OR all:"choice architecture"',
    'all:"behaviour change" OR all:"behavior change"',
    'all:"default option" AND all:"experiment"',
    'all:"sludge" AND all:"policy"',
]

PER_QUERY = 12
MAX_NEW_PER_RUN = 4


SYSTEM_PROMPT = f"""You are the Evidence beat agent for Mirsad, a public observatory
of behavioural science in the wild.

You read the title + abstract of a recent paper and decide whether the
finding has plausible relevance to public policy, services, or consumer
products. Strictly lab-only or pure-theory work does NOT qualify. Field
experiments, RCTs in real-world settings, large-scale natural experiments,
and findings about real interventions DO qualify.

If it qualifies, write an 80-word LAY summary that a smart non-expert can
read. No jargon. Lead with the practical bottom line.

Tag mechanisms from this taxonomy:
{', '.join(MECHANISMS)}

Output JSON only:
{{
  "qualifies": true | false,
  "title_en": "10–16 word plain-English title",
  "title_ar": "Arabic translation",
  "summary_en": "~80 words. Lay language. Lead with the bottom line. Mention the setting and sample size if known.",
  "summary_ar": "Arabic translation",
  "mechanisms": ["..."],
  "countries": ["where the study was run, [] if not stated"],
  "verdict": "One short BeSci-flavoured line, max 15 words."
}}
"""


def query_arxiv(query: str, max_results: int) -> list[dict]:
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mirsad/0.1"})
        r.raise_for_status()
    except Exception as e:
        log(f"arxiv fetch failed ({query}): {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.content)
    out: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
        link = ""
        for l in entry.findall("atom:link", ns):
            if l.attrib.get("rel") == "alternate":
                link = l.attrib.get("href", "")
                break
        published = (entry.findtext("atom:published", "", ns) or "")[:10]
        out.append({
            "title": title,
            "link": link,
            "summary": summary[:2000],
            "published": published,
            "source_name": "arXiv",
        })
    return out


def evaluate(entry: dict) -> dict | None:
    user = (
        f"Paper title: {entry['title']}\n\n"
        f"Abstract:\n{entry['summary']}\n\n"
        f"arXiv URL: {entry['link']}\n\n"
        f"Return JSON per the schema."
    )
    try:
        return with_retry(lambda: ask_json(SYSTEM_PROMPT, user, max_tokens=1500))
    except Exception as e:
        log(f"LLM eval failed for {entry['link']}: {e}")
        return None


def run() -> None:
    seen = {p["id"] for p in load_posts()}
    added = 0

    # Collect candidates from all queries, dedup on URL
    bag: dict[str, dict] = {}
    for q in QUERIES:
        log(f"querying arXiv — {q}")
        for e in query_arxiv(q, PER_QUERY):
            if e["link"] and e["link"] not in bag:
                bag[e["link"]] = e
    log(f"candidates collected: {len(bag)}")

    for entry in bag.values():
        if added >= MAX_NEW_PER_RUN:
            break
        pid = make_id(entry["link"])
        if pid in seen:
            continue

        verdict = evaluate(entry)
        if not verdict or not verdict.get("qualifies"):
            continue

        mechs = [m for m in (verdict.get("mechanisms") or []) if m in MECHANISMS]

        post = Post(
            id=pid,
            beat="evidence",
            title_en=verdict.get("title_en", entry["title"])[:220],
            title_ar=verdict.get("title_ar", "")[:220],
            summary_en=verdict.get("summary_en", "")[:1200],
            summary_ar=verdict.get("summary_ar", "")[:1200],
            mechanisms=mechs,
            source_url=entry["link"],
            source_name=entry.get("source_name", "arXiv"),
            countries=verdict.get("countries", [])[:5],
            published_at=entry.get("published", "")[:10],
            discovered_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            verdict=verdict.get("verdict", "")[:200],
        )
        if append_post(post):
            log(f"  + {post.title_en}")
            added += 1
            seen.add(pid)

    log(f"done. added {added} posts.")


if __name__ == "__main__":
    run()
