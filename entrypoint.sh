#!/bin/sh
# Regenerate the digest every 3 hours in the background; serve in the foreground.
(
  while true; do
    python digest.py --web --no-open || echo "digest generation failed" >&2
    sleep 10800
  done
) &
exec python serve.py 8484
