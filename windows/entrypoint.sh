#!/usr/bin/env bash
set -euo pipefail
case "${1:-configure}" in
  prepare)
    # Named volumes keep SQLite databases inside Docker's Linux filesystem.
    mkdir -p /configs/qbittorrent/wireguard
    if [ "${VPN_ENABLED:-false}" = true ]; then
      test -s /wireguard/wg0.conf || { echo 'VPN selected but wireguard/wg0.conf is missing.' >&2; exit 1; }
      grep -q '\[Interface\]' /wireguard/wg0.conf
      grep -q '\[Peer\]' /wireguard/wg0.conf
      cp /wireguard/wg0.conf /configs/qbittorrent/wireguard/wg0.conf
      chmod 600 /configs/qbittorrent/wireguard/wg0.conf
    fi
    chown -R 1000:1000 /configs
    ;;
  configure) exec bash /usr/local/bin/configure-stack ;;
  provision) exec python3 /installer/portal/cloudflare_setup.py ;;
  *) exec "$@" ;;
esac
