#!/bin/bash
# Publish the notebooks that are not up yet, waiting out Kaggle's write quota.
#
# Kaggle answers 429 to every kernel save once you have pushed enough in a day,
# and it stays that way for a while. Retrying in a tight loop just burns the
# allowance, so this waits several minutes between rounds and stops as soon as
# everything is up.
#
#   ./notebooks/publish_when_allowed.sh 15 16 17 18 19 20 21 22
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
