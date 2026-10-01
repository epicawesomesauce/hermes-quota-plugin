#!/bin/bash
# Does uninstall.sh's ownership check survive a relative or dot-dot symlink
# pointing at the same installed tree?
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$REPO/uninstall.sh" ] || REPO=/home/thileman/.hermes/cache/scratch/work

FAKEBIN="$(mktemp -d)"
printf '%s\n' '#!/bin/sh' 'key=""; prev=""' \
  'for a in "$@"; do if [ "$prev" = "get" ]; then key="$a"; fi; prev="$a"; done' \
  'case "$*" in' '  *"config get"*) echo "Config key not set: $key"; exit 1 ;;' \
  '  *) exit 0 ;;' 'esac' > "$FAKEBIN/hermes"
chmod +x "$FAKEBIN/hermes"
export PATH="$FAKEBIN:$PATH"

H="$(mktemp -d)"
LINK="$H/profiles/alpha/plugins/quota"      # the one path the loop inspects

FOREIGN="$FAKEBIN/devco-op"

run_case() {
  local label="$1" target="$2" expect="$3"   # expect: removed | preserved
  # mkdir the PARENT of $LINK only: creating $LINK itself would make it a
  # directory and ln -s would put the link inside it.
  rm -rf "$H"; mkdir -p "$(dirname "$LINK")" "$H/plugins/quota" "$H/desktop-plugins/quota"
  # The foreign target lives inside FAKEBIN, not $H: run_case wipes $H.
  mkdir -p "$FOREIGN"
  case "$target" in /tmp/devco-op) target="$FOREIGN";; esac
  ln -s "$target" "$LINK"
  ( cd "$REPO" && HERMES_HOME="$H" ./uninstall.sh >/dev/null 2>&1 )
  local got
  if [ -L "$LINK" ]; then got="preserved"; else got="removed"; fi
  if [ "$got" = "$expect" ]; then
    echo "  PASS  $label -> $got"
  else
    echo "  FAIL  $label -> $got (expected $expect)"
  fi
}

echo "  installed tree: $H/plugins/quota"
run_case "absolute link to our tree      " "$H/plugins/quota"        removed
run_case "relative link to our tree      " "../../../plugins/quota" removed
run_case "dot-dot link to our tree      " "$H/profiles/alpha/../../plugins/quota" removed
run_case "link to a foreign dev checkout" "/tmp/devco-op"           preserved
run_case "dangling link elsewhere        " "$FAKEBIN/not-here"       preserved

rm -rf "$H" "$FAKEBIN"