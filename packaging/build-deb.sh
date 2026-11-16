#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODULES=${ZERO_SSH_NODE_MODULES:-$ROOT/node_modules}
if [ "${1:-}" = --node-modules ]; then MODULES=$2; shift 2; fi
[ "$#" -eq 0 ] || { echo 'Usage: build-deb.sh [--node-modules PATH]' >&2; exit 2; }
[ "$(dpkg --print-architecture)" = arm64 ] || { echo 'Build on the ARM64 device with its Node 20 dependencies.' >&2; exit 1; }
[ "$(node -p process.versions.modules)" = 115 ] || { echo 'Node 20 (ABI 115) is required.' >&2; exit 1; }
MODULES=$(CDPATH= cd -- "$MODULES" && pwd)
node -e 'require(process.argv[1]);' "$MODULES/node-pty"
VERSION=$(node -p 'require(process.argv[1]).version' "$ROOT/package.json")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
APP=$STAGE/usr/lib/zero-ssh
LAUNCHER=$STAGE/usr/share/APPLaunch
mkdir -p "$APP/native" "$STAGE/DEBIAN" "$LAUNCHER/applications" "$LAUNCHER/share/images" "$STAGE/usr/share/doc/zero-ssh" "$ROOT/dist"
cp "$ROOT/server.mjs" "$ROOT/package.json" "$ROOT/package-lock.json" "$APP/"
cp -R "$ROOT/public" "$APP/public"
python3 - "$MODULES" "$APP/node_modules" <<'PY'
from pathlib import Path
import shutil
import sys

source, destination = map(Path, sys.argv[1:])
files = {
    "node-pty": ["package.json", "LICENSE", "build/Release/pty.node"],
    "ws": ["package.json", "LICENSE", "index.js", "wrapper.mjs", "browser.js"],
    "@xterm/xterm": ["package.json", "LICENSE", "lib/xterm.js", "css/xterm.css"],
    "@xterm/addon-fit": ["package.json", "LICENSE", "lib/addon-fit.js"],
}
for package, names in files.items():
    if package in ("node-pty", "ws"):
        names += [str(path.relative_to(source / package)) for path in (source / package / "lib").glob("*.js")
                  if not path.name.endswith(".test.js")]
    for name in names:
        target = destination / package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / package / name, target)
PY
node -e 'require(process.argv[1]); require(process.argv[2]);' "$APP/node_modules/node-pty" "$APP/node_modules/ws"
cp "$ROOT/native/zero-ssh.py" "$APP/native/"
cp "$ROOT/packaging/zero-ssh.png" "$LAUNCHER/share/images/"
cp "$ROOT/packaging/zero-ssh.desktop" "$LAUNCHER/applications/"
{
    cat "$ROOT/packaging/copyright"
    printf '\n'
    cat "$ROOT/LICENSE"
} > "$STAGE/usr/share/doc/zero-ssh/copyright"
for script in postinst prerm postrm; do cp "$ROOT/packaging/$script" "$STAGE/DEBIAN/"; chmod 755 "$STAGE/DEBIAN/$script"; done
SIZE=$(du -sk "$STAGE/usr" | awk '{print $1}')
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: zero-ssh
Version: $VERSION
Architecture: arm64
Maintainer: tongfei11 <tongfei11@users.noreply.github.com>
Section: net
Priority: optional
Installed-Size: $SIZE
Depends: nodejs (>= 20), nodejs (<< 21), python3, python3-pil, python3-pyte, python3-websocket, fonts-dejavu-core, fonts-noto-cjk, openssh-client, systemd
Homepage: https://github.com/tongfei11/ZeroSSH
Description: SSH session manager for Cardputer Zero
 Native keyboard interface and local web terminal session manager.
CONTROL
# Dependency licenses stay alongside their packages in node_modules.
dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/zero-ssh_${VERSION}_arm64.deb"
