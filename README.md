# IST Screen Cast

Cast layar laptop Linux ke projektor dan TV, tanpa kabel HDMI. Ada dua jalur:

| Jalur | Untuk | Jaringan |
|---|---|---|
| **Miracast** (Wi-Fi Direct) | projektor dengan *Screen Mirroring*, TV dengan *Screen Share* / Miracast (mis. TV LG webOS) | langsung laptop ↔ perangkat, tidak perlu WiFi yang sama |
| **Google Cast** | Android TV / Google TV / Chromecast (mis. TV TCL) | laptop dan TV harus di WiFi yang sama |

Bisa dipakai lewat aplikasi **IST Screen Cast** (GUI) atau lewat terminal. Status: eksperimental;
lihat [Perangkat yang sudah diuji](#perangkat-yang-sudah-diuji).

## Kebutuhan

- Linux dengan **NetworkManager** dan **wpa_supplicant** (default di Ubuntu, Debian, Fedora, Mint, Pop!_OS, elementary OS).
- WiFi yang mendukung **Wi-Fi Direct (P2P)**. `--check` memberi tahu apakah WiFi laptop mendukung.
- Sesi desktop **Wayland**:
  - GNOME: langsung jalan, tanpa dialog.
  - Desktop lain (KDE Plasma, Sway, dll.): lewat xdg-desktop-portal. Lihat [Tangkapan layar](#tangkapan-layar-mutter-atau-xdg-desktop-portal).
- Paket (nama Debian/Ubuntu):

      sudo apt install wpasupplicant network-manager iptables busybox iproute2 pulseaudio-utils \
        python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-pipewire \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
        gstreamer1.0-pulseaudio avahi-utils ffmpeg

  `ffmpeg` hanya dipakai untuk Google Cast.

`avahi-utils` hanya dibutuhkan untuk Google Cast.

**Cek dulu sebelum dipakai pertama kali:**

    sudo bash cast.sh --check

Perintah ini tidak mengubah apa pun. Hasilnya `OK`/`XX` per item, plus perintah `apt install`
untuk yang kurang. Pengecekan yang sama juga berjalan otomatis setiap kali cast dimulai.

## Cara pakai

Siapkan perangkat tujuan dulu:

- **Projektor:** pilih input/source **Screen Mirroring** (atau Miracast).
- **TV Miracast:** nyalakan TV. Saat diminta, setujui permintaan koneksi dengan remote.
- **TV Google Cast:** nyalakan TV, pastikan tersambung ke WiFi yang sama dengan laptop.

Kalau sedang membuka aplikasi cast lain (misalnya GNOME Network Displays), tutup dulu, karena
aplikasi itu memakai Wi-Fi Direct yang sama.

### Aplikasi IST Screen Cast (GUI)

1. Buka aplikasi, lalu tekan **Pindai**. Kalau aplikasi dipasang dari `.deb`, password tidak diminta.
2. Pilih perangkat. Miracast dan Google Cast tampil dalam satu daftar.
3. Pilih **Mirror** atau **Extend**, dan resolusi.
4. Tekan **Cast**. Kalau TV meminta persetujuan, setujui dengan remote.
5. Tekan **Stop** untuk berhenti. Pengaturan jaringan sementara dikembalikan otomatis.

Opsi **Advanced** sama dengan opsi terminal:

| Opsi | Sama dengan | Kapan berlaku |
|---|---|---|
| Interface WiFi | `--iface` | saat Pindai (terkunci selama sesi) |
| Tangkapan layar | `--capture` | saat Pindai (terkunci selama sesi) |
| Cara tersambung | `--join` / `--negotiate` | saat Cast (Miracast) |
| GO intent | `--go-intent` | saat Cast (Miracast) |
| Audio | `--audio` / `--no-audio` | saat Cast (Miracast) |
| Encoder hardware | `--hw` | saat Cast (Miracast) |
| Bitrate | `--bitrate` (kbit/s) | saat Cast (Miracast dan Google Cast) |

**Instalasi:** unduh `ist-screen-cast_<versi>_amd64.deb` dari halaman Releases di GitHub, lalu:

    sudo apt install ./ist-screen-cast_<versi>_amd64.deb

`apt` otomatis memasang semua dependensi. Script ikut terpasang di `/usr/lib/ist-screen-cast/`,
dan aplikasi muncul di menu dengan nama **IST Screen Cast**. Paket di-build di Ubuntu 22.04,
jadi jalan di Ubuntu 22.04 ke atas dan turunannya (Mint 21+, Pop!_OS 22.04+, elementary OS 7+).

Untuk menjalankan atau mem-build dari kode sumber, lihat
[Build dan menjalankan dari source](#build-dan-menjalankan-dari-source).

### Terminal: Miracast

    sudo bash cast.sh                          # pindai, pilih perangkat dan tampilan dari menu
    sudo bash cast.sh --peer "Nama Perangkat"  # langsung ke perangkat ini (nama atau MAC)
    sudo bash cast.sh --mirror | --extend      # tampilan, tanpa menu
    sudo bash cast.sh --mode 1080p30           # 480p60 | 720p30 (default) | 720p60 | 1080p30
    sudo bash cast.sh --join | --negotiate     # paksa cara tersambung (default: otomatis)
    sudo bash cast.sh --go-intent 0            # 0..15 (default 14), lihat Detail teknis
    sudo bash cast.sh --audio lpcm             # auto (default) | aac | lpcm;  --no-audio = tanpa suara
    sudo bash cast.sh --hw                     # coba encoder hardware, otomatis kembali ke openh264
    sudo bash cast.sh --iface wlp2s0           # interface WiFi tertentu (default: dideteksi otomatis)
    sudo bash cast.sh --capture portal         # paksa tangkapan layar lewat xdg-desktop-portal
    sudo bash cast.sh --check                  # hanya cek kesiapan laptop

Menu perangkat hanya menampilkan perangkat Miracast. Printer Wi-Fi Direct otomatis disaring.
Tekan Enter untuk memilih perangkat bertanda `*`. Nama default-nya diatur di `DEFAULT_NAME`
di `cast.sh`.

**Mirror** menampilkan layar laptop apa adanya. **Extend** menjadikan perangkat monitor
tambahan; atur posisinya di Settings → Displays.

Tekan Ctrl+C untuk berhenti dan membersihkan semuanya. Tekan Ctrl+C sekali lagi untuk keluar paksa.

### Terminal: Google Cast

    sudo bash gcast.sh                                   # pindai, pilih perangkat dari menu
    sudo bash gcast.sh --device "Living Room TV"         # langsung ke perangkat (nama atau IP)
    sudo bash gcast.sh --virtual                         # extend (monitor virtual)
    sudo bash gcast.sh --list                            # hanya tampilkan perangkat
    sudo bash gcast.sh --device "Living Room TV" --status-only   # uji koneksi tanpa membuka app di TV

`sudo` hanya dipakai untuk membuka port 8099 di firewall selama sesi. Delay lebih besar
daripada Miracast (beberapa detik), karena TV mem-buffer stream. Untuk slide dan dokumen
tidak masalah; untuk video, lebih baik pakai tombol Cast di situs/aplikasinya.

## Masalah umum

| Gejala | Coba |
|---|---|
| Perangkat tidak muncul | Projektor harus di input Screen Mirroring dan TV menyala. Dekatkan laptop, lalu Pindai ulang. Untuk Google Cast: laptop dan TV harus di WiFi yang sama. |
| Menunggu lama di "Setujui koneksi di TV" | Perhatikan pop-up di TV. Sebagian TV baru menyambung sekitar 1 menit setelah disetujui (batas 150 detik). Koneksi berikutnya ke TV yang sama jauh lebih cepat. |
| TV tertahan di layar "menyambung" | Tekan Batal di TV, lalu Cast lagi. |
| `--check` menampilkan `XX` | Jalankan perintah `apt install` yang ditampilkan. |
| Dialog "bagikan layar" muncul | Normal di desktop selain GNOME. Pilih layar, centang "ingat" kalau ada. |
| Ingin memilih layar lain, tapi dialog tidak muncul lagi | Hapus `~/.local/state/miracast-cast/portal-*.token`. |
| Setelah selesai, aplikasi cast lain tidak menemukan perangkat | `sudo systemctl restart NetworkManager` (WiFi putus beberapa detik). |
| Script/aplikasi sempat dimatikan paksa | Lihat [Perubahan sistem](#perubahan-sistem) untuk cara mengembalikan manual. |

Saat melapor masalah, sertakan log. Di aplikasi: buka bagian **Log**, lalu tekan **Salin**.
Di terminal: salin output lengkap.

## Perangkat yang sudah diuji

Pengujian dilakukan di laptop Ubuntu 22.04 (GNOME Wayland, NetworkManager 1.36, WiFi Intel AX211).

| Perangkat | Jalur | Hasil |
|---|---|---|
| TV LG webOS (UQ8050PSB) | Miracast, negosiasi | **Berhasil**: gambar dan suara, 720p30. Koneksi ulang lewat jalur cepat ±1 detik. |
| Projektor Epson (Screen Mirroring) | Miracast, join | Tersambung dan stream terkirim, tetapi **layar masih hitam**. Perbaikan format stream belum diuji ulang ke projektor. |
| TV TCL Android TV | Google Cast | Sudah diimplementasikan, **belum diuji** ke TV. |

Yang belum pernah diuji: distro/versi lain, chip WiFi non-Intel, encoder hardware, dan
desktop selain GNOME. Hasil dari laptop lain sangat membantu, termasuk output `--check`.

## Build dan menjalankan dari source

    git clone https://github.com/aamsur-933/ist-screen-cast.git
    cd ist-screen-cast

### Script (terminal): tanpa build

`cast.sh`, `gcast.sh`, dan file Python dijalankan langsung dari folder repo. Cukup pasang paket
di [Kebutuhan](#kebutuhan), lalu:

    sudo bash cast.sh --check     # cek kesiapan laptop
    sudo bash cast.sh             # Miracast
    sudo bash gcast.sh            # Google Cast

Perubahan pada script langsung berlaku di percobaan berikutnya.

### Aplikasi GUI: kebutuhan build

Selain paket di [Kebutuhan](#kebutuhan), siapkan:

- **Rust** (stable) lewat [rustup](https://rustup.rs);
- **Node.js 20+** dan npm;
- library pengembangan WebKitGTK:

      sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev build-essential

`libwebkit2gtk-4.1-dev` tersedia mulai Ubuntu 22.04 / Debian 12.

### Aplikasi GUI: menjalankan dari source

    cd gui
    npm ci          # sekali saja (memasang Tauri CLI di gui/node_modules)
    npm run dev     # compile lalu buka jendela IST Screen Cast

Compile pertama kali butuh sekitar 1–2 menit; berikutnya hanya beberapa detik. Di mode ini:

- GUI memakai script di **folder repo**, bukan `/usr/lib/ist-screen-cast/`, walaupun paket
  `.deb` juga terpasang. Perubahan pada `cast.sh` dan file Python berlaku di Pindai berikutnya.
- **Password tetap diminta** setiap Pindai, karena aturan polkit hanya berlaku untuk script
  yang dipasang dari paket.
- Perubahan di `gui/src-tauri/` otomatis di-compile ulang. Setelah mengubah `gui/ui/`, jalankan
  ulang `npm run dev`.
- Folder script lain bisa dipilih dengan variabel `IST_SCREENCAST_DIR=/path/ke/folder`.

### Aplikasi GUI: build paket `.deb` sendiri

    cd gui
    npm ci
    npx tauri build --bundles deb --config '{"version":"0.2.0"}'

Tanpa `--config`, versi diambil dari `gui/src-tauri/tauri.conf.json`. Build release butuh
beberapa menit, dan folder `gui/src-tauri/target/` bisa mencapai 2 GB. Hasilnya ada di
`gui/src-tauri/target/release/bundle/deb/`. Perhatikan bahwa nama file-nya mengandung spasi:

    sudo apt install "./src-tauri/target/release/bundle/deb/IST Screen Cast_0.2.0_amd64.deb"

Isi paket:

- aplikasi di `/usr/bin/ist-screen-cast`;
- script di `/usr/lib/ist-screen-cast/`;
- aturan polkit di `/usr/share/polkit-1/actions/`;
- ikon dan entri menu.

Cek isinya dengan `dpkg-deb --contents <file.deb>`.

### Uji tanpa projektor/TV

Server Miracast bisa diuji dengan projektor tiruan. Jalankan sebagai user biasa, tanpa sudo,
di dua terminal:

    python3 wfd_source.py --port 17236 --once    # terminal 1: server Miracast (tangkapan layar asli)
    python3 fake_sink.py 17236                   # terminal 2: tiruan projektor, melaporkan RTP yang diterima

Hasil yang benar: `SINK: received ... RTP bytes ... -> OK`. Untuk cek kesiapan tanpa root,
jalankan `python3 wfd_source.py --check`.

### Membuat rilis

Buat Release di GitHub dengan tag semver, misalnya `v0.2.0` atau `v0.2.0-beta.1`, lalu Publish.
Workflow `.github/workflows/release-deb.yml` akan:

1. mem-build `.deb` di Ubuntu 22.04 dengan versi dari tag;
2. mengecek isinya;
3. melampirkan `ist-screen-cast_<versi>_amd64.deb` ke Release itu (sekitar 10 menit).

Build juga bisa dijalankan manual dari tab **Actions → Release .deb → Run workflow**. Hasil
run manual tersimpan sebagai artifact, tidak ditempel ke Release.

## Detail teknis

### Cara tersambung Miracast

| Cara | Kapan | Contoh |
|---|---|---|
| `join` | Perangkat sudah menjadi Group Owner (`group_capab` bit 0) | projektor Epson |
| `negotiate` | Perangkat belum punya grup | TV LG webOS |

**join**: laptop masuk ke grup Wi-Fi Direct milik perangkat (`p2p_connect … pbc join`), lalu
meminta IP lewat DHCP dari perangkat. Projektor seperti Epson selalu menjalankan grupnya sendiri
dan menolak negosiasi grup baru (`status 1`).

**negotiate**: laptop mengirim Provision Discovery, lalu GO Negotiation dengan `go_intent=14`,
sehingga laptop yang menjadi Group Owner. Perangkat yang memaksa intent 15 tetap menjadi GO;
15 lawan 15 akan gagal dengan status 9. Laptop lalu memakai IP `192.168.49.1` dan menjalankan
server DHCP (`busybox udhcpd`) untuk perangkat. `--go-intent 0` meminta perangkat yang menjadi GO.

Yang ditemukan saat pengujian dengan TV LG webOS:

- TV menjawab `go_intent 0` (ingin menjadi client).
- TV menjawab `status 1` sampai pengguna menyetujui. Script mengirim ulang negosiasi setiap
  10 detik dan mendengarkan di channel TV (`listen_freq`).
- DHCP harus mengirim **router** (`option router`). Tanpa itu, TV mendapat IP tetapi tidak
  pernah menyambung ke RTSP.
- TV menyambung sekitar 70 detik setelah percobaan pertama, walaupun permintaan sudah disetujui
  lebih awal.

**Jalur cepat.** Grup hasil negosiasi dibuat *persistent*. SSID dan passphrase-nya disimpan di
`/var/lib/miracast-cast/<mac>.conf`. Koneksi berikutnya cukup mengirim **P2P Invitation**
(`p2p_invite persistent=…`): TV LG tersambung kurang dari 1 detik, dibanding sekitar 70 detik.
Kalau undangan tidak dijawab dalam 8 detik, script kembali ke negosiasi biasa. Kalau undangan
**ditolak** (misalnya TV di-reset), grup lama dan file credential-nya dihapus. Setelah negosiasi
baru berhasil, grup lama untuk perangkat yang sama dibuang.

### Kenapa tidak lewat NetworkManager saja

Aplikasi seperti GNOME Network Displays meminta NetworkManager untuk membuat koneksi Wi-Fi Direct.
Di NetworkManager yang diuji (1.36), cara itu tidak cukup:

- NetworkManager tidak bisa **join** ke grup yang sudah ada (dibutuhkan projektor).
- NetworkManager menghapus grup P2P yang tidak ia buat sendiri, lewat D-Bus `RemoveInterface`.
  Selama sesi, panggilan itu diblokir dengan aturan D-Bus sementara.
- NetworkManager tidak menyediakan P2P Invitation untuk jalur cepat.

Karena itu script berbicara langsung dengan wpa_supplicant, dan untuk itu butuh akses root.

### Stream Miracast

- Video H.264 Constrained Baseline. Audio AAC kalau perangkat mendukung; kalau tidak, LPCM.
  Contohnya projektor Epson yang hanya menerima LPCM.
- `mpegtsmux` menandai LPCM sebagai format DVD (`0x8b`/`0xfd`), padahal WFD butuh `0x83`/`0xbd`.
  Selain itu ia menambahkan descriptor Blu-ray `HDMV`. `TsPatcher` membangun ulang PAT/PMT
  seperti Android: PMT PID `0x100`, descriptor AVC `0x28` + `0x2a`, descriptor LPCM `0x83`,
  dan PID WFD standar (video `0x1011`, audio `0x1100`).
- Sebagian firmware mengirim data sampah di depan balasan RTSP. Parser membuangnya.
- Encoder default **openh264** (software), supaya hasilnya sama di semua laptop. `--hw` mencoba
  `vah264lpenc`, `vah264enc`, `vaapih264enc`, lalu `nvh264enc`. Setiap encoder diuji dulu dan
  harus menghasilkan Constrained Baseline; kalau tidak ada yang lolos, script kembali ke openh264.

### Tangkapan layar: Mutter atau xdg-desktop-portal

| | Mutter ScreenCast (utama) | xdg-desktop-portal ScreenCast (cadangan) |
|---|---|---|
| Desktop | GNOME; kemungkinan juga Gala (elementary OS) | KDE Plasma, wlroots (Sway/Hyprland), dll., asal backend portalnya terpasang |
| Dialog persetujuan | tidak ada | dialog "bagikan layar"; pengguna memilih layar |
| Persetujuan diingat | - | ya, kalau portal versi 4+ (token di `~/.local/state/miracast-cast/`) |
| Extend (monitor virtual) | ya (GNOME 42+) | hanya kalau portal desktop itu menyediakannya |

Mode `auto` (default) mencoba Mutter dulu, lalu portal. Backend yang dipakai tercatat di log
(`Tangkapan layar: ...`). Keduanya butuh sesi **Wayland**. Contohnya elementary OS 8, yang secara
default memakai X11: pilih "Secure Session" di layar login. Untuk mengecek apakah Mutter
ScreenCast tersedia:

    gdbus introspect --session --dest org.gnome.Mutter.ScreenCast \
      --object-path /org/gnome/Mutter/ScreenCast | grep -i version

### Google Cast

Laptop membuka koneksi kontrol Cast (TLS port 8009) dan menjalankan **Default Media Receiver**
(`CC1AD845`) di TV. Lalu TV diminta memutar `http://<laptop>:8099/screen.mp4`, yaitu tangkapan
layar yang di-stream dari laptop: H.264 (openh264) + AAC, dibungkus ffmpeg (`-c copy`, tanpa encode
ulang) menjadi MP4 terfragmentasi. Detail protokol ada di `docs/google-cast.md`.

- **Suara hanya di TV.** Selama cast, `gcast.py` membuat sink PulseAudio sementara
  (`ist_screencast`) dan menjadikannya output default, jadi speaker laptop diam. Saat Stop, output
  dikembalikan. Kalau proses mati tidak rapi, `gcast.sh`/GUI menjalankan `gcast.py --restore-audio`.
  Pakai `--local-audio` supaya suara tetap keluar di laptop juga.
- **Kenapa bukan WebM/VP8.** Format lama (`--codec vp8`) diputar TV TCL (Android TV) dengan
  status PLAYING tapi layar hitam dan tanpa suara.
- **Kenapa ffmpeg, bukan `mp4mux`.** `mp4mux` GStreamer 1.20 menulis satu `trun` per sampel, dan
  Chromium (dasar Default Media Receiver) hanya bisa mendekode frame pertama.

### Aplikasi GUI

GUI (Tauri v2) berjalan sebagai user biasa. Saat **Pindai**, GUI menjalankan `cast.sh --gui`
sebagai root lewat `pkexec`:

| Dipasang dari | Perintah | Password |
|---|---|---|
| paket `.deb` | `pkexec /usr/lib/ist-screen-cast/cast.sh --gui` | **tidak diminta** untuk user yang login di depan laptop |
| repo (`npm run dev`) | `pkexec /bin/bash <repo>/cast.sh --gui` | diminta setiap sesi |

Paket `.deb` memasang aturan polkit `/usr/share/polkit-1/actions/app.ist.screencast.policy`
(sumbernya `packaging/app.ist.screencast.policy`). Aturan itu berlaku hanya untuk program
`/usr/lib/ist-screen-cast/cast.sh`, dengan:

- `allow_active=yes`: sesi lokal yang aktif tidak ditanya password, sama seperti NetworkManager
  mengizinkan user mengatur WiFi.
- Sesi lain (SSH, sesi tidak aktif) tetap harus memasukkan password administrator.

Konsekuensinya, siapa pun yang login di laptop itu bisa menjalankan `cast.sh` sebagai root.
Karena itu argumen yang dipakai sebagai root dicek ketat: `--iface` harus nama interface yang
valid, `--go-intent` harus 0–15, dan perintah GUI hanya menerima nilai dari daftar yang
diizinkan. Script itu sendiri milik root di `/usr/lib`, jadi user tidak bisa mengubah isinya.

Di mode `--gui`, `cast.sh`:

1. menjalankan pengecekan yang sama dengan `--check`;
2. menyiapkan sistem, sama seperti mode terminal;
3. memindai Miracast dan Google Cast bersamaan;
4. mengirim daftar perangkat ke GUI (`@@DEV ...`), lalu menunggu perintah di stdin:
   `rescan`, `cast <miracast|gcast> <id> <mirror|extend> <mode> [opsi]`, atau `stop`.

Semua input dicek ulang di `cast.sh`. Proses root tidak bisa dihentikan dengan sinyal dari user
biasa, jadi tombol Stop mengirim `stop` (atau menutup stdin). Kalau GUI tertutup atau crash,
`cast.sh` tetap membersihkan semuanya.

## Perubahan sistem

Perubahan yang **menetap**:

- `/var/lib/miracast-cast/`: credential grup persistent untuk jalur cepat. Hapus folder ini untuk
  mengulang dari negosiasi biasa.
- `~/.local/state/miracast-cast/portal-*.token`: persetujuan dialog portal. File ini hanya ada
  kalau xdg-desktop-portal dipakai.
- Paket `.deb`: aplikasi, script di `/usr/lib/ist-screen-cast/`, dan aturan polkit. Hapus dengan
  `sudo apt remove ist-screen-cast`.

Perubahan **sementara**, otomatis dihapus saat berhenti:

- `/etc/NetworkManager/conf.d/99-p2p-manual.conf`: NetworkManager mengabaikan interface P2P.
  Selama file ini ada, aplikasi cast lain yang memakai NetworkManager tidak bisa dipakai.
- `/etc/dbus-1/system.d/zz-p2p-cast-temp.conf`: blokir `RemoveInterface`.
- Aturan iptables: TCP 7236 dan UDP 67 di interface `p2p-*`, serta TCP 8099 untuk Google Cast.
- `/run/p2p-cast-udhcpd.conf` dan lease file, hanya kalau laptop menjadi Group Owner.
- Grup Wi-Fi Direct.

Kalau script atau aplikasi sempat dimatikan paksa (misalnya laptop mati), kembalikan manual:

    sudo rm -f /etc/NetworkManager/conf.d/99-p2p-manual.conf && sudo nmcli general reload conf
    sudo rm -f /etc/dbus-1/system.d/zz-p2p-cast-temp.conf && sudo systemctl reload dbus
    sudo systemctl restart NetworkManager

Aturan iptables hilang sendiri setelah reboot.

## File

| File | Fungsi |
|---|---|
| `cast.sh` | Orkestrasi Miracast: pengecekan, NetworkManager, D-Bus, firewall, join/negosiasi, DHCP; juga mode `--gui` |
| `wfd_source.py` | Miracast source: tangkapan layar (Mutter / portal), RTSP M1–M7, GStreamer H.264 + LPCM/AAC |
| `gcast.sh` / `gcast.py` | Google Cast: firewall sementara, kontrol Cast, HTTP stream MP4 (H.264 + AAC), suara hanya di TV |
| `gui/` | Aplikasi IST Screen Cast (Tauri v2): `src-tauri/src/main.rs` (pkexec + stdin/stdout), `ui/` (HTML/CSS/JS) |
| `dhcp-hook.sh` | Hook udhcpc: hanya memasang IP, tanpa mengubah route/DNS |
| `fake_sink.py` | Projektor tiruan untuk uji lokal (`wfd_source.py --port 17236`, lalu `fake_sink.py 17236`) |
| `p2p-test.sh` | Uji join Wi-Fi Direct saja |
| `docs/google-cast.md` | Catatan protokol dan rencana Google Cast |
