#!/bin/bash
# Publish the notebooks that are not up yet, waiting out Kaggle's write quota.
#
#   ./notebooks/publish_when_allowed.sh 16 17
#
# WHAT THE QUOTA ACTUALLY DOES, measured over two days rather than guessed:
#
# It is a **daily allowance**, not a short cooldown. Once spent, every
# `SaveKernel` answers 429 for many hours — a full day of hourly retries got
# nothing. When it resets you get a burst: eleven notebooks went up inside one
# window, and then it refused again immediately.
#
# So an intermediate reading — "about three pushes, then it clears in minutes" —
# was wrong, and worth writing down because it is the reading the first hour of
# evidence supports. Three did go through, and the rest went through later in the
# *same* open window rather than after a cooldown.
#
# Two consequences for anyone running this:
#
#   * Retrying faster does not help and may spend the allowance on failures.
#   * `kaggle kernels list` keeps working while saves are refused, so a 429 here
#     is never an auth problem. Check with `kaggle kernels status <user>/<slug>`,
#     which answers about one kernel exactly — `kernels list` paginates
#     unreliably on a large account and reported eleven live notebooks as
#     missing.
#
# RUN IT SOMEWHERE THAT SURVIVES. A backgrounded shell loop was killed four
# times out of four in an agent session, so if you want this unattended, hand it
# to the process supervisor instead:
#
#   systemd-run --user --on-calendar=hourly --unit=bg-publish \
#       "$PWD/notebooks/publish_when_allowed.sh" 16 17
#
# or a crontab line:
#
#   17 * * * * cd /path/to/browsergraph && ./notebooks/publish_when_allowed.sh 16 17
#
# Neither is installed by this script. Starting a recurring job on somebody's
# machine is not a thing a publishing helper should do without being asked.
cd "$(dirname "$0")/.." || exit 1
PENDING=("$@")
[ ${#PENDING[@]} -eq 0 ] && PENDING=(15 16 17 18 19 20 21 22)
GAP=${PUBLISH_GAP:-300}
ROUNDS=${PUBLISH_ROUNDS:-12}

for round in $(seq 1 "$ROUNDS"); do
  still=()
  for nb in "${PENDING[@]}"; do
    out=$(timeout 300 .venv/bin/python notebooks/publish_kaggle.py --push "$nb" 2>&1)
    if echo "$out" | grep -q "successfully pushed"; then
      echo "round $round: $nb published"
    else
      still+=("$nb")
      # Kaggle also caps how many kernels may be *running* at once; that is a
      # different limit from the write quota and clears much faster.
      echo "$out" | grep -qi "429\|Too Many" && break
    fi
    sleep 20
  done
  PENDING=("${still[@]}")
  if [ ${#PENDING[@]} -eq 0 ]; then
    echo "all published after $round round(s)"
    exit 0
  fi
  echo "round $round: still pending -> ${PENDING[*]}"
  sleep "$GAP"
done
echo "gave up with these still pending: ${PENDING[*]}"
exit 1
