#!/usr/bin/env bash
# Verify uninstall ownership checks for absolute and relative symlink targets.
set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
[ -f "$REPO/uninstall.sh" ] || {
  echo "Refusing to test: uninstall.sh is missing." >&2
  exit 2
}
: "${HERMES_TEST_SCRATCH:?Set HERMES_TEST_SCRATCH to a dedicated scratch directory.}"
case "$HERMES_TEST_SCRATCH" in
  /*) ;;
  *) echo "Refusing to test: HERMES_TEST_SCRATCH must be an absolute path." >&2; exit 2 ;;
esac
[ "$HERMES_TEST_SCRATCH" != "/" ] || {
  echo "Refusing to test: HERMES_TEST_SCRATCH cannot be /." >&2
  exit 2
}
case "$HERMES_TEST_SCRATCH" in
  "$HOME/.hermes"|"$HOME/.hermes/plugins"*|"$HOME/.hermes/desktop-plugins"*|"$HOME/.hermes/profiles"*)
    echo "Refusing to test inside active Hermes plugins or profiles." >&2
    exit 2
    ;;
esac
mkdir -p "$HERMES_TEST_SCRATCH"
TEST_SCRATCH="$(cd "$HERMES_TEST_SCRATCH" && pwd -P)"
case "$TEST_SCRATCH" in
  "$HOME/.hermes"|"$HOME/.hermes/plugins"|"$HOME/.hermes/plugins/"*|\
  "$HOME/.hermes/desktop-plugins"|"$HOME/.hermes/desktop-plugins/"*|\
  "$HOME/.hermes/profiles"|"$HOME/.hermes/profiles/"*)
    echo "Refusing to test inside active Hermes plugins or profiles." >&2
    exit 2
    ;;
esac

RUN_ROOT="$(mktemp -d "$TEST_SCRATCH/quota-symlink-probe.XXXXXX")"
trap 'rm -rf "$RUN_ROOT"' EXIT
FAKEBIN="$RUN_ROOT/bin"
mkdir -p "$FAKEBIN"
SYSTEM_PATH="${PATH:?PATH must be set}"
REAL_LN="$(command -v ln)"
REAL_READLINK="$(command -v readlink)"

cat > "$FAKEBIN/hermes" <<'FAKE_HERMES'
#!/bin/sh
if [ "${1:-}" = "--quota-shell-test-identity" ]; then
  printf '%s\n' 'quota-shell-test-fake-v1'
  exit 0
fi
key=""
prev=""
for a in "$@"; do
  if [ "$prev" = "get" ]; then key="$a"; fi
  prev="$a"
done
case "$*" in
  *"config get"*) echo "Config key not set: $key"; exit 1 ;;
  *) exit 0 ;;
esac
FAKE_HERMES
chmod +x "$FAKEBIN/hermes"
cat > "$FAKEBIN/readlink" <<'FAKE_READLINK'
#!/bin/sh
if [ "${1:-}" = "-f" ]; then
  : > "$QUOTA_TEST_READLINK_F_MARKER"
  echo "simulated macOS readlink: -f is unsupported" >&2
  exit 1
fi
exec "$QUOTA_TEST_REAL_READLINK" "$@"
FAKE_READLINK
chmod +x "$FAKEBIN/readlink"
export QUOTA_TEST_REAL_READLINK="$REAL_READLINK"
export QUOTA_TEST_READLINK_F_MARKER="$RUN_ROOT/readlink-f-called"
export PATH="$FAKEBIN:$SYSTEM_PATH"

fail_closed() {
  echo "FATAL: $*" >&2
  exit 2
}
assert_fake_launcher() {
  local resolved identity
  resolved="$(command -v hermes || true)"
  [ "$resolved" = "$FAKEBIN/hermes" ] ||
    fail_closed "fake hermes is not first on PATH (found: ${resolved:-none})"
  identity="$(hermes --quota-shell-test-identity)"
  [ "$identity" = "quota-shell-test-fake-v1" ] ||
    fail_closed "fake hermes identity check failed"
}
assert_safe_home() {
  local home="$1"
  [ -n "$home" ] || fail_closed "HERMES_HOME cannot be empty"
  case "$home" in
    "$RUN_ROOT"/home) ;;
    *) fail_closed "HERMES_HOME is outside this run's scratch directory: $home" ;;
  esac
  [ -d "$home" ] || fail_closed "throwaway HERMES_HOME does not exist: $home"
}

H="$RUN_ROOT/home"
FOREIGN="$RUN_ROOT/dev-checkout"
mkdir -p "$H/profiles/alpha" "$FOREIGN"
PASS=0
FAIL=0
ok()  { printf '  PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }

assert_fake_launcher

run_case() {
  local label="$1" target="$2" expect="$3" link_rel="$4"
  local link="$H/profiles/alpha/$link_rel" log="$RUN_ROOT/uninstall.log" rc=0 got
  rm -rf "$H"
  mkdir -p "$H/profiles/alpha/$(dirname "$link_rel")" \
    "$H/plugins/quota" "$H/desktop-plugins/quota" "$FOREIGN"
  case "$target" in
    __FOREIGN__) target="$FOREIGN" ;;
    __DANGLING__) target="$FOREIGN/not-present" ;;
  esac
  "$REAL_LN" -s "$target" "$link"
  assert_fake_launcher
  assert_safe_home "$H"
  if (
    cd "$REPO"
    export PATH="$FAKEBIN:$SYSTEM_PATH"
    export HERMES_HOME="$H"
    ./uninstall.sh
  ) > "$log" 2>&1; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    bad "$label (uninstall exit $rc)"
    cat "$log"
    return
  fi
  if [ -L "$link" ]; then got="preserved"; else got="removed"; fi
  if [ "$got" = "$expect" ]; then
    ok "$label -> $got"
  else
    bad "$label -> $got (expected $expect)"
    cat "$log"
  fi
  if grep -q "Quota uninstall failed" "$log"; then
    bad "$label entered ERR rollback"
  fi
}

printf '%s\n' "  isolated home: $H"
run_case "absolute link to installed backend" "$H/plugins/quota" removed "plugins/quota"
run_case "relative link to installed backend" "../../../plugins/quota" removed "plugins/quota"
run_case "dot-dot link to installed backend" "$H/profiles/alpha/../../plugins/quota" removed "plugins/quota"
run_case "absolute link to installed widget" "$H/desktop-plugins/quota" removed "desktop-plugins/quota"
run_case "foreign development symlink" __FOREIGN__ preserved "plugins/quota"
run_case "dangling link elsewhere" __DANGLING__ preserved "plugins/quota"
[ ! -e "$QUOTA_TEST_READLINK_F_MARKER" ] && ok "portable resolver avoids readlink -f" || bad "uninstall called unsupported readlink -f"
printf '\n=== %s passed, %s failed ===\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
