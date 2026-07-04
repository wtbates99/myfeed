#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["feedparser"]
# ///
"""Personal digest: pull RSS sources from feeds.toml, score, cap, render HTML.

Usage: ./digest.py          (writes digest.html next to this file and opens it)
       ./digest.py --no-open
"""

import html
import re
import sys
import time
import tomllib
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import feedparser

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "feeds.toml").read_text())


def fetch(source):
    try:
        parsed = feedparser.parse(source["url"], agent="myfeed/1.0")
        return source, parsed.entries
    except Exception as e:
        print(f"  ! {source['name']}: {e}", file=sys.stderr)
        return source, []


def entry_time(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc) if t else None


def score(title, source, age_hours):
    s = source.get("weight", 0)
    low = title.lower()
    s += 2 * sum(1 for k in CONFIG["boost"] if k in low)
    s -= 5 * sum(1 for k in CONFIG["bury"] if k in low)
    s -= age_hours / 12  # freshness decay: -1 point per 12h
    return s


def collect():
    now = datetime.now(timezone.utc)
    items, seen = [], set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(fetch, CONFIG["source"])
    for source, entries in results:
        for e in entries:
            title = (e.get("title") or "").strip()
            link = e.get("link")
            if not title or not link:
                continue
            key = re.sub(r"\W+", "", title.lower())[:60]
            if key in seen:
                continue
            when = entry_time(e)
            age = (now - when).total_seconds() / 3600 if when else 24
            if age > CONFIG["max_age_hours"]:
                continue
            seen.add(key)
            items.append({
                "title": title, "link": link, "source": source["name"],
                "topic": source["topic"], "when": when,
                "score": score(title, source, age),
            })
    return items


def pick(items):
    """Top N overall; topics that missed the cut get one slot, but only if
    their best item scores >= 1 — quiet topics shouldn't force stale filler."""
    items.sort(key=lambda i: i["score"], reverse=True)
    n = CONFIG["max_items"]
    picked = items[:n]
    have = {i["topic"] for i in picked}
    backfills = []
    for topic in {i["topic"] for i in items} - have:
        best = next(i for i in items if i["topic"] == topic)
        if best["score"] >= 1:
            backfills.append(best)
    if backfills:
        picked = picked[: n - len(backfills)] + backfills
        picked.sort(key=lambda i: i["score"], reverse=True)
    return picked


def render(picked):
    rows = []
    for topic in dict.fromkeys(i["topic"] for i in picked):
        rows.append(f'<h2>{html.escape(topic)}</h2>')
        for i in (x for x in picked if x["topic"] == topic):
            ago = ""
            if i["when"]:
                h = (datetime.now(timezone.utc) - i["when"]).total_seconds() / 3600
                ago = f"{h/24:.0f}d ago" if h >= 24 else f"{h:.0f}h ago"
            rows.append(
                f'<p><a href="{html.escape(i["link"])}">{html.escape(i["title"])}</a>'
                f'<span class="meta">{html.escape(i["source"])} · {ago}</span></p>'
            )
    body = "\n".join(rows)
    date = datetime.now().strftime("%A, %B %-d")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Digest — {date}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 18px/1.6 Georgia, serif; max-width: 44rem; margin: 3rem auto; padding: 0 1rem; }}
  h1 {{ font-size: 1.4rem; }} h1 small {{ color: gray; font-weight: normal; }}
  h2 {{ font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.1em; color: gray; margin-top: 2.5rem; }}
  a {{ color: inherit; }} a:visited {{ color: gray; }}
  .meta {{ display: block; font-size: 0.8rem; color: gray; }}
  footer {{ margin-top: 3rem; color: gray; font-size: 0.85rem; border-top: 1px solid gray; padding-top: 1rem; }}
</style></head><body>
<h1>Digest <small>{date} · {len(picked)} items</small></h1>
{body}
<footer>That's everything. Go do something else.</footer>
</body></html>"""


def main():
    items = collect()
    if not items:
        sys.exit("No items fetched — check your network or feeds.toml URLs.")
    picked = pick(items)
    out = HERE / "digest.html"
    out.write_text(render(picked))
    print(f"Wrote {out} ({len(picked)} items from {len(items)} candidates)")
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
