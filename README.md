# myfeed

A personal, finite news digest. One script, one config file, no accounts,
no algorithm feeding you rage, no infinite scroll — the page literally ends.

I got tired of Google/Apple News missing what I care about and Reddit/X/YouTube
eating my time, so this pulls the RSS feeds I actually want, scores every
headline against my interests, and renders the top ~18 items from the last
48 hours as a plain HTML page. Read it with coffee, close the tab, done.

## Usage

Requires [uv](https://docs.astral.sh/uv/) — the script declares its own
dependency (feedparser) inline.

```sh
./digest.py            # fetch, rank, write digest.html, open in browser
./digest.py --no-open  # just write the file
```

## How it works

Everything lives in `feeds.toml`:

- **`[[source]]`** — RSS/Atom feeds, each with a `topic` (for grouping) and a
  `weight` (per-source score bump). Negative weights are useful for firehoses:
  the arXiv cs.LG feed is wired in at `weight = -1`, so papers only surface
  when their titles hit boost keywords.
- **`boost`** — keywords that raise a headline's score (+2 each). This is the
  personalization dial: your teams, your stack, your genres.
- **`bury`** — keywords that sink a headline (−5 each). Clickbait, betting
  odds, "best CD rates today" filler.
- **`max_items` / `max_age_hours`** — the hard cap and freshness window.

Scoring is transparent: `source weight + boost hits − bury hits − age/12h`.
Each topic gets at least one slot if it has something scoring well; quiet
topics just sit out rather than forcing stale filler.

Fork it, gut my `feeds.toml`, put your own obsessions in.
