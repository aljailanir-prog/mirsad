"""
Shared utilities for Mirsad agents.

Three agents (policy, product, evidence) run independently on their own
schedules. Each writes to a shared data/posts.json. This module gives them
the bits they have in common: an LLM wrapper, a posts file reader/writer
with deduplication, the behavioural-science mechanism taxonomy, and a
small RSS helper.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import feedparser
import requests
from anthropic import Anthropic

# ----- paths ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT
POSTS_FILE = ROOT / "posts.json"
MAX_POSTS = 500  # cap the feed; older posts roll off

# ----- taxonomy ------------------------------------------------------------

# Behavioural-science mechanisms (EAST + BCT-style + dark-pattern coverage).
# Agents are constrained to pick from this list so the frontend filters
# stay coherent. Order is roughly EAST first, then nudge categories,
# then dark patterns.
MECHANISMS = [
    "default",
    "social_proof",
    "loss_framing",
    "gain_framing",
    "salience",
    "friction_added",
    "friction_removed",
    "commitment_device",
    "implementation_intention",
    "feedback_loop",
    "reminder",
    "incentive",
    "disclosure",
    "choice_architecture",
    "identity_priming",
    "personalisation",
    "gamification",
    "deceptive_pattern",  # umbrella for dark patterns
    "sludge",
]

MECHANISM_LABELS = {
    "default":                 ("Default", "افتراضي"),
    "social_proof":            ("Social proof", "إثبات اجتماعي"),
    "loss_framing":            ("Loss framing", "تأطير الخسارة"),
    "gain_framing":            ("Gain framing", "تأطير المكسب"),
    "salience":                ("Salience", "بروز"),
    "friction_added":          ("Friction added", "إضافة عقبات"),
    "friction_removed":        ("Friction removed", "إزالة عقبات"),
    "commitment_device":       ("Commitment device", "أداة التزام"),
    "implementation_intention":("Implementation intention", "نية تنفيذية"),
    "feedback_loop":           ("Feedback loop", "حلقة تغذية راجعة"),
    "reminder":                ("Reminder", "تذكير"),
    "incentive":               ("Incentive", "حافز"),
    "disclosure":              ("Disclosure", "إفصاح"),
    "choice_architecture":     ("Choice architecture", "هندسة الخيار"),
    "identity_priming":        ("Identity priming", "تنشيط الهوية"),
    "personalisation":         ("Personalisation", "تخصيص"),
    "gamification":            ("Gamification", "تلعيب"),
    "deceptive_pattern":       ("Deceptive pattern", "نمط خادع"),
    "sludge":                  ("Sludge", "احتكاك إداري"),
}

BEATS = {
    "policy":   ("Policy", "السياسات"),
    "product":  ("Product", "المنتجات"),
    "evidence": ("Evidence", "الأدلة"),
}

# ----- post schema ---------------------------------------------------------

@dataclass
class Post:
    id: str
    beat: str                          # policy | product | evidence
    title_en: str
    summary_en: str
    title_ar: str = ""
    summary_ar: str = ""
    mechanisms: list[str] = field(default_factory=list)
    source_url: str = ""
    source_name: str = ""
    countries: list[str] = field(default_factory=list)
    published_at: str = ""             # ISO date if known, else ""
    discovered_at: str = ""            # ISO date when the agent saw it
    verdict: str = ""                  # short BeSci-flavoured one-liner
    flag: str = ""                     # "elegant" | "predatory" | "" (product agent)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_id(source_url: str) -> str:
    """Stable post id derived from the source URL."""
    return hashlib.sha1(source_url.strip().lower().encode()).hexdigest()[:12]


# ----- posts file ---------------------------------------------------------

def load_posts() -> list[dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not POSTS_FILE.exists():
        return []
    try:
        return json.loads(POSTS_FILE.read_text())
    except json.JSONDecodeError:
        return []


def save_posts(posts: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    posts.sort(key=lambda p: p.get("discovered_at", ""), reverse=True)
    posts = posts[:MAX_POSTS]
    POSTS_FILE.write_text(json.dumps(posts, ensure_ascii=False, indent=2))


def already_seen(posts: list[dict[str, Any]], post_id: str) -> bool:
    return any(p.get("id") == post_id for p in posts)


def append_post(post: Post) -> bool:
    """Append a Post to the shared file if not already present.

    Returns True if appended, False if duplicate."""
    posts = load_posts()
    if already_seen(posts, post.id):
        return False
    if not post.discovered_at:
        post.discovered_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    posts.append(post.to_dict())
    save_posts(posts)
    return True


# ----- LLM ----------------------------------------------------------------

# Pick a fast, cheap model by default. Anyone can override via env.
DEFAULT_MODEL = os.environ.get("MIRSAD_MODEL", "claude-haiku-4-5")

_client: Anthropic | None = None


def llm() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Set it as a repo secret in GitHub "
                "Actions or export it locally."
            )
        _client = Anthropic(api_key=key)
    return _client


def ask_json(system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
    """Ask Claude and return parsed JSON. Throws if it can't parse."""
    msg = llm().messages.create(
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    # Strip code fences if Claude wrapped the JSON
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


# ----- RSS helper ---------------------------------------------------------

def fetch_rss(url: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Pull an RSS/Atom feed and return a list of normalised entries."""
    try:
        # Some feeds dislike default UA
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mirsad/0.1 (+https://github.com/)"
        })
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as e:
        log(f"feed fetch failed for {url}: {e}")
        return []

    out: list[dict[str, Any]] = []
    for e in parsed.entries:
        link = getattr(e, "link", "")
        if not link:
            continue
        out.append({
            "title": getattr(e, "title", "").strip(),
            "link": link,
            "summary": _clean(getattr(e, "summary", "") or getattr(e, "description", "")),
            "published": getattr(e, "published", "") or getattr(e, "updated", ""),
        })
    return out


def _clean(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


# ----- logging ------------------------------------------------------------

def log(msg: str) -> None:
    """Print to stderr with an agent tag if available."""
    tag = os.environ.get("MIRSAD_AGENT", "mirsad")
    print(f"[{tag}] {msg}", file=sys.stderr, flush=True)


# ----- backoff ------------------------------------------------------------

def with_retry(fn, attempts: int = 3, base: float = 1.5):
    """Tiny retry wrapper for flaky network calls."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(base ** i)
    raise last  # type: ignore[misc]
