#!/bin/sh
# Install the live rig's Actions runner on novatest-hl. Run as root, from this
# directory; safe to re-run (it replaces what it installed, and never touches a
# registration that exists).
#
#   sudo sh tests/live/runner/install.sh
#
# It installs gitea-runner (pinned, checked against its sha256) as user
# gitea-runner in host mode, the systemd unit, and the one sudoers rule the
# workflow needs. Registering is a separate, one-time step with a token from
# gitea (README.md); the unit starts once the registration exists.
set -eu

VERSION=5.2.4
SHA256=674145e627e5c879d72c293a08991458cf73c81124e54afdd40cf6995098a01b
URL=https://dl.gitea.com/gitea-runner/$VERSION/gitea-runner-$VERSION-linux-amd64
HOME_DIR=/var/lib/gitea-runner
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

# git: gitea-runner 5 needs it on PATH, even in host mode.
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }

id gitea-runner >/dev/null 2>&1 || \
  useradd --system --create-home --home-dir "$HOME_DIR" --shell /usr/sbin/nologin gitea-runner
install -d -o gitea-runner -g gitea-runner -m 750 "$HOME_DIR" "$HOME_DIR/work"

if ! echo "$SHA256  /usr/local/bin/gitea-runner" | sha256sum -c --status 2>/dev/null; then
  tmp=$(mktemp)
  curl -fsSL -o "$tmp" "$URL"
  echo "$SHA256  $tmp" | sha256sum -c -
  install -m 755 "$tmp" /usr/local/bin/gitea-runner
  rm -f "$tmp"
fi
/usr/local/bin/gitea-runner --version
# act_runner was renamed gitea-runner in v1.0.0; drop the old binary.
rm -f /usr/local/bin/act_runner

install -d -m 755 /etc/act_runner
install -m 644 "$HERE/config.yaml" /etc/act_runner/config.yaml

# Checked before it goes live: a broken sudoers file locks out sudo entirely.
visudo -cf "$HERE/sudoers"
install -m 440 "$HERE/sudoers" /etc/sudoers.d/gitea-runner

install -m 644 "$HERE/act-runner.service" /etc/systemd/system/act-runner.service
systemctl daemon-reload
systemctl enable act-runner.service

if [ -f "$HOME_DIR/.runner" ]; then
  systemctl restart act-runner.service
  systemctl --no-pager --lines=5 status act-runner.service
else
  echo
  echo "Not registered yet. With a registration token from"
  echo "  https://gitea-hl.taild204f.ts.net/lime/nova-hub/settings/actions/runners"
  echo "run:"
  echo "  sudo -u gitea-runner sh -c 'cd $HOME_DIR && gitea-runner --config /etc/act_runner/config.yaml register --no-interactive \\"
  echo "    --instance https://gitea-hl.taild204f.ts.net --name novatest-hl --labels novatest:host --token <TOKEN>'"
  echo "  sudo systemctl start act-runner.service"
fi
