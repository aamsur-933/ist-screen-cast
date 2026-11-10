# Miracast dari Ubuntu 22.04 ke projektor/TV (eksperimental)

Cast layar Linux (GNOME Wayland; desktop lain lewat xdg-desktop-portal) ke projektor Epson, TV LG webOS, dan perangkat Miracast
lain lewat Wi-Fi Direct. Script ini dipakai karena GNOME Network Displays di
Ubuntu 22.04 gagal ke projektor Epson.

## Pemakaian

Hentikan GNOME Network Displays dulu. Siapkan perangkat tujuan:

- Projektor Epson: pilih source **Screen Mirroring**.
- TV LG webOS: cukup nyalakan (input apa saja), lalu setujui pop-up koneksi.

Lalu jalankan:

    sudo bash cast.sh                    # pindai, pilih perangkat dari menu (Enter = LOBBY.A1)
    sudo bash cast.sh --peer LOBBY.A1    # langsung ke perangkat ini (nama atau MAC), tanpa menu
    sudo bash cast.sh --extend           # langsung extend (monitor tambahan), tanpa menu tampilan
    sudo bash cast.sh --mirror           # langsung mirror, tanpa menu tampilan
    sudo bash cast.sh --mode 1080p30     # 480p60 | 720p30 | 720p60 | 1080p30
    sudo bash cast.sh --join             # paksa JOIN (default: otomatis)
    sudo bash cast.sh --negotiate        # paksa NEGOSIASI (default: otomatis)
    sudo bash cast.sh --hw               # coba encoder hardware, otomatis kembali ke openh264
    sudo bash cast.sh --iface wlp2s0     # interface WiFi tertentu (default: dideteksi otomatis)
    sudo bash cast.sh --check            # hanya cek kesiapan laptop, tanpa mengubah apa pun
    sudo bash cast.sh --capture portal   # paksa tangkapan layar lewat xdg-desktop-portal (default: auto)

Setelah memilih perangkat, script menanyakan tampilan: **1) Mirror** (default, Enter) atau
**2) Extend** (perangkat menjadi monitor tambahan; atur posisinya di Settings → Displays).

Menu perangkat hanya menampilkan perangkat yang mengiklankan Wi-Fi Display (Miracast).
Printer Wi-Fi Direct seperti `DIRECT-…-EPSON-L5290` disaring. Setiap entri
menampilkan cara tersambung yang akan dipakai:

| Cara | Kapan | Contoh |
|---|---|---|
| `join` | Perangkat sudah menjadi Group Owner (`group_capab` bit 0) | Projektor Epson |
| `negotiate` | Perangkat belum punya grup | TV LG webOS |

Pada `negotiate`, laptop mengirim Provision Discovery lalu GO Negotiation dengan
`go_intent=14`, sehingga **laptop yang menjadi Group Owner** (perangkat yang memaksa 15 tetap
menjadi GO dan laptop menjadi client; 15 lawan 15 akan gagal dengan status 9). TV memunculkan permintaan
koneksi; setujui dengan remote (batas waktu 150 detik; TV LG biasanya tersambung ~70 detik setelah percobaan pertama). Laptop lalu memakai IP
`192.168.49.1` dan menjalankan server DHCP (`busybox udhcpd`) untuk perangkat.
Opsi `--go-intent 0` meminta perangkat yang menjadi Group Owner.

Hal yang dibutuhkan TV LG (UQ8050PSB, webOS) agar mau tersambung:
- TV menjawab `go_intent 0` (ingin jadi client), jadi laptop harus menjadi GO (`go_intent=14`).
- TV menjawab GO Negotiation dengan `status 1` sampai pengguna menyetujui. Script
  mengirim ulang negosiasi setiap 10 detik dan mendengarkan di channel TV
  (`listen_freq` TV, di sini channel 1).
- DHCP harus mengirim **router** (`option router 192.168.49.1`). Tanpa router, TV mendapat
  IP tetapi tidak pernah menyambung ke RTSP di laptop, lalu memutus setelah sekitar 17 detik.
- TV LG baru tidak punya aplikasi "Screen Share": pop-up persetujuan muncul di input apa pun.
  Kalau TV tertahan di layar "menyambung", tekan Batal dulu.

**Jalur cepat (perangkat yang sudah dikenal).** Mode `negotiate` membuat grup sebagai
*persistent* (`p2p_connect … persistent`), lalu menyimpan SSID dan passphrase grup di
`/var/lib/miracast-cast/<mac>.conf`. Koneksi berikutnya mengirim **P2P Invitation**
(`p2p_invite persistent=…`) ke grup itu, tanpa Provision Discovery dan tanpa negosiasi
baru. Kalau undangan ditolak atau tidak dijawab dalam 8 detik, script otomatis kembali
ke negosiasi biasa. Alasannya: setiap negosiasi baru ke TV LG selalu menunggu sekitar
60 detik (diukur pada 3 koneksi: TV mengirim negosiasinya sendiri di +69,4 / +69,9 /
+69,5 detik), padahal TV mengiklankan dukungan persistent group (`group_capab 0x2a`).
Kalau undangan **ditolak** (misalnya TV di-reset), grup lama dan file credential perangkat itu
dihapus. Kalau undangan hanya **tidak dijawab** (TV mati atau di luar jangkauan), grup tetap
disimpan. Setelah negosiasi baru berhasil, grup lama untuk perangkat yang sama dibuang.

Tekan Ctrl+C untuk berhenti dan membersihkan semuanya. Tekan Ctrl+C sekali lagi
untuk keluar paksa.

## Google Cast (Android TV / Google TV / Chromecast)

Untuk TV yang **tidak** mendukung Miracast (tidak muncul di menu `cast.sh`), misalnya TV TCL
Android TV. Jalurnya lewat WiFi biasa, jadi laptop dan TV harus berada di jaringan yang sama.

    sudo bash gcast.sh                                   # pindai, pilih perangkat dari menu
    sudo bash gcast.sh --device "Living Room TV"         # langsung ke perangkat (nama atau IP)
    sudo bash gcast.sh --virtual                         # extend (monitor virtual)
    sudo bash gcast.sh --list                            # hanya tampilkan perangkat
    sudo bash gcast.sh --device "Living Room TV" --status-only   # uji koneksi, tanpa membuka app di TV

Cara kerja: laptop membuka koneksi kontrol Cast (TLS port 8009), menjalankan **Default Media
Receiver** (`CC1AD845`) di TV, lalu meminta TV memutar `http://<laptop>:8099/screen.webm`.
Stream itu berisi tangkapan layar desktop (VP8 + Opus, WebM). sudo hanya dipakai untuk
membuka port 8099 di firewall selama sesi. Delay lebih besar dari Miracast karena TV
mem-buffer stream HTTP. Rencana dan detail protokol ada di `docs/google-cast.md`.

## Dipakai di laptop lain (tim)

Jalankan dulu `sudo bash cast.sh --check`. Perintah ini tidak mengubah apa pun dan
menampilkan `OK`/`XX` beserta perintah `apt install` yang dibutuhkan. Pemeriksaan yang
sama otomatis berjalan setiap kali `cast.sh` dijalankan, sebelum sistem diubah.

Yang diperiksa:

| Cek | Kenapa |
|---|---|
| Perintah dasar (wpa_cli, nmcli, iptables, busybox udhcpc/udhcpd, pactl, ...) | dipakai script |
| Interface WiFi dan `p2p-dev-<wifi>` merespons `ping` | chip/driver dan wpa_supplicant harus mendukung Wi-Fi Direct |
| `iw list` melaporkan P2P-GO/P2P-client (kalau `iw` terpasang) | dukungan driver |
| Sesi desktop Wayland | Extend (monitor virtual) butuh Wayland |
| Mutter ScreenCast API (utama) atau xdg-desktop-portal ScreenCast (cadangan) | tangkapan layar; minimal salah satu harus ada |
| Elemen GStreamer wajib, encoder openh264, sumber audio | jalur media |
| Encoder hardware yang tersedia | informasi untuk `--hw` |

Nama interface WiFi tidak lagi tertulis tetap di script. Interface dideteksi otomatis
(WiFi yang tersambung menurut NetworkManager), atau bisa dipilih dengan `--iface`.

Encoder video default adalah **openh264** (software): hasilnya sama di semua laptop dan
selalu Constrained Baseline. `--hw` mencoba `vah264lpenc`, `vah264enc`, `vaapih264enc`,
lalu `nvh264enc`. Setiap encoder diuji dulu dengan beberapa frame dan harus menghasilkan
Constrained Baseline; kalau tidak ada yang lolos, script kembali ke openh264. Encoder yang
dipakai tercatat di log (`Encoder video: ...`).

Yang belum pernah diuji: laptop selain Ubuntu 22.04 (NetworkManager 1.36), chip WiFi
non-Intel, encoder hardware (di laptop pengembang tidak ada yang terpasang), dan desktop
selain GNOME.

### Tangkapan layar: Mutter atau xdg-desktop-portal

| | Mutter ScreenCast (utama) | xdg-desktop-portal ScreenCast (cadangan) |
|---|---|---|
| Desktop | GNOME; kemungkinan juga Gala (elementary OS, turunan Mutter) | KDE Plasma, wlroots (Sway/Hyprland), dll., asal backend portalnya terpasang |
| Dialog persetujuan | tidak ada | dialog "bagikan layar" muncul dan pengguna memilih layar |
| Persetujuan diingat | - | ya, kalau portal versi 4+; token di `~/.local/state/miracast-cast/` |
| Extend (monitor virtual) | ya (API versi 4+, GNOME 42+) | hanya kalau portal desktop itu menawarkannya |

Mode `auto` (default) mencoba Mutter dulu, lalu portal kalau Mutter tidak ada. Backend yang
dipakai tercatat di log (`Tangkapan layar: ...`), dan `--check` menampilkan keduanya. Kalau
dialog portal tidak lagi muncul padahal ingin memilih layar lain, hapus
`~/.local/state/miracast-cast/portal-*.token`.

Keduanya butuh sesi **Wayland**. elementary OS 8 memakai X11 secara default, jadi pilih sesi
"Secure Session" (Wayland) di layar login. Untuk mengecek apakah desktop menyediakan
Mutter ScreenCast:

    gdbus introspect --session --dest org.gnome.Mutter.ScreenCast \
      --object-path /org/gnome/Mutter/ScreenCast | grep -i version

## Kenapa perlu script ini

- Projektor Epson selalu menjalankan grupnya sendiri sebagai *persistent Group
  Owner*. Negosiasi grup baru ditolak dengan `status 1`, jadi laptop harus **join**
  (`p2p_connect … pbc join`). NetworkManager 1.36 tidak bisa join.
- NetworkManager menghapus grup P2P yang tidak ia buat sendiri lewat D-Bus
  `RemoveInterface` ("NMSupplicantInterface is disposing"). Selama sesi, panggilan
  itu diblokir dengan aturan D-Bus sementara.
- Projektor Epson hanya menerima audio **LPCM**. `mpegtsmux` menandai LPCM sebagai DVD
  (`0x8b`/`0xfd`), padahal WFD butuh `0x83`/`0xbd`. Selain itu `mpegtsmux` menambahkan
  descriptor Blu-ray `HDMV` di PMT. `TsPatcher` membangun ulang PAT/PMT seperti
  `TSPacketizer.cpp` di Android: PMT PID `0x100`, descriptor AVC `0x28` + `0x2a`, dan
  descriptor LPCM `0x83`. Stream memakai PID WFD standar (video `0x1011`, audio `0x1100`).
- Balasan RTSP dari firmware Epson kadang diawali data sampah, jadi parser
  membuangnya.

## Status pengujian

- **TV LG webOS `[LG] webOS TV UQ8050PSB`: berhasil**, gambar dan suara (mode `negotiate`, laptop = GO).
  Diuji dengan `--audio lpcm`: 720p30, H.264 CBP 3.1, LPCM 48 kHz, PMT gaya Android, sama
  persis dengan konfigurasi untuk Epson. TV menjawab `wfd_content_protection: HDCP2.1`,
  tetapi tetap memutar stream tanpa HDCP.
  Jalur cepat terbukti: P2P Invitation diterima dalam 21 ms dan TV tersambung 0,7 detik
  setelah undangan (negosiasi biasa: sekitar 70 detik).
- Projektor Epson `LOBBY.A1`: join, DHCP, RTSP M1–M7, dan keepalive stabil (diuji lebih
  dari 1,5 menit). **Layar masih hitam.** PMT gaya Android (descriptor `0x28`/`0x2a`/`0x83`,
  PMT PID `0x100`) belum dicoba ke projektor. Pembanding yang pasti: rekaman `pktmon`
  dari Windows yang cast ke projektor yang sama.

## File

| File | Fungsi |
|---|---|
| `cast.sh` | Orkestrasi: NM, D-Bus, firewall, join/negosiasi, DHCP, lalu menjalankan server |
| `wfd_source.py` | Miracast source: tangkapan layar (Mutter / portal), RTSP M1–M7, GStreamer H.264 + LPCM/AAC |
| `dhcp-hook.sh` | Hook udhcpc: hanya memasang IP, tanpa mengubah route/DNS |
| `fake_sink.py` | Projektor tiruan untuk uji lokal (`wfd_source.py --port 17236` lalu `fake_sink.py 17236`) |
| `p2p-test.sh` | Uji join Wi-Fi Direct saja |
| `gcast.sh` / `gcast.py` | Google Cast: firewall sementara + kontrol Cast + HTTP stream WebM |

## Perubahan sistem

Perubahan yang **menetap**:

- `/var/lib/miracast-cast/`: credential grup persistent untuk jalur cepat. Hapus folder ini
  untuk mengulang dari negosiasi biasa.
- `~/.local/state/miracast-cast/portal-*.token`: persetujuan dialog portal. File ini hanya ada
  kalau xdg-desktop-portal dipakai.

Perubahan lain bersifat **sementara** dan dihapus saat Ctrl+C:

- `/etc/NetworkManager/conf.d/99-p2p-manual.conf` (NM mengabaikan interface P2P).
  Selama file ini ada, GNOME Network Displays tidak bisa dipakai.
- `/etc/dbus-1/system.d/zz-p2p-cast-temp.conf` (blokir `RemoveInterface`)
- aturan iptables TCP 7236 dan UDP 67 di `p2p-wlo1-+`
- `/run/p2p-cast-udhcpd.conf` dan lease file (hanya kalau laptop menjadi Group Owner)
- grup P2P

Setelah selesai, `p2p-dev-wlo1` bisa tertahan di status "unavailable". Untuk memakai
GNOME Network Displays lagi, jalankan `sudo systemctl restart NetworkManager`.

Kalau script sempat dimatikan paksa, kembalikan manual:

    sudo rm -f /etc/NetworkManager/conf.d/99-p2p-manual.conf && sudo nmcli general reload conf
    sudo rm -f /etc/dbus-1/system.d/zz-p2p-cast-temp.conf && sudo systemctl reload dbus
