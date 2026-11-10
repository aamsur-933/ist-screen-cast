#!/bin/sh
# udhcpc hook: hanya pasang IP di interface P2P.
# Sengaja TIDAK mengubah default route / DNS supaya internet tetap lewat WiFi biasa.
case "$1" in
  deconfig)
    ip -4 addr flush dev "$interface"
    ;;
  bound|renew)
    ip -4 addr flush dev "$interface"
    ip -4 addr add "$ip/${mask:-24}" dev "$interface"
    echo "$ip ${router:-} ${serverid:-}" > /run/p2p-cast.dhcp
    ;;
esac
exit 0
