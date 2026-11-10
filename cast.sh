#!/bin/bash
# Cast layar Ubuntu ke projektor/TV via Miracast (Wi-Fi Direct), tanpa GNOME Network Displays.
#   sudo bash cast.sh                    # pindai, pilih perangkat dari menu (Enter = LOBBY.A1)
#   sudo bash cast.sh --peer LOBBY.A1    # langsung ke perangkat ini (nama atau MAC), tanpa menu
#   sudo bash cast.sh --extend           # langsung extend (monitor virtual), tanpa menu tampilan
#   sudo bash cast.sh --mirror           # langsung mirror layar utama, tanpa menu tampilan
#   sudo bash cast.sh --mode 1080p30     # resolusi lain: 480p60 720p30 720p60 1080p30
#   sudo bash cast.sh --join | --negotiate   # paksa cara tersambung (default: otomatis)
# Cara tersambung otomatis per perangkat:
#   - perangkat sudah Group Owner (mis. projektor Epson)  -> JOIN ke grupnya
#   - perangkat belum punya grup (mis. TV LG webOS)       -> NEGOSIASI; default laptop minta jadi GO
#     (go_intent=14: TV LG menjawab intent 0 = ingin jadi client; 14, bukan 15, supaya tidak gagal
#     "status 9" dengan perangkat yang juga memaksa intent 15); laptop lalu menjalankan server DHCP.
#   sudo bash cast.sh --go-intent 0      # sebaliknya: minta perangkat yang jadi GO (0..15)
#   sudo bash cast.sh --hw               # coba encoder hardware (VA-API/NVENC), fallback ke openh264
#   sudo bash cast.sh --iface wlp2s0     # interface WiFi tertentu (default: dideteksi otomatis)
#   sudo bash cast.sh --check            # hanya cek kesiapan laptop, tanpa mengubah apa pun
#   sudo bash cast.sh --capture portal   # paksa tangkapan layar lewat xdg-desktop-portal (default: auto)
#   pkexec /bin/bash cast.sh --gui       # dipakai GUI: tanpa menu; perintah dibaca dari stdin (lihat gui_select)
# Hentikan dengan Ctrl+C (semua dibersihkan otomatis).
set -u
DEFAULT_NAME="LOBBY.A1"            # pilihan default di menu (Enter)
PEER="${PEER:-}"; PEER_NAME=""; CONNECT_MODE="auto"; GO_INTENT=14; DISPLAY_MODE=""
WIFI=""; CHECK_ONLY=0; GUI=0
PY_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --peer)      PEER="${2:-}"; shift 2 ;;
    --peer=*)    PEER="${1#--peer=}"; shift ;;
    --join)      CONNECT_MODE="join"; shift ;;
    --negotiate) CONNECT_MODE="negotiate"; shift ;;
    --go-intent) GO_INTENT="${2:-14}"; shift 2 ;;
    --mirror)    DISPLAY_MODE="mirror"; shift ;;
    --extend|--virtual) DISPLAY_MODE="extend"; shift ;;
    --iface)     WIFI="${2:-}"; shift 2 ;;
    --check)     CHECK_ONLY=1; shift ;;
    --gui)       GUI=1; shift ;;
    *)           PY_ARGS+=("$1"); shift ;;
  esac
done
DIR="$(cd "$(dirname "$0")" && pwd)"
U="${SUDO_USER:-}"
# Dijalankan lewat pkexec (GUI): pengguna desktop dari PKEXEC_UID
[ -z "$U" ] && [ -n "${PKEXEC_UID:-}" ] && U=$(id -nu "$PKEXEC_UID" 2>/dev/null)
# --capture ikut diteruskan ke pengecekan dan ke gcast.py
CAP_ARGS=()
for ((i = 0; i < ${#PY_ARGS[@]}; i++)); do
  case "${PY_ARGS[$i]}" in
    --capture) CAP_ARGS+=(--capture "${PY_ARGS[$((i + 1))]:-auto}") ;;
    --capture=*) CAP_ARGS+=("${PY_ARGS[$i]}") ;;
  esac
done
# Baris status untuk GUI (hanya di --gui): @@<kolom>\t<kolom>...
ev() { [ "$GUI" = 1 ] || return 0; local IFS=$'\t'; printf '@@%s\n' "$*"; }
# GUI menutup stdin / mengirim "stop" -> hentikan sesi (GUI berjalan sebagai user, tidak bisa kirim sinyal ke root)
WATCHPID=""
start_stop_watcher() {
  [ "$GUI" = 1 ] || return 0
  ( while IFS= read -r l; do [ "$l" = stop ] && break; done; kill -TERM $$ 2>/dev/null ) >/dev/null 2>&1 &
  WATCHPID=$!
}
# Kalau GUI tertutup, stdout ikut putus: jangan sampai SIGPIPE membatalkan pembersihan
[ "$GUI" = 1 ] && trap '' PIPE
[ "$(id -u)" -eq 0 ] && [ -n "$U" ] || { echo "Jalankan dengan: sudo bash $0"; exit 1; }
UIDN=$(id -u "$U")

# Interface WiFi: --iface, atau WiFi yang sedang tersambung menurut NetworkManager, atau WiFi pertama.
detect_wifi() {
  local d
  d=$(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | awk -F: '$2=="wifi" && $3=="connected"{print $1; exit}')
  [ -n "$d" ] || d=$(nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')
  if [ -z "$d" ]; then
    local x; for x in /sys/class/net/*; do [ -d "$x/wireless" ] && { d=$(basename "$x"); break; }; done
  fi
  echo "$d"
}
[ -n "$WIFI" ] || WIFI=$(detect_wifi)
# Interface kontrol Wi-Fi Direct milik wpa_supplicant: p2p-dev-<wifi> (kalau ada beberapa, yang cocok)
IF="p2p-dev-$WIFI"
if [ ! -S "/run/wpa_supplicant/$IF" ]; then
  alt=$(ls /run/wpa_supplicant 2>/dev/null | grep -m1 '^p2p-dev-' || true)
  [ -n "$alt" ] && IF="$alt"
fi
W() { timeout 5 wpa_cli -i "$IF" "$@" </dev/null; }
GIF=""; PYPID=""; DHPID=""; TAILPID=""; CLEANING=0; GC_RULE=""
DBUS_POLICY=/etc/dbus-1/system.d/zz-p2p-cast-temp.conf
NM_CONF=/etc/NetworkManager/conf.d/99-p2p-manual.conf
# Interface grup P2P bernama p2p-<wifi>-N, atau p2p-N kalau nama WiFi terlalu panjang (batas 15 karakter),
# jadi aturan memakai awalan "p2p-" (p2p-dev-* bukan netdev, tidak terpengaruh).
FW_RULES=("INPUT -i p2p-+ -p tcp --dport 7236 -j ACCEPT"   # RTSP dari perangkat
          "INPUT -i p2p-+ -p udp --dport 67 -j ACCEPT")    # DHCP server (kalau laptop = GO)
GO_NET=192.168.49                   # subnet kalau laptop yang jadi Group Owner
UDHCPD_CONF=/run/p2p-cast-udhcpd.conf

# Hentikan satu process group: TERM, tunggu maks 3 detik, lalu KILL.
stop_group() {
  local pg=$1 i
  kill -TERM -- "-$pg" 2>/dev/null || return 0
  for i in $(seq 30); do kill -0 -- "-$pg" 2>/dev/null || return 0; sleep 0.1; done
  kill -KILL -- "-$pg" 2>/dev/null
}

cleanup() {
  [ "$CLEANING" = 1 ] && return
  CLEANING=1
  # Ctrl+C kedua saat membersihkan = keluar paksa
  trap 'echo; echo "Keluar paksa."; stty sane 2>/dev/null; exit 130' INT
  trap - EXIT TERM
  echo; echo "== Membersihkan (Ctrl+C lagi untuk keluar paksa)"
  [ -n "$PYPID" ] && stop_group "$PYPID"
  [ -n "$DHPID" ] && stop_group "$DHPID"
  [ -n "$TAILPID" ] && stop_group "$TAILPID"
  [ -n "$WATCHPID" ] && kill "$WATCHPID" 2>/dev/null
  W p2p_group_remove '*' >/dev/null 2>&1   # semua grup P2P
  W p2p_cancel >/dev/null 2>&1
  local r
  for r in "${FW_RULES[@]}"; do iptables -D $r 2>/dev/null; done
  [ -n "$GC_RULE" ] && iptables -D $GC_RULE 2>/dev/null
  if [ -f "$DBUS_POLICY" ]; then
    rm -f "$DBUS_POLICY"
    timeout 5 dbus-send --system --type=method_call --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig 2>/dev/null
    echo "Aturan D-Bus sementara dihapus."
  fi
  if [ -f "$NM_CONF" ]; then
    rm -f "$NM_CONF"
    timeout 10 nmcli general reload conf 2>/dev/null
    sleep 2
    if nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^$IF:unavailable"; then
      echo "NetworkManager mengelola Wi-Fi Direct lagi, tapi $IF masih 'unavailable'."
      echo "Untuk memakai GNOME Network Displays, jalankan: sudo systemctl restart NetworkManager"
      echo "(WiFi putus beberapa detik)"
    else
      echo "NetworkManager kembali mengelola Wi-Fi Direct (Network Displays bisa dipakai lagi)."
    fi
  fi
  rm -f /run/p2p-cast.dhcp "$UDHCPD_CONF" /run/p2p-cast-udhcpd.leases
  stty sane 2>/dev/null
  echo "Selesai."
  exit 0
}
# Cek kesiapan laptop SEBELUM sistem diubah. Gagal di sini = tidak ada yang perlu dibersihkan.
preflight() {
  local fatal=0 c pkg missing=() pkgs=()
  ok()  { echo "  OK  $*"; }
  bad() { echo "  XX  $1"; [ -n "${2:-}" ] && echo "        -> $2"; }
  echo "== Cek kesiapan laptop"
  for c in wpa_cli:wpasupplicant nmcli:network-manager iptables:iptables busybox:busybox \
           dbus-send:dbus journalctl:systemd ss:iproute2 setsid:util-linux python3:python3 \
           pactl:pulseaudio-utils; do
    command -v "${c%%:*}" >/dev/null 2>&1 || { missing+=("${c%%:*}"); pkgs+=("${c#*:}"); }
  done
  if [ ${#missing[@]} -gt 0 ]; then
    bad "perintah tidak ada: ${missing[*]}" "sudo apt install ${pkgs[*]}"; fatal=1
  else
    ok "perintah yang dibutuhkan lengkap"
  fi
  if command -v busybox >/dev/null && busybox --list | grep -qx udhcpc && busybox --list | grep -qx udhcpd; then
    ok "busybox udhcpc/udhcpd (DHCP)"
  else
    bad "busybox tanpa udhcpc/udhcpd" "sudo apt install busybox"; fatal=1
  fi
  if [ -z "$WIFI" ]; then
    bad "tidak ada interface WiFi" "sambungkan WiFi, atau pakai --iface <nama>"; fatal=1
  else
    ok "interface WiFi: $WIFI"
  fi
  if ! systemctl is-active --quiet wpa_supplicant; then
    bad "wpa_supplicant tidak berjalan" "Wi-Fi Direct butuh wpa_supplicant (iwd tidak didukung)"; fatal=1
  elif [ "$(timeout 3 wpa_cli -i "$IF" ping 2>/dev/null </dev/null)" = PONG ]; then
    ok "Wi-Fi Direct tersedia ($IF)"
  else
    bad "Wi-Fi Direct tidak tersedia ($IF)" \
        "chip/driver WiFi tidak mendukung P2P, atau wpa_supplicant tanpa P2P. Cek: nmcli device | grep p2p"
    fatal=1
  fi
  if command -v iw >/dev/null 2>&1; then
    if iw list 2>/dev/null | grep -q 'P2P-GO' && iw list 2>/dev/null | grep -q 'P2P-client'; then
      ok "driver mendukung P2P-GO dan P2P-client"
    else
      bad "driver tidak melaporkan P2P-GO/P2P-client (iw list)" "Wi-Fi Direct kemungkinan tidak jalan"
    fi
  fi
  # Sesi desktop pengguna: Wayland dibutuhkan untuk Extend (monitor virtual)
  local s t types=""
  for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk -v u="$U" '$3==u{print $1}'); do
    t=$(loginctl show-session "$s" -p Type --value 2>/dev/null); [ -n "$t" ] && types+="$t "
  done
  case "$types" in
    *wayland*) ok "sesi desktop $U: wayland" ;;
    *x11*)     bad "sesi desktop $U: x11" "Mirror kemungkinan jalan; Extend (monitor virtual) butuh sesi Wayland" ;;
    *)         bad "sesi desktop $U tidak terdeteksi" "jalankan dari sesi desktop yang sedang login" ;;
  esac
  # Python + GStreamer + tangkapan layar (Mutter / xdg-desktop-portal) + audio, dicek sebagai pengguna desktop
  if ! sudo -u "$U" python3 -I -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst' 2>/dev/null; then
    bad "Python GObject / GStreamer introspection tidak ada" \
        "sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0"; fatal=1
  else
    local vargs=(${CAP_ARGS[@]+"${CAP_ARGS[@]}"}) out rc; [ "$DISPLAY_MODE" = extend ] && vargs+=(--virtual)
    out=$(sudo -u "$U" env XDG_RUNTIME_DIR="/run/user/$UIDN" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UIDN/bus" \
          python3 -I "$DIR/wfd_source.py" --check "${vargs[@]}" </dev/null 2>&1); rc=$?
    printf '%s\n' "$out" | grep -v '^\[OpenH264\]' | grep -vE '^[0-9]{2}:[0-9]{2}:[0-9]{2} Audio dari' || true
    [ "$rc" -eq 0 ] || fatal=1
  fi
  return $fatal
}

ev STATE checking
if [ "$CHECK_ONLY" = 1 ]; then
  preflight && { echo "Laptop siap dipakai untuk cast."; exit 0; } || { echo "Ada yang perlu diperbaiki (lihat XX di atas)."; exit 1; }
fi
preflight || { echo "Ada yang perlu diperbaiki (lihat XX di atas). Tidak ada yang diubah di sistem."; exit 1; }

trap cleanup EXIT INT TERM

echo "== 1/6 NetworkManager: abaikan interface Wi-Fi Direct (sementara, dikembalikan saat Ctrl+C)"
if [ ! -f "$NM_CONF" ]; then
  printf '# SEMENTARA, dibuat oleh cast.sh; dihapus otomatis saat cast.sh berhenti.\n# Kalau tertinggal: hapus file ini + "nmcli general reload conf"\n[keyfile]\nunmanaged-devices+=interface-name:p2p-*\n' > "$NM_CONF"
  nmcli general reload conf
  sleep 2
fi
nmcli -t -f DEVICE,STATE device | grep p2p || true

# NetworkManager 1.36 memanggil RemoveInterface untuk grup P2P yang tidak ia buat sendiri
# ("NMSupplicantInterface is disposing"). Blokir panggilan itu selama sesi ini saja.
echo "== 1b/6 D-Bus: blokir sementara wpa_supplicant RemoveInterface"
cat > "$DBUS_POLICY" <<'XML'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<!-- SEMENTARA, dibuat oleh cast.sh; aman dihapus. -->
<busconfig>
  <policy context="mandatory">
    <deny send_destination="fi.w1.wpa_supplicant1"
          send_interface="fi.w1.wpa_supplicant1"
          send_member="RemoveInterface"/>
  </policy>
</busconfig>
XML
timeout 5 dbus-send --system --print-reply --type=method_call --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig >/dev/null \
  && echo "aktif" || echo "PERINGATAN: reload D-Bus gagal"

echo "== 2/6 Firewall: izinkan RTSP (TCP 7236) dan DHCP (UDP 67) di interface P2P (sementara)"
for r in "${FW_RULES[@]}"; do iptables -C $r 2>/dev/null || iptables -I $r; done

echo "== 3/6 Wi-Fi Display IE (laptop = Miracast source, port 7236)"
W set wifi_display 1 >/dev/null
W wfd_subelem_set 0 000600101c440032 >/dev/null

echo "== 3b/6 Pilih perangkat"
# Pindai peer Wi-Fi Direct; hanya yang mengiklankan Wi-Fi Display (Miracast sink) ditampilkan.
# $1 = jumlah putaran (berhenti di putaran pertama yang menemukan perangkat)
scan_miracast() {
NAMES=(); MACS=(); LEVELS=(); CAPS=(); LFREQS=()
local attempt mac info
for attempt in $(seq "${1:-2}"); do
  echo "Memindai perangkat Miracast di sekitar (6 detik)..."
  W p2p_find 6 >/dev/null; sleep 6; W p2p_stop_find >/dev/null
  for mac in $(W p2p_peers); do
    info=$(W p2p_peer "$mac")
    printf '%s\n' "$info" | grep -q '^wfd_subelems=' || continue
    MACS+=("$mac")
    NAMES+=("$(printf '%s\n' "$info" | sed -n 's/^device_name=//p')")
    LEVELS+=("$(printf '%s\n' "$info" | sed -n 's/^level=//p')")
    CAPS+=("$(printf '%s\n' "$info" | sed -n 's/^group_capab=//p')")
    LFREQS+=("$(printf '%s\n' "$info" | sed -n 's/^listen_freq=//p')")
  done
  [ ${#MACS[@]} -gt 0 ] && break
done
}

# group_capab bit 0 = perangkat sedang menjadi Group Owner -> harus JOIN; selain itu NEGOSIASI
how_for() { local c=$(( ${1:-0} )); (( c & 1 )) && echo join || echo negotiate; }

# ---- Mode GUI: pindai Miracast + Google Cast, kirim daftar ke GUI, tunggu perintah di stdin ----
#   rescan                                         pindai ulang
#   cast miracast <mac> <mirror|extend> <mode> [opsi...]   mode: 480p60 | 720p30 | 720p60 | 1080p30
#   cast gcast <ip> <mirror|extend> <mode> [opsi...]
#   opsi (Advanced): how=auto|join|negotiate  go=0..15  hw=0|1  audio=auto|aac|lpcm|off  bitrate=<kbit/s>
#   (--iface dan --capture diberikan saat sesi dimulai)
#   stop (atau stdin ditutup)                      selesai, bersihkan
GUI_SEL=""; GC_EXTRA=()
gui_select() {
  local A kind id disp mode i gtmp gpid n m ip port opt k v bad how go extra gextra scan=1
  gerr() { echo "$*"; ev ERROR "$*"; }
  while :; do
    if [ "$scan" = 1 ]; then
    scan=0
    ev STATE scanning
    echo "Memindai perangkat Miracast (Wi-Fi Direct) dan Google Cast (WiFi)..."
    gtmp=$(mktemp)
    sudo -u "$U" python3 -I "$DIR/gcast.py" --list --tsv </dev/null >"$gtmp" 2>/dev/null &
    gpid=$!
    scan_miracast 1
    wait "$gpid" 2>/dev/null
    for i in "${!MACS[@]}"; do
      ev DEV miracast "${MACS[$i]}" "${NAMES[$i]}" "${LEVELS[$i]:-?}" "$(how_for "${CAPS[$i]}")"
    done
    GC_IPS=()
    while IFS=$'\t' read -r n m ip port; do
      [[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || continue
      GC_IPS+=("$ip"); ev DEV gcast "$ip" "$n" "$m" "$port"
    done < "$gtmp"
    rm -f "$gtmp"
    echo "Ditemukan: ${#MACS[@]} Miracast, ${#GC_IPS[@]} Google Cast"
    fi
    ev STATE ready
    IFS=' ' read -r -a A || exit 0
    case "${A[0]:-}" in
      rescan) scan=1; continue ;;
      stop)   exit 0 ;;
      cast)   kind="${A[1]:-}"; id="${A[2]:-}"; disp="${A[3]:-}"; mode="${A[4]:-}" ;;
      *)      gerr "Perintah tidak dikenal: ${A[*]}"; continue ;;
    esac
    case "$disp" in mirror|extend) DISPLAY_MODE="$disp" ;; *) gerr "Tampilan tidak valid: $disp"; continue ;; esac
    case "$mode" in 480p60|720p30|720p60|1080p30) ;; *) gerr "Resolusi tidak valid: $mode"; continue ;; esac
    # Opsi Advanced: dicek semua dulu, baru dipakai kalau valid
    how="$CONNECT_MODE"; go="$GO_INTENT"; extra=(); gextra=(); bad=""
    for opt in "${A[@]:5}"; do
      k="${opt%%=*}"; v="${opt#*=}"
      case "$k=$v" in
        how=auto|how=join|how=negotiate) how="$v" ;;
        go=*)      [[ "$v" =~ ^([0-9]|1[0-5])$ ]] && go="$v" || bad="$opt" ;;
        hw=1)      extra+=(--hw) ;;
        hw=0)      ;;
        audio=off) extra+=(--no-audio) ;;
        audio=auto|audio=aac|audio=lpcm) extra+=(--audio "$v") ;;
        bitrate=*) if [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -ge 500 ] && [ "$v" -le 30000 ]; then
                     extra+=(--bitrate $((v * 1024))); gextra+=(--bitrate "$v")
                   else bad="$opt"; fi ;;
        *)         bad="$opt" ;;
      esac
    done
    [ -z "$bad" ] || { gerr "Opsi tidak valid: $bad"; continue; }
    if [ "$kind" = gcast ]; then
      GC_EXTRA=(${gextra[@]+"${gextra[@]}"})
      for ip in "${GC_IPS[@]}"; do [ "$ip" = "$id" ] && gui_gcast "$id" "$mode"; done
      gerr "Perangkat Google Cast $id tidak ada di hasil pindai"; continue
    fi
    if [ "$kind" = miracast ]; then
      for i in "${!MACS[@]}"; do [ "${MACS[$i],,}" = "${id,,}" ] && GUI_SEL=$i; done
      if [ -n "$GUI_SEL" ]; then
        PY_ARGS+=(--mode "$mode" ${extra[@]+"${extra[@]}"})
        CONNECT_MODE="$how"; GO_INTENT="$go"
        start_stop_watcher
        return 0
      fi
      gerr "Perangkat Miracast $id tidak ada di hasil pindai"; continue
    fi
    gerr "Jenis perangkat tidak valid: $kind"
  done
}
# Google Cast dari sesi GUI: buka port HTTP stream, jalankan gcast.py sebagai user, tunggu sampai berhenti
gui_gcast() {
  local ip=$1 size fps vargs=()
  case "$2" in
    480p60) size=854x480; fps=60 ;; 720p30) size=1280x720; fps=30 ;;
    720p60) size=1280x720; fps=60 ;; 1080p30) size=1920x1080; fps=30 ;;
  esac
  [ "$DISPLAY_MODE" = extend ] && vargs=(--virtual)
  GC_RULE="INPUT -p tcp --dport 8099 -j ACCEPT"
  iptables -C $GC_RULE 2>/dev/null || iptables -I $GC_RULE
  echo "Port 8099 dibuka sementara untuk stream ke TV."
  ev STATE connecting "$ip"
  start_stop_watcher
  setsid sudo -u "$U" env XDG_RUNTIME_DIR="/run/user/$UIDN" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UIDN/bus" \
    python3 -I -u "$DIR/gcast.py" --device "$ip" --size "$size" --fps "$fps" \
    ${vargs[@]+"${vargs[@]}"} ${CAP_ARGS[@]+"${CAP_ARGS[@]}"} ${GC_EXTRA[@]+"${GC_EXTRA[@]}"} </dev/null &
  PYPID=$!
  while kill -0 "$PYPID" 2>/dev/null; do wait "$PYPID" 2>/dev/null; done
  echo "Google Cast berhenti."
  exit 0
}

if [ "$GUI" = 1 ]; then
  gui_select
else
  scan_miracast 2
  [ ${#MACS[@]} -gt 0 ] || { echo "Tidak ada perangkat Miracast yang terdeteksi. Pastikan projektor di mode Screen Mirroring / TV di Screen Share."; exit 1; }
fi

SEL=-1
if [ -n "$GUI_SEL" ]; then
  SEL=$GUI_SEL
elif [ -n "$PEER" ]; then
  # --peer / PEER=: cocokkan dengan nama (tanpa beda huruf besar/kecil) atau MAC
  for i in "${!MACS[@]}"; do
    if [ "${MACS[$i],,}" = "${PEER,,}" ] || [ "${NAMES[$i],,}" = "${PEER,,}" ]; then SEL=$i; break; fi
  done
  [ "$SEL" -ge 0 ] || { echo "Perangkat '$PEER' tidak ditemukan. Yang terdeteksi: ${NAMES[*]}"; exit 1; }
else
  DEF=0
  for i in "${!MACS[@]}"; do [ "${NAMES[$i]}" = "$DEFAULT_NAME" ] && DEF=$i; done
  echo
  for i in "${!MACS[@]}"; do
    mark=" "; [ "$i" = "$DEF" ] && mark="*"
    printf ' %s %d) %-24s %s  sinyal %4s dBm  [%s]\n' "$mark" $((i+1)) "${NAMES[$i]}" "${MACS[$i]}" \
      "${LEVELS[$i]:-?}" "$(how_for "${CAPS[$i]}")"
  done
  echo
  read -r -p "Pilih nomor perangkat [Enter = $((DEF+1)) ${NAMES[$DEF]}]: " ANS </dev/tty
  ANS="${ANS:-$((DEF+1))}"
  if ! [[ "$ANS" =~ ^[0-9]+$ ]] || [ "$ANS" -lt 1 ] || [ "$ANS" -gt ${#MACS[@]} ]; then
    echo "Pilihan tidak valid: $ANS"; exit 1
  fi
  SEL=$((ANS-1))
fi
PEER="${MACS[$SEL]}"; PEER_NAME="${NAMES[$SEL]}"; PEER_LFREQ="${LFREQS[$SEL]:-}"
HOW="$CONNECT_MODE"; [ "$HOW" = auto ] && HOW=$(how_for "${CAPS[$SEL]}")
echo "Perangkat: $PEER_NAME ($PEER), cara tersambung: $HOW"
ev STATE connecting "$PEER_NAME" "$HOW"

# Mode tampilan: dari --mirror/--extend, atau ditanyakan lewat menu
if [ -z "$DISPLAY_MODE" ]; then
  echo
  echo "   1) Mirror  - layar laptop ditampilkan sama persis"
  echo "   2) Extend  - perangkat jadi monitor tambahan (geser jendela ke sana)"
  echo
  read -r -p "Pilih tampilan [Enter = 1 Mirror]: " DANS </dev/tty
  case "${DANS:-1}" in
    1|m|M|mirror) DISPLAY_MODE="mirror" ;;
    2|e|E|extend) DISPLAY_MODE="extend" ;;
    *) echo "Pilihan tidak valid: $DANS"; exit 1 ;;
  esac
fi
[ "$DISPLAY_MODE" = extend ] && PY_ARGS+=("--virtual")
echo "Tampilan: $DISPLAY_MODE"

echo "== 4/6 Jalankan server Miracast (sebagai $U)"
# setsid: process group sendiri, jadi Ctrl+C hanya ditangani script ini lalu dihentikan rapi
setsid sudo -u "$U" env XDG_RUNTIME_DIR="/run/user/$UIDN" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UIDN/bus" \
  python3 -I -u "$DIR/wfd_source.py" -v "${PY_ARGS[@]}" </dev/null &
PYPID=$!
for _ in $(seq 20); do ss -ltn | grep -q ':7236 ' && break; sleep 0.5; done
ss -ltn | grep -q ':7236 ' || { echo "Server Miracast gagal start"; exit 1; }

# Interface grup P2P yang benar-benar aktif (operstate up + carrier).
# Interface sisa percobaan lama (down) diabaikan.
active_gif() {
  local i
  for i in $(ls /sys/class/net | grep '^p2p-' | grep -v '^p2p-dev-'); do
    [ "$(cat /sys/class/net/$i/operstate 2>/dev/null)" = up ] && \
    [ "$(cat /sys/class/net/$i/carrier 2>/dev/null)" = 1 ] && { echo "$i"; return 0; }
  done
  return 1
}

echo "== 5/6 Wi-Fi Direct ke $PEER_NAME ($HOW)"
# Selalu mulai dari awal: perangkat memulai sesi Miracast tepat setelah tersambung.
if active_gif >/dev/null; then
  echo "Putuskan grup lama dulu"
  W p2p_group_remove '*' >/dev/null 2>&1
  for _ in $(seq 10); do active_gif >/dev/null || break; sleep 0.5; done
  sleep 2
fi
W p2p_find 10 >/dev/null
for _ in $(seq 20); do W p2p_peer "$PEER" 2>/dev/null | grep -q '^device_name' && break; sleep 0.5; done
W p2p_stop_find >/dev/null

# ---- Jalur cepat untuk perangkat yang sudah dikenal (persistent group + P2P Invitation) ----
# Negosiasi baru ke TV LG selalu menunggu ~60 detik (TV menjawab "status 1"). TV mengiklankan dukungan
# persistent group (group_capab 0x2a), jadi koneksi pertama dibuat persistent dan disimpan; koneksi
# berikutnya cukup mengundang TV ke grup itu. Credential juga disimpan di file supaya tahan reboot.
PSTORE=/var/lib/miracast-cast
PFILE="$PSTORE/$(echo "$PEER" | tr ':' '-').conf"
find_persistent_id() {   # id jaringan persistent (di wpa_supplicant) yang client-nya perangkat ini
  local id
  for id in $(W list_networks 2>/dev/null | awk -F'\t' 'NR>1 && $4 ~ /P2P-PERSISTENT/ {print $1}'); do
    W get_network "$id" p2p_client_list 2>/dev/null | grep -qi "$PEER" && { echo "$id"; return 0; }
  done
  return 1
}
restore_persistent_id() {   # buat ulang dari file kalau wpa_supplicant sudah restart (mis. setelah reboot)
  [ -r "$PFILE" ] || return 1
  local SSID="" PASS="" id
  # shellcheck disable=SC1090
  . "$PFILE"
  [ -n "$SSID" ] && [ -n "$PASS" ] || return 1
  id=$(W add_network 2>/dev/null | tail -1); [[ "$id" =~ ^[0-9]+$ ]] || return 1
  W set_network "$id" ssid "\"$SSID\"" >/dev/null
  W set_network "$id" psk "\"$PASS\"" >/dev/null
  W set_network "$id" key_mgmt WPA-PSK >/dev/null
  W set_network "$id" proto RSN >/dev/null
  W set_network "$id" pairwise CCMP >/dev/null
  W set_network "$id" mode 3 >/dev/null          # 3 = P2P GO (laptop)
  W set_network "$id" disabled 2 >/dev/null      # 2 = persistent P2P group
  W set_network "$id" p2p_client_list "$PEER" >/dev/null
  echo "$id"
}
persistent_ids_for_peer() {   # semua id grup persistent yang client-nya perangkat ini
  local id
  for id in $(W list_networks 2>/dev/null | awk -F'\t' 'NR>1 && $4 ~ /P2P-PERSISTENT/ {print $1}'); do
    W get_network "$id" p2p_client_list 2>/dev/null | grep -qi "$PEER" && echo "$id"
  done
}
forget_persistent() {   # hapus grup persistent perangkat ini (di wpa_supplicant) + file credential
  local id
  for id in $(persistent_ids_for_peer); do W remove_network "$id" >/dev/null 2>&1; done
  rm -f "$PFILE"
}
invite_known_peer() {
  local id ti rejected=0
  id=$(find_persistent_id) || id=$(restore_persistent_id) || return 1
  echo "Perangkat sudah dikenal: kirim P2P Invitation ke grup persistent #$id (jalur cepat)"
  ti=$(date +%s)
  W p2p_invite persistent="$id" peer="$PEER" >/dev/null
  local why="tidak ada jawaban dalam 8 detik"
  for _ in $(seq 32); do   # maks 8 detik (TV LG menjawab dalam ~20 ms)
    GIF=$(active_gif) && { echo "Invitation diterima dalam $(( $(date +%s) - ti )) detik"; return 0; }
    if journalctl -u wpa_supplicant --since "@$ti" -o cat --no-pager 2>/dev/null \
         | grep -qE 'P2P-INVITATION-RESULT status=[1-9]'; then
      why="ditolak: $(journalctl -u wpa_supplicant --since "@$ti" -o cat --no-pager 2>/dev/null \
            | grep -oE 'P2P-INVITATION-RESULT status=[0-9]+' | tail -1)"
      rejected=1
      break
    fi
    sleep 0.25
  done
  GIF=""
  echo "Invitation tidak berhasil ($why), pakai negosiasi biasa"
  W p2p_group_remove '*' >/dev/null 2>&1
  W p2p_cancel >/dev/null 2>&1
  if [ "$rejected" = 1 ]; then
    # Ditolak eksplisit (mis. TV di-reset / lupa laptop): grup lama tidak berlaku lagi, jangan dipakai lagi.
    # Kalau hanya tidak dijawab (TV mati / di luar jangkauan), grup tetap disimpan.
    forget_persistent
    echo "Grup persistent lama untuk perangkat ini dihapus"
  fi
  return 1
}

T0=$(date +%s)
FAST=0
if [ "$HOW" = negotiate ]; then
  # Dengarkan di channel yang sama dengan perangkat. Setelah pengguna menyetujui, perangkat yang tadi
  # menjawab "status 1" memulai GO Negotiation sendiri; TV LG mengirimnya di channel-nya sendiri
  # (terlihat: semua frame TV di 2412 MHz, sedangkan laptop mendengarkan di 2437 MHz).
  if [[ "$PEER_LFREQ" =~ ^24[0-9][0-9]$ ]] && [ "$PEER_LFREQ" -ge 2412 ] && [ "$PEER_LFREQ" -le 2462 ]; then
    LCH=$(( (PEER_LFREQ - 2407) / 5 ))
    W set p2p_listen_reg_class 81 >/dev/null
    W set p2p_listen_channel "$LCH" >/dev/null && echo "Channel dengar laptop = channel $LCH (sama dengan perangkat)"
  fi
  invite_known_peer && FAST=1
fi
if [ "$FAST" = 1 ]; then
  WAIT_S=0
elif [ "$HOW" = join ]; then
  W p2p_connect "$PEER" pbc join
  WAIT_S=20
else
  T0=$(date +%s)
  # provdisc: kirim Provision Discovery dulu (seperti Windows) supaya perangkat memunculkan prompt.
  # go_intent: 14 = laptop (hampir pasti) jadi Group Owner; perangkat yang memaksa 15 tetap jadi GO (laptop = client).
  # persistent: grup disimpan supaya koneksi berikutnya bisa lewat P2P Invitation (jalur cepat).
  W p2p_connect "$PEER" pbc persistent go_intent=$GO_INTENT provdisc
  echo "go_intent laptop = $GO_INTENT (grup persistent)"
  echo ">>> Kalau di TV/perangkat muncul permintaan koneksi, SETUJUI dengan remote. TV LG biasanya baru tersambung ~70 detik kemudian (maks 150 detik)."
  WAIT_S=150   # TV LG baru memulai GO Negotiation sendiri ~70 detik setelah Provision Discovery
  ev STATE approve "$WAIT_S"
fi
[ "$FAST" = 1 ] || GIF=""
FAIL=""; RETRY=0; LAST_TRY=$T0
for _ in $(seq $((WAIT_S * 4))); do
  GIF=$(active_gif) && break
  GIF=""
  if [ "$HOW" = negotiate ]; then
    # TV menjawab "status 1" (= menunggu persetujuan pengguna) lalu seharusnya mengirim GO Negotiation
    # balik. Saat laptop juga tersambung ke WiFi lain, permintaan itu sering terlewat. Seperti Android/
    # Windows: kirim ulang GO Negotiation tiap ~5 detik; setelah disetujui di TV, jawabannya status 0.
    NOW=$(date +%s)
    if (( NOW - LAST_TRY >= 10 )); then
      if journalctl -u wpa_supplicant --since "@$LAST_TRY" -o cat --no-pager 2>/dev/null \
           | grep -qE 'GO Negotiation rejected: status 1|Wait for the peer to become ready'; then
        RETRY=$((RETRY + 1))
        echo "   TV belum siap / menunggu persetujuan, kirim ulang negosiasi ($RETRY)"
        W p2p_cancel >/dev/null
        W p2p_connect "$PEER" pbc persistent go_intent=$GO_INTENT >/dev/null   # tanpa provdisc: jangan munculkan prompt lagi
      fi
      LAST_TRY=$NOW
    fi
  else
    FAIL=$(journalctl -u wpa_supplicant --since "@$T0" -o cat --no-pager 2>/dev/null \
           | grep -oE 'P2P-(GO-NEG-FAILURE|GROUP-FORMATION-FAILURE).*' | tail -1)
    [ -n "$FAIL" ] && break
  fi
  sleep 0.25
done
[ -n "$GIF" ] || [ "$HOW" = join ] || FAIL=$(journalctl -u wpa_supplicant --since "@$T0" -o cat --no-pager 2>/dev/null \
           | grep -oE 'P2P-(GO-NEG-FAILURE|GROUP-FORMATION-FAILURE).*|GO Negotiation rejected: status [0-9]+' | tail -1)
[ -n "$GIF" ] || { echo "Gagal tersambung ${FAIL:+($FAIL)}"; exit 1; }

# Peran laptop di grup: dari event P2P-GROUP-STARTED <iface> GO|client
ROLE=$(journalctl -u wpa_supplicant --since "@$T0" -o cat --no-pager 2>/dev/null \
       | grep -oE "P2P-GROUP-STARTED $GIF (GO|client)" | tail -1 | awk '{print $3}')
[ -n "$ROLE" ] || ROLE=$(timeout 3 wpa_cli -i "$GIF" status 2>/dev/null </dev/null | grep -q '^mode=P2P GO' && echo GO || echo client)
echo "Terhubung lewat $GIF (laptop = $ROLE)"
ev STATE linked "$ROLE"

# Simpan credential grup persistent (laptop = GO) supaya jalur cepat tetap bisa dipakai setelah reboot
if [ "$HOW" = negotiate ] && [ "$ROLE" = GO ]; then
  GLINE=$(journalctl -u wpa_supplicant --since "@$T0" -o cat --no-pager 2>/dev/null \
          | grep -E "P2P-GROUP-STARTED $GIF GO " | tail -1)
  if printf '%s' "$GLINE" | grep -q '\[PERSISTENT\]' && [ "$FAST" = 0 ]; then
    # Grup baru hasil negosiasi = id terbesar; grup lama untuk perangkat yang sama dibuang supaya
    # jalur cepat berikutnya tidak memakai grup yang sudah tidak berlaku.
    PIDS=$(persistent_ids_for_peer | sort -n)
    NEWEST=$(printf '%s\n' "$PIDS" | tail -1)
    for id in $PIDS; do [ "$id" != "$NEWEST" ] && W remove_network "$id" >/dev/null 2>&1; done
  fi
  if printf '%s' "$GLINE" | grep -q '\[PERSISTENT\]'; then
    G_SSID=$(printf '%s' "$GLINE" | grep -oE 'ssid="[^"]*"' | head -1 | sed 's/^ssid="//; s/"$//')
    G_PASS=$(timeout 3 wpa_cli -i "$GIF" p2p_get_passphrase </dev/null 2>/dev/null | tail -1)
    if [ -n "$G_SSID" ] && [ -n "$G_PASS" ] && [ "$G_PASS" != FAIL ]; then
      mkdir -p "$PSTORE" && chmod 700 "$PSTORE"
      printf 'SSID=%q\nPASS=%q\n' "$G_SSID" "$G_PASS" > "$PFILE" && chmod 600 "$PFILE"
      echo "Grup persistent disimpan ($PFILE): koneksi berikutnya lewat jalur cepat"
    else
      echo "Grup persistent tersimpan di wpa_supplicant (hilang kalau wpa_supplicant restart / reboot)"
    fi
  else
    echo "Catatan: grup tidak persistent, jalur cepat belum bisa dipakai"
  fi
fi

if [ "$ROLE" = client ]; then
  # Perangkat = Group Owner: minta IP lewat DHCP
  echo "== 6/6 DHCP dari perangkat"
  setsid busybox udhcpc -i "$GIF" -f -t 6 -T 3 -s "$DIR/dhcp-hook.sh" </dev/null >/dev/null 2>&1 &
  DHPID=$!
  for _ in $(seq 30); do [ -s /run/p2p-cast.dhcp ] && break; sleep 0.5; done
  if [ -s /run/p2p-cast.dhcp ]; then
    read -r MYIP GOIP _ < /run/p2p-cast.dhcp
    echo "IP laptop: $MYIP   perangkat: ${GOIP:-?}"
  else
    echo "Tidak dapat IP dari DHCP perangkat"; exit 1
  fi
else
  # Laptop = Group Owner: pasang IP sendiri dan jalankan server DHCP untuk perangkat
  echo "== 6/6 Laptop jadi Group Owner: server DHCP $GO_NET.0/24"
  ip -4 addr flush dev "$GIF"
  ip -4 addr add "$GO_NET.1/24" dev "$GIF"
  : > /run/p2p-cast-udhcpd.leases
  cat > "$UDHCPD_CONF" <<EOF
interface $GIF
start $GO_NET.10
end $GO_NET.20
max_leases 10
lease_file /run/p2p-cast-udhcpd.leases
option subnet 255.255.255.0
# Sink Miracast menyambung ke gateway (= laptop, Group Owner); NM "shared" (dnsmasq) juga mengirim ini
option router $GO_NET.1
option dns $GO_NET.1
EOF
  # Log DHCP server disimpan (tidak dihapus saat cleanup) supaya bisa dianalisis: /run/p2p-cast-udhcpd.log
  setsid busybox udhcpd -f "$UDHCPD_CONF" </dev/null >/run/p2p-cast-udhcpd.log 2>&1 &
  DHPID=$!
  echo "IP laptop: $GO_NET.1   perangkat akan mendapat $GO_NET.10-20"
  setsid bash -c "tail -n +1 -F /run/p2p-cast-udhcpd.log 2>/dev/null | sed -u 's/^/   dhcp: /'" </dev/null &
  TAILPID=$!
fi

echo
echo ">>> Menunggu perangkat menyambung ke laptop. Lihat layar perangkat. Ctrl+C untuk berhenti."
ev STATE waiting
# 'wait' bisa diinterupsi Ctrl+C (trap langsung jalan); loop supaya tetap menunggu kalau ada sinyal lain
while kill -0 "$PYPID" 2>/dev/null; do wait "$PYPID" 2>/dev/null; done
echo "Server Miracast berhenti."
