#!/usr/bin/env bash
# Print ONE feature entry from feature_list.json instead of reading the whole file (token saver).
# Without an ID, the next feature from next_feature.sh is shown.
# Also prints the overall progress, the dependencies and the features that depend on this one.
# Usage: scripts/show_feature.sh [ID]
set -euo pipefail
cd "$(dirname "$0")/.."
id="${1:-}"
if [[ -z "$id" ]]; then
  id="$(scripts/next_feature.sh 1 | cut -f2)"
  if [[ -z "$id" ]]; then
    echo "Kein offener Feature-Kandidat (alle erledigt oder Abhängigkeiten offen)." >&2
    exit 1
  fi
fi
jq -r --arg id "$id" '
  (map(select(.id == $id)) | first) as $f
  | if $f == null then error("Feature \($id) nicht in feature_list.json") else . end
  | (map(select(.passes)) | length) as $done
  | (map({key: .id, value: .}) | from_entries) as $by_id
  | "Fortschritt: \($done)/\(length) Features bestanden\n",
    ($f | "Feature: \(.id) – \(.title)\nPriorität: \(.priority) | passes: \(.passes)\n\n\(.description)\n"),
    "Abhängigkeiten:",
    (if ($f.depends_on | length) == 0 then "  –"
     else ($f.depends_on[] | "  - \(.) – \($by_id[.].title // "UNBEKANNT") (passes: \($by_id[.].passes // false))") end),
    "\nWird gebraucht von (Schnittstelle passend auslegen, Details: scripts/show_feature.sh <ID>):",
    ([.[] | select(.depends_on | index($f.id))] as $users
     | if ($users | length) == 0 then "  –"
       else ($users[] | "  - \(.id) – \(.title)") end),
    "\nAcceptance:",
    ($f.acceptance[] | "  - [ ] \(.)")
' feature_list.json
