#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["feedparser"]
# ///
"""Personal digest: pull RSS sources from feeds.toml, score, cap, render HTML.

Usage: ./digest.py                  write digest.html with direct links, open it
       ./digest.py --no-open        just write the file
       ./digest.py --web            render links through /go for click tracking
                                    (production mode behind serve.py)
       ./digest.py --search TERM    grep all candidates, not just the picked ones
"""

import calendar
import html
import json
import math
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
SHOWN_LOG = HERE / "shown.jsonl"
CLICKS_LOG = HERE / "clicks.jsonl"

STOPWORDS = set("""this that with from have will your what when they them then
were been more over which their there about after into just says said could
would should these those against""".split())


def tokens(title):
    return {t for t in re.findall(r"[a-z][a-z']{3,}", title.lower())
            if t not in STOPWORDS}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- masthead data

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
    """Record, last result, and next game for your teams via ESPN's API."""
    parts = []
    for label, path in TEAMS:
        try:
            t = get_json(f"https://site.api.espn.com/apis/site/v2/sports/{path}")["team"]
            bit = f"{label} {t['record']['items'][0]['summary']}"
            try:
                evs = get_json(f"https://site.api.espn.com/apis/site/v2/sports/{path}/schedule")["events"]
                done = [e for e in evs if e["competitions"][0]["status"]["type"]["completed"]]
                comp = done[-1]["competitions"][0]
                us = next(c for c in comp["competitors"] if c["team"]["abbreviation"] == "BOS")
                them = next(c for c in comp["competitors"] if c is not us)
                wl = "W" if us.get("winner") else "L"
                at = "vs" if us["homeAway"] == "home" else "@"
                bit += (f" · last: {wl} {us['score']['displayValue']}-"
                        f"{them['score']['displayValue']} {at} {them['team']['abbreviation']}")
            except Exception:
                pass
            ne = t.get("nextEvent")
            if ne:
                when = datetime.fromisoformat(ne[0]["date"].replace("Z", "+00:00")).astimezone()
                bit += f" · next: {ne[0]['shortName']} {when.strftime('%a %-I:%M%p')}"
            parts.append(bit)
        except Exception:
            continue
    return " ··· ".join(parts)


def econ_line():
    """Upcoming econ releases (CPI/FOMC/jobs) within two weeks."""
    f = HERE / "econ_2026.json"
    if not f.exists():
        return ""
    today = datetime.now().date()
    out = []
    for e in json.loads(f.read_text()):
        d = datetime.fromisoformat(e["date"]).date()
        if 0 <= (d - today).days <= 14:
            out.append(f"{e['label']} {d.strftime('%a %-m/%-d')}")
    return " · ".join(out[:3])


def ham_question():
    """One Technician-pool question per day, answer behind a click."""
    f = HERE / "tech_pool.json"
    if not f.exists():
        return ""
    qs = json.loads(f.read_text())
    q = qs[datetime.now().date().toordinal() % len(qs)]
    choices = "".join(f"<br><b>{k}.</b> {html.escape(v)}"
                      for k, v in sorted(q["choices"].items()))
    return (f'<div class="paper"><b>📻 HAM QUESTION OF THE DAY</b> '
            f'<span class="meta">{q["id"]} · 2026–2030 Technician pool</span><br>'
            f'{html.escape(q["q"])}{choices}'
            f'<details><summary>Answer</summary><b>{q["answer"]}.</b> '
            f'{html.escape(q["choices"][q["answer"]])}</details></div>')


# ---------------------------------------------------------------- collection

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


def learned_weights():
    """Per-token score adjustments learned from your click history:
    tokens you click more often than average float up, ignored ones sink."""
    shown = {d["url"]: d["title"] for d in read_jsonl(SHOWN_LOG)}
    clicked = {d["url"] for d in read_jsonl(CLICKS_LOG)} & set(shown)
    if len(clicked) < 5:
        return {}
    s_ct, c_ct = {}, {}
    for url, title in shown.items():
        for t in tokens(title):
            s_ct[t] = s_ct.get(t, 0) + 1
            if url in clicked:
                c_ct[t] = c_ct.get(t, 0) + 1
    base = len(clicked) / len(shown)
    weights = {}
    for t, s in s_ct.items():
        if s < 5:
            continue
        rate = (c_ct.get(t, 0) + base) / (s + 1)
        weights[t] = max(-1.0, min(1.0, math.log2(rate / base)))
    return weights


def score(title, source, age_hours, window, learned):
    s = source.get("weight", 0)
    low = title.lower()
    s += 2 * sum(1 for k in CONFIG["boost"] if k in low)
    s -= 5 * sum(1 for k in CONFIG["bury"] if k in low)
    # freshness decay: 0 to -4 across the source's window, so weekly feeds
    # with a long window aren't crushed for being 3 days old
    s -= 4 * age_hours / window
    if learned:
        s += max(-3.0, min(3.0, sum(learned.get(t, 0) for t in tokens(title))))
    return s


def collect():
    now = datetime.now(timezone.utc)
    learned = learned_weights()
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
            item = {
                "title": title, "link": link, "source": source["name"],
                "topic": source["topic"], "when": when,
                "score": score(title, source, age, window, learned),
            }
            if source["name"] == "arXiv cs.LG":
                item["blurb"] = re.sub(r"<[^>]+>", " ",
                                       e.get("summary", "")).strip()[:350]
            items.append(item)
    return dedupe_similar(items)


def dedupe_similar(items):
    """One story, one slot: cluster near-duplicate titles across sources,
    keep the best-scoring one, and note the other outlets as 'also'."""
    items.sort(key=lambda i: i["score"], reverse=True)
    kept = []
    for i in items:
        tk = tokens(i["title"])
        for k in kept:
            union = tk | k["_tk"]
            common = tk & k["_tk"]
            if len(common) >= 3 and union and len(common) / len(union) >= 0.5:
                if i["source"] != k["source"] and i["source"] not in k["also"]:
                    k["also"].append(i["source"])
                break
        else:
            i["_tk"], i["also"] = tk, []
            kept.append(i)
    for k in kept:
        del k["_tk"]
    return kept


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


# ---------------------------------------------------------------- render

def href(i, web):
    if not web:
        return html.escape(i["link"])
    return ("/go?u=" + urllib.parse.quote(i["link"], safe="")
            + "&t=" + urllib.parse.quote(i["title"][:120]))


def render(picked, paper, web):
    # Drudge-style: top-scoring item becomes the screaming lead headline,
    # the rest flow as dense links in three columns grouped by topic.
    lead, rest = picked[0], picked[1:]

    def link(i):
        extra = ""
        if i["when"]:
            h = max(0, (datetime.now(timezone.utc) - i["when"]).total_seconds() / 3600)
            if h >= 24:
                extra += f" <span class='meta'>({h/24:.0f}d)</span>"
        if i["also"]:
            extra += f" <span class='meta'>(also: {html.escape(', '.join(i['also']))})</span>"
        cls = ' class="seen"' if i.get("seen") else ""
        return (f'<p{cls}><a href="{href(i, web)}">'
                f'{html.escape(i["title"]).upper()}</a>{extra}</p>')

    cols = []
    for topic in dict.fromkeys(i["topic"] for i in rest):
        cols.append(f'<h2>{html.escape(topic).upper()}</h2>')
        cols.extend(link(i) for i in rest if i["topic"] == topic)
    body = "\n".join(cols)
    date = datetime.now().strftime("%A, %B %-d, %Y").upper()
    stamp = datetime.now().strftime("%-I:%M %p")
    ticker = markets()
    ticker_html = f'<div class="ticker">{ticker}</div>' if ticker else ""
    lines = " ··· ".join(x for x in (teams(), econ_line() and "AHEAD: " + econ_line()) if x)
    team_html = f'<div class="ticker">{html.escape(lines, quote=False)}</div>' if lines else ""
    paper_html = ""
    if paper:
        blurb = paper.get("blurb", "")
        blurb_html = f'<br><span class="meta">{html.escape(blurb)}…</span>' if blurb else ""
        paper_html = (f'<div class="paper"><b>📄 PAPER OF THE WEEK</b>: '
                      f'<a href="{href(paper, web)}">{html.escape(paper["title"])}</a>'
                      f'{blurb_html}</div>')
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
  .cols .seen a {{ color: #66708c; }}
  @media (prefers-color-scheme: dark) {{
    .cols a {{ color: #8ab4f8; }} .cols .seen a {{ color: #77809c; }}
  }}
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
  .paper details {{ margin-top: 0.4rem; }}
  .paper summary {{ cursor: pointer; color: #444; }}
  @media (prefers-color-scheme: dark) {{ .paper summary {{ color: #bbb; }} }}
  footer {{ margin: 2rem 0 1rem; color: #444; font-size: 0.85rem;
            border-top: 1px solid gray; padding-top: 1rem; }}
  @media (prefers-color-scheme: dark) {{ footer {{ color: #bbb; }} }}
  footer a {{ color: inherit; }}
</style></head><body>
<header>THE DIGEST ··· {date} ··· GENERATED {stamp}</header>
{ticker_html}
{team_html}
<div class="lead"><a href="{href(lead, web)}">{html.escape(lead["title"]).upper()}</a>
<span class="meta">{html.escape(lead["source"])}</span></div>
<hr>
{paper_html}
{ham_question()}
<div class="cols">
{body}
</div>
<footer>THAT'S EVERYTHING. GO DO SOMETHING ELSE. · <a href="/archive/">archive</a></footer>
</body></html>"""


# ---------------------------------------------------------------- main

def main():
    if "--search" in sys.argv:
        term = sys.argv[sys.argv.index("--search") + 1].lower()
        hits = [i for i in collect() if term in i["title"].lower()]
        for i in sorted(hits, key=lambda i: -i["score"]):
            print(f'{i["score"]:5.1f} {i["source"]:26} {i["title"]}')
            print(f'      {i["link"]}')
        print(f"{len(hits)} matches")
        return

    web = "--web" in sys.argv
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
            "score": paper["score"], "blurb": paper.get("blurb", ""),
            "also": [], "when": None,
            "saved": datetime.now(timezone.utc).isoformat(),
        }))

    picked = pick(items)

    # anything shown in a previous edition renders dimmed; log new urls
    prior = {d["url"] for d in read_jsonl(SHOWN_LOG)}
    with SHOWN_LOG.open("a") as f:
        for i in picked:
            if i["link"] in prior:
                i["seen"] = True
            else:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "url": i["link"], "title": i["title"], "topic": i["topic"],
                }) + "\n")

    out = HERE / "digest.html"
    page = render(picked, paper, web)
    out.write_text(page)
    archive = HERE / "archive"
    archive.mkdir(exist_ok=True)
    (archive / datetime.now().strftime("%Y-%m-%d-%H%M.html")).write_text(page)
    print(f"Wrote {out} ({len(picked)} items from {len(items)} candidates)")
    if "--no-open" not in sys.argv:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
