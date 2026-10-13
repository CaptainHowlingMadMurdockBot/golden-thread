#!/usr/bin/env bash
set -euo pipefail

# Build the distributable zip -- the artifact INSTALL.md, ONBOARDING.md and
# golden-thread-docs.md all tell a new machine to unzip.
#
# Two defects this replaces, both found 2026-09-10:
#
#   1. VERSION was the hardcoded string "0.1.0", against a version directory that
#      had not existed for a dozen releases. `set -e` plus a missing directory
#      meant the script could only fail -- but the STALE ZIP IT HAD ALREADY
#      PRODUCED stayed in the repo, dated and plausible, and three documents
#      pointed a new user straight at it. The documented install path for a second
#      machine handed out a build from before nearly everything.
#
#   2. It never copied hooks/. install.sh reads "$SRC/hooks" to populate
#      ~/.claude/golden-thread/hooks, so a zip built by this script could not
#      install the Core-rule enforcement at all -- inject_core_rules.sh,
#      validate_response.sh and guard_session_claims.sh were simply absent. The
#      one tier whose whole purpose is that it never silently fails, silently
#      absent from the artifact strangers install from.
#
# The version is DERIVED, never typed, by the same rule install.sh uses: the
# newest version directory that carries plugin metadata. A constant here is a
# constant that goes stale the next time someone adds a directory and forgets
# this file -- which is exactly what happened.
#
# Since 0.13.0 the PLUGINS are derived too: every plugin dev/plugins.py discovers is
# shipped, rather than a gt + gt-wiki pair named here, so a third plugin cannot be left
# out of the artifact by a script nobody remembered to edit.

cd "$(dirname "$0")"
DIST="golden-thread-plugin"
ZIP="golden-thread-plugin.zip"

# Newest per plugin, sorted numerically per field (dev/plugins.py): a lexical sort puts
# 0.9.4 above 0.10.0 and would start shipping the older release at double digits.
PLUGINS="$(python3 dev/plugins.py list)" || true
grep -q '^golden-thread ' <<<"$PLUGINS" \
  || { echo "✗ no installable gt version directory found"; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
rm -f "$ZIP"

LABEL=""
while read -r plugin version name; do
  mkdir -p "$STAGE/$DIST/$plugin/$version"
  # hooks/ is NOT optional -- see note 2 above.
  for dir in .claude-plugin skills scripts templates commands hooks help; do
    [ -d "$plugin/$version/$dir" ] \
      && cp -R "$plugin/$version/$dir" "$STAGE/$DIST/$plugin/$version/"
  done
  LABEL="${LABEL:+$LABEL, }$name $version"
done <<< "$PLUGINS"

# The gt-all bundle (0.22.0): install.sh --all-modules reads which modules "everything" means
# from bundles/gt-all, so a zip install carries it too.
[ -d bundles ] && cp -R bundles "$STAGE/$DIST/"

# MANIFEST.json is deliberately NOT shipped: install.sh regenerates it against the
# files it actually lays down, so a stale manifest in the zip would make the very
# first component check report drift that does not exist.
find "$STAGE" \( -name '__pycache__' -o -name '*.pyc' -o -name '.DS_Store' \) \
     -exec rm -rf {} + 2>/dev/null || true

# The docs a fresh machine needs in hand before it has a vault to read them from.
for f in install.sh install.cmd selftest.sh README.md INSTALL.md ONBOARDING.md MANUAL.md; do
  [ -f "$f" ] && cp "$f" "$STAGE/$DIST/"
done
chmod +x "$STAGE/$DIST/install.sh" "$STAGE/$DIST/selftest.sh" 2>/dev/null || true

# zip/unzip when present; Python's zipfile otherwise (0.21.0) -- native Windows' Git Bash ships
# neither. The fallback writes the same entries, with each file's mode: *.sh and anything
# executable as 0755 (Windows reports no exec bit), everything else 0644.
zip_tree() {  # $1 = zip to write, $2 = directory to add (relative, from the cwd)
  # GT_PACKAGE_PYZIP=1 forces the fallback (the tests prove it where zip exists).
  if [ -z "${GT_PACKAGE_PYZIP:-}" ] && command -v zip >/dev/null 2>&1; then
    zip -qr "$1" "$2"; return
  fi
  python3 - "$1" "$2" <<'PY'
import os, sys, zipfile
out, top = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(top):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(root, name)
            arc = p.replace(os.sep, "/")
            info = zipfile.ZipInfo.from_file(p, arc)
            ex = name.endswith(".sh") or os.access(p, os.X_OK) and not name.endswith(
                (".md", ".json", ".txt", ".html", ".py"))
            info.external_attr = ((0o100755 if ex else 0o100644) & 0xFFFF) << 16
            with open(p, "rb") as fh:
                zf.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
PY
}
zip_names() {  # $1 = zip: one entry name per line
  if [ -z "${GT_PACKAGE_PYZIP:-}" ] && command -v unzip >/dev/null 2>&1; then
    unzip -l "$1" | awk '$4 != "" && $4 != "Name" && $4 != "----" {print $4}'
  else
    python3 -c 'import sys, zipfile; print("\n".join(zipfile.ZipFile(sys.argv[1]).namelist()))' "$1"
  fi
}

(cd "$STAGE" && zip_tree "$OLDPWD/$ZIP" "$DIST")

echo "Created $ZIP  ($LABEL)"
# State what a consumer will find, so a wrong build is visible here rather than on
# someone else's machine after they follow INSTALL.md.
zip_names "$ZIP" | grep -E "^$DIST/[^/]+/[0-9]+[.][0-9]+[.][0-9]+/" \
  | sed -E "s#^($DIST/[^/]+/[0-9.]+)/.*#\1#" | sort -u | sed 's/^/  ships /'
