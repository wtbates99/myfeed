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

Besides the ranked links, the page carries a market ticker (S&P, Nasdaq,
Dow, 10-yr, BTC via Yahoo's chart API), your teams' records and next games
(ESPN's public API), and a pinned "paper of the week" — the best-scoring
arXiv paper of the past 7 days, cached in `paper.json` since arXiv's RSS
only carries each day's announcements. All of it degrades gracefully:
if an API is down, that line just doesn't render.

## Deployment

Production runs as a Docker container (see `Dockerfile` + `entrypoint.sh`):
the entrypoint regenerates the digest every 3 hours and `serve.py` serves it,
logging clicks to a mounted `/data` volume. Pushes to `main` build
`ghcr.io/wtbates99/myfeed:latest` via GitHub Actions; watchtower redeploys it,
and Cloudflare Tunnel routes news.palanbates.com to the container.

`serve.py` routes: `/` the digest · `/go` click-logging redirect (feeds the
learned ranker) · `/archive/` the five most recent editions · `/robots.txt`
says go away. Digest generation prunes older archive pages to save disk space.

Alternatively, run it bare on a systemd user timer:

```ini
# ~/.config/systemd/user/myfeed.service
[Unit]
Description=Regenerate myfeed digest
After=network-online.target
[Service]
Type=oneshot
Environment=PATH=%h/.local/bin:/usr/bin:/bin
ExecStart=%h/myfeed/digest.py --no-open

# ~/.config/systemd/user/myfeed.timer
[Unit]
Description=Regenerate myfeed digest every 3 hours
[Timer]
OnCalendar=*-*-* 00/3:00:00
Persistent=true
[Install]
WantedBy=timers.target
```

```sh
systemctl --user daemon-reload && systemctl --user enable --now myfeed.timer
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
