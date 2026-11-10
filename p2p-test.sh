#!/bin/bash
# Uji koneksi Wi-Fi Direct (Miracast) ke projektor Epson langsung via wpa_supplicant,
# melewati NetworkManager, untuk melihat apakah projektor mau membentuk grup.
# Jalankan dengan: sudo bash p2p-test.sh <MAC perangkat>   (MAC terlihat di menu cast.sh)
set -u
PEER="${1:-}"
[ -n "$PEER" ] || { echo "Pakai: sudo bash $0 <MAC perangkat, mis. aa:bb:cc:dd:ee:ff>"; exit 1; }
IF=p2p-dev-wlo1
W() { wpa_cli -i "$IF" "$@"; }

echo "== Aktifkan Wi-Fi Display IE (laptop sebagai Miracast source, port 7236)"
W set wifi_display 1
W wfd_subelem_set 0 000600101c440032

echo "== Mulai log (40 detik)"
journalctl -f -n0 -u wpa_supplicant -o short-precise 2>/dev/null \
  | grep --line-buffered -E 'P2P-|Provision Discovery Response|GO Negotiation (Response|Request|Confirm)|Status: |rejected|GROUP|WPS-|CTRL-EVENT-(CONNECTED|DISCONNECTED|ASSOC-REJECT|SSID-TEMP)|Trying to associate|join' &
LOGPID=$!

echo "== Cari peer"
W p2p_find 8
sleep 6
W p2p_stop_find

echo "== JOIN ke grup yang sudah dijalankan projektor (projektor = Group Owner)"
W p2p_connect "$PEER" pbc join

sleep 33
echo "== Hasil DHCP/IP di interface grup (jika terbentuk):"
ip -4 addr show | grep -A2 'p2p-wlo1' || true
echo "== Selesai, bersihkan"
W p2p_cancel >/dev/null 2>&1
for g in $(ls /sys/class/net | grep '^p2p-wlo1-'); do W p2p_group_remove "$g" >/dev/null 2>&1; done
kill "$LOGPID" 2>/dev/null
echo "== Status interface:"
ip -4 addr show | grep -A2 'p2p-wlo1' || echo "(tidak ada grup P2P aktif)"
