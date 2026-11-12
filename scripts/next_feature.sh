#!/usr/bin/env bash
# Print the next candidate features: open, all depends_on passed, sorted by priority
# (stable, so ties keep file order). First line = the feature to work on.
# Usage: scripts/next_feature.sh [N]   (default 3)
set -euo pipefail
cd "$(dirname "$0")/.."
jq -r --argjson n "${1:-3}" '
  (map(select(.passes) | .id)) as $done
  | [ .[] | select(.passes | not)
          | select(all(.depends_on[]; . as $d | $done | index($d) != null)) ]
  | sort_by(.priority) | .[0:$n][]
  | "\(.priority)\t\(.id)\t\(.title)"' feature_list.json
