#!/bin/bash
# Cast desktop ke perangkat Google Cast (Android TV / Google TV / Chromecast) lewat WiFi biasa.
#   sudo bash gcast.sh                          # pindai, pilih perangkat dari menu
#   sudo bash gcast.sh --device "Living Room TV"
#   sudo bash gcast.sh --virtual                # extend (monitor virtual) bukan mirror
#   sudo bash gcast.sh --list                   # hanya tampilkan perangkat
#   sudo bash gcast.sh --device "Living Room TV" --status-only   # uji koneksi tanpa membuka app di TV
# sudo hanya dipakai untuk membuka port HTTP stream di firewall selama sesi (dibersihkan saat Ctrl+C).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
U="${SUDO_USER:-}"
[ "$(id -u)" -eq 0 ] && [ -n "$U" ] || { echo "Jalankan dengan: sudo bash $0"; exit 1; }
UIDN=$(id -u "$U")
PORT=8099
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do [ "${ARGS[$i]}" = --port ] && PORT="${ARGS[$((i + 1))]:-$PORT}"; done
FW_RULE=(INPUT -p tcp --dport "$PORT" -j ACCEPT)

cleanup() {
  trap - EXIT INT TERM
  iptables -D "${FW_RULE[@]}" 2>/dev/null && echo "Port $PORT ditutup lagi."
  # suara laptop kembali normal walau gcast.py tidak berhenti rapi (sink audio sementara dihapus)
  timeout 10 sudo -u "$U" env XDG_RUNTIME_DIR="/run/user/$UIDN" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UIDN/bus" \
    python3 -I "$DIR/gcast.py" --restore-audio </dev/null
  stty sane 2>/dev/null
}
trap cleanup EXIT
trap ':' INT TERM   # Ctrl+C ditangani gcast.py (STOP app di TV), lalu cleanup di sini

iptables -C "${FW_RULE[@]}" 2>/dev/null || iptables -I "${FW_RULE[@]}"
echo "Port $PORT dibuka sementara untuk stream ke TV."
sudo -u "$U" env XDG_RUNTIME_DIR="/run/user/$UIDN" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UIDN/bus" \
  python3 -I -u "$DIR/gcast.py" "${ARGS[@]}"
