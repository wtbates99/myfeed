#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["feedparser"]
# ///
"""Personal digest: pull RSS sources from feeds.toml, score, cap, render HTML.

Usage: ./digest.py          (writes digest.html next to this file and opens it)
       ./digest.py --no-open
"""

import calendar
import html
import json
import re
import sys
import tomllib
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import feedparser

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "feeds.toml").read_text())


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


MARKET_SYMBOLS = [
    ("S&P", "^GSPC"), ("NASDAQ", "^IXIC"), ("DOW", "^DJI"),
    ("10-YR", "^TNX"), ("BTC", "BTC-USD"),
]


def markets():
    """One-line market snapshot: price + % change vs previous close."""
    parts = []
    for label, sym in MARKET_SYMBOLS:
        try:
            d = get_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                         f"{urllib.parse.quote(sym)}?range=5d&interval=1d")
            res = d["chart"]["result"][0]
            price = res["meta"]["regularMarketPrice"]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c]
            # closes[-1] is the current/most recent session, so the reference
            # for daily change is the close before it
            pct = (price / closes[-2] - 1) * 100
            arrow = "▲" if pct >= 0 else "▼"
            cls = "up" if pct >= 0 else "down"
            shown = f"{price:.2f}%" if label == "10-YR" else f"{price:,.0f}"
            parts.append(f'{label} {shown} <span class="{cls}">{arrow}{abs(pct):.1f}%</span>')
        except Exception:
            continue
    return " · ".join(parts)


TEAMS = [
    ("SOX", "baseball/mlb/teams/bos"), ("CELTICS", "basketball/nba/teams/bos"),
]


def teams():
    """Record + next game for your teams via ESPN's public API."""
    parts = []
    for label, path in TEAMS:
        try:
            t = get_json(f"https://site.api.espn.com/apis/site/v2/sports/{path}")["team"]
            bit = f"{label} {t['record']['items'][0]['summary']}"
            ne = t.get("nextEvent")
            if ne:
                when = datetime.fromisoformat(ne[0]["date"].replace("Z", "+00:00")).astimezone()
                bit += f" · next: {ne[0]['shortName']} {when.strftime('%a %-I:%M%p')}"
            parts.append(bit)
        except Exception:
            continue
    return " ··· ".join(parts)


def fetch(source):
    try:
        parsed = feedparser.parse(source["url"], agent="myfeed/1.0")
        return source, parsed.entries
    except Exception as e:
        print(f"  ! {source['name']}: {e}", file=sys.stderr)
        return source, []


def entry_time(entry):
    # feedparser normalizes struct_times to UTC, so convert with timegm,
    # not mktime (which would wrongly apply the local timezone).
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc) if t else None


def score(title, source, age_hours, window):
    s = source.get("weight", 0)
    low = title.lower()
    s += 2 * sum(1 for k in CONFIG["boost"] if k in low)
    s -= 5 * sum(1 for k in CONFIG["bury"] if k in low)
    # freshness decay: 0 to -4 across the source's window, so weekly feeds
    # with a long window aren't crushed for being 3 days old
    s -= 4 * age_hours / window
    return s


def collect():
    now = datetime.now(timezone.utc)
    items, seen = [], set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(fetch, CONFIG["source"])
    for source, entries in results:
        window = source.get("max_age_hours", CONFIG["max_age_hours"])
        for e in entries:
            title = (e.get("title") or "").strip()
            link = e.get("link")
            if not title or not link:
                continue
            key = re.sub(r"\W+", "", title.lower())[:60]
            if key in seen:
                continue
            when = entry_time(e)
            # clamp to 0: some feeds have slight clock skew into the future
            age = max(0, (now - when).total_seconds() / 3600) if when else 24
            if age > window:
                continue
            seen.add(key)
            items.append({
                "title": title, "link": link, "source": source["name"],
                "topic": source["topic"], "when": when,
                "score": score(title, source, age, window),
            })
    return items


def pick(items):
    """Greedy top-N with per-source and per-topic caps so no feed hogs the
    digest. Topics that still missed the cut get one slot if their best item
    scores >= 0 — but truly stale filler stays out."""
    items.sort(key=lambda i: i["score"], reverse=True)
    n = CONFIG["max_items"]
    per_source, per_topic = CONFIG["max_per_source"], CONFIG["max_per_topic"]
    picked, src_n, top_n = [], {}, {}
    for i in items:
        if len(picked) == n:
            break
        if src_n.get(i["source"], 0) >= per_source or top_n.get(i["topic"], 0) >= per_topic:
            continue
        picked.append(i)
        src_n[i["source"]] = src_n.get(i["source"], 0) + 1
        top_n[i["topic"]] = top_n.get(i["topic"], 0) + 1
    backfills = []
    for topic in {i["topic"] for i in items} - set(top_n):
        best = next(i for i in items if i["topic"] == topic)
        if best["score"] >= 0:
            backfills.append(best)
    if backfills:
        picked = picked[: n - len(backfills)] + backfills
        picked.sort(key=lambda i: i["score"], reverse=True)
    return picked


def render(picked, paper):
    # Drudge-style: top-scoring item becomes the screaming lead headline,
    # the rest flow as dense links in three columns grouped by topic.
    lead, rest = picked[0], picked[1:]

    def link(i):
        ago = ""
        if i["when"]:
            h = max(0, (datetime.now(timezone.utc) - i["when"]).total_seconds() / 3600)
            ago = f" <span class='meta'>({h/24:.0f}d)</span>" if h >= 24 else ""
        return f'<p><a href="{html.escape(i["link"])}">{html.escape(i["title"]).upper()}</a>{ago}</p>'

    cols = []
    for topic in dict.fromkeys(i["topic"] for i in rest):
        cols.append(f'<h2>{html.escape(topic).upper()}</h2>')
        cols.extend(link(i) for i in rest if i["topic"] == topic)
    body = "\n".join(cols)
    date = datetime.now().strftime("%A, %B %-d, %Y").upper()
    stamp = datetime.now().strftime("%-I:%M %p")
    ticker = markets()
    ticker_html = f'<div class="ticker">{ticker}</div>' if ticker else ""
    team_line = teams()
    team_html = f'<div class="ticker">{html.escape(team_line, quote=False)}</div>' if team_line else ""
    paper_html = ""
    if paper:
        paper_html = (f'<div class="paper">📄 PAPER OF THE WEEK: '
                      f'<a href="{html.escape(paper["link"])}">{html.escape(paper["title"])}</a></div>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>THE DIGEST — {date}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: "Times New Roman", Times, serif; max-width: 64rem;
          margin: 1.5rem auto; padding: 0 1rem; text-align: center; }}
  .lead {{ margin: 1.5rem auto 0.5rem; max-width: 42rem; }}
  .lead a {{ font-size: 2.2rem; font-weight: bold; line-height: 1.15;
             color: inherit; text-decoration: underline; }}
  .lead .meta {{ display: block; margin-top: 0.3rem; }}
  hr {{ border: none; border-top: 3px double currentColor; margin: 1.5rem 0; }}
  .cols {{ column-count: 3; column-gap: 2.5rem; column-rule: 1px solid gray;
           text-align: left; }}
  @media (max-width: 40rem) {{ .cols {{ column-count: 1; }} }}
  .cols p {{ margin: 0 0 0.55rem; font-size: 1rem; line-height: 1.3;
             break-inside: avoid; }}
  .cols a {{ color: #00c; }} .cols a:visited, .lead a:visited {{ color: #529; }}
  @media (prefers-color-scheme: dark) {{ .cols a {{ color: #8ab4f8; }} }}
  h2 {{ font-size: 0.9rem; font-weight: bold; letter-spacing: 0.12em;
        color: inherit; border-bottom: 2px solid currentColor;
        padding-bottom: 0.15rem; margin: 1.4rem 0 0.5rem; break-after: avoid; }}
  h2:first-child {{ margin-top: 0; }}
  .meta {{ font-size: 0.75rem; color: #444; }}
  header {{ font-size: 0.8rem; letter-spacing: 0.3em; color: #333; font-weight: bold; }}
  @media (prefers-color-scheme: dark) {{
    .meta {{ color: #bbb; }} header {{ color: #ddd; }}
  }}
  .ticker {{ font-size: 0.85rem; margin-top: 0.4rem; }}
  .up {{ color: green; }} .down {{ color: #c00; }}
  @media (prefers-color-scheme: dark) {{ .up {{ color: #7c7; }} .down {{ color: #f88; }} }}
  .paper {{ border: 1px solid gray; max-width: 42rem; margin: 0 auto 1.5rem;
            padding: 0.6rem 1rem; font-size: 0.95rem; }}
  .paper a {{ color: inherit; }}
  footer {{ margin: 2rem 0 1rem; color: #444; font-size: 0.85rem;
            border-top: 1px solid gray; padding-top: 1rem; }}
  @media (prefers-color-scheme: dark) {{ footer {{ color: #bbb; }} }}
</style></head><body>
<header>THE DIGEST ··· {date} ··· GENERATED {stamp}</header>
{ticker_html}
{team_html}
<div class="lead"><a href="{html.escape(lead["link"])}">{html.escape(lead["title"]).upper()}</a>
<span class="meta">{html.escape(lead["source"])}</span></div>
<hr>
{paper_html}
<div class="cols">
{body}
</div>
<footer>THAT'S EVERYTHING. GO DO SOMETHING ELSE.</footer>
</body></html>"""


def main():
    items = collect()
    if not items:
        sys.exit("No items fetched — check your network or feeds.toml URLs.")
    # Pin the best arXiv paper of the week in its own box, out of the main
    # flow. arXiv's RSS only carries each day's announcements, so remember
    # the week's best in a cache file — otherwise weekends would be empty.
    arxiv = [i for i in items if i["source"] == "arXiv cs.LG"]
    paper = max(arxiv, key=lambda i: i["score"], default=None)
    if paper:
        items.remove(paper)
    cache_file = HERE / "paper.json"
    cached = json.loads(cache_file.read_text()) if cache_file.exists() else None
    if cached:
        age_days = (datetime.now(timezone.utc)
                    - datetime.fromisoformat(cached["saved"])).days
        if age_days < 7 and (not paper or paper["score"] <= cached["score"]):
            paper = cached
    if paper and paper is not cached:
        cache_file.write_text(json.dumps({
            "title": paper["title"], "link": paper["link"],
            "score": paper["score"],
            "saved": datetime.now(timezone.utc).isoformat(),
        }))
    picked = pick(items)
    out = HERE / "digest.html"
    out.write_text(render(picked, paper))
    print(f"Wrote {out} ({len(picked)} items from {len(items)} candidates)")
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
