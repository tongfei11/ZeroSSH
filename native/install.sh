#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."

if [[ $(id -u) == 0 ]]; then
  echo '请用运行 APPLaunch 的普通用户执行安装。' >&2
  exit 1
fi
if [[ $(uname -m) != aarch64 || ! -d /usr/share/APPLaunch/applications ]]; then
  echo '请在 CardputerZero 上运行安装脚本。' >&2
  exit 1
fi

sudo apt-get -o DPkg::Lock::Timeout=30 install -y --no-install-recommends \
  nodejs npm libnode-dev build-essential python3-pil python3-pyte python3-websocket fonts-dejavu-core

app="$HOME/.local/share/zero-ssh"
units="$HOME/.config/systemd/user"
install -d "$app/public" "$units"
install -m 0644 package.json package-lock.json "$app/"
(
  cd "$app"
  npm_config_nodedir=/usr npm ci --omit=dev
)
install -m 0644 server.mjs "$app/"
install -m 0644 public/index.html public/style.css public/app.js public/i18n.js "$app/public/"
install -m 0644 native/zero-ssh.service native/zero-ssh.socket "$units/"

sudo install -d /usr/local/lib/zero-ssh
sudo install -m 0644 native/zero-ssh.py /usr/local/lib/zero-ssh/
icon=$(mktemp)
trap 'unlink "$icon"' EXIT
python3 - "$icon" <<'PY'
import sys

from PIL import Image, ImageDraw

image = Image.new("RGBA", (64, 64))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((1, 1, 62, 62), radius=13, fill="#20352e")
draw.line(((18, 20), (30, 30), (18, 40)), fill="#87efba", width=5)
draw.line(((36, 40), (48, 40)), fill="#87efba", width=5)
image.save(sys.argv[1], format="PNG")
PY
sudo install -m 0644 "$icon" /usr/local/lib/zero-ssh/zero-ssh.png
sudo install -m 0644 native/zero-ssh.desktop /usr/share/APPLaunch/applications/
systemctl --user daemon-reload
systemctl --user stop zero-ssh.service zero-ssh.socket
systemctl --user enable --now zero-ssh.socket
if systemctl --user is-active --quiet zero-ssh.service; then
  systemctl --user restart zero-ssh.service
fi
curl -fsS --max-time 12 http://127.0.0.1:4173/api/state
printf '\n%s\n' '已安装 Zero SSH。请在 APPLaunch 中打开。'
