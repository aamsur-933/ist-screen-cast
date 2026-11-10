# Google Cast (Chromecast built-in)

Untuk TV yang tidak mendukung Miracast, misalnya TV TCL Android TV. TV seperti ini hanya mengiklankan `_googlecast._tcp` dan tidak muncul di
Wi-Fi Direct. Laptop dan TV harus berada di WiFi yang sama.

Implementasi: `gcast.py` (protokol, stream, audio) dan `gcast.sh` (firewall sementara). Di GUI,
perangkat Google Cast muncul di daftar yang sama dengan Miracast (`cast.sh --gui` →
`gui_gcast`).

## Protokol

Alur protokol mengikuti GNOME Network Displays (`src/cc/`). Kodenya GPL-3, jadi hanya alurnya
yang ditiru, kodenya tidak disalin.

1. **Discovery**: mDNS `_googlecast._tcp` lewat `avahi-browse`. TXT record `fn` berisi nama,
   `md` berisi model.
2. **Kontrol**: TLS ke port **8009**. Sertifikatnya self-signed, jadi verifikasi dimatikan.
   Isinya pesan `CastMessage` (protobuf) dengan prefix panjang 4 byte big-endian. `CastMessage`
   hanya punya 7 field, jadi di-encode manual tanpa dependensi protobuf.
   - `urn:x-cast:com.google.cast.tp.connection`: `CONNECT` / `CLOSE`
   - `urn:x-cast:com.google.cast.tp.heartbeat`: `PING` / `PONG`
   - `urn:x-cast:com.google.cast.receiver`: `LAUNCH` app `CC1AD845` (Default Media Receiver),
     `GET_STATUS`, `STOP`
   - `urn:x-cast:com.google.cast.media`: `LOAD` dengan `streamType: LIVE`, `contentType: video/mp4`
     dan URL stream; TV mengirim `MEDIA_STATUS`
3. **Media**: HTTP server di laptop (`http://<laptop>:8099/screen.mp4`). Setiap `GET` memulai
   pipeline baru; request baru menggantikan stream yang sedang berjalan (TV boleh menyambung ulang).

## Format stream

    pipewiresrc (Mutter ScreenCast / xdg-desktop-portal)
      → videorate → videoscale → I420 <size>
      → openh264enc (bitrate = --bitrate × 1000 bit/s, gop = fps, usage-type=screen)
      → h264parse → Constrained Baseline, byte-stream ─────────────┐
    pulsesrc (monitor sink ist_screencast) → voaacenc 128 kbit/s ──┴→ mpegtsmux → appsink
      → ffmpeg -c copy -bsf:a aac_adtstoasc
               -movflags frag_keyframe+empty_moov+default_base_moof -flush_packets 1
      → MP4 terfragmentasi (moov kosong di awal, lalu moof+mdat tiap keyframe) → HTTP

- ffmpeg hanya membungkus ulang (`-c copy`), tidak meng-encode ulang. `-probesize 500000
  -analyzeduration 1000000` supaya data pertama keluar ±1–2 detik setelah request.
- `-bsf:a aac_adtstoasc` wajib: AAC dari MPEG-TS berformat ADTS. Tanpa filter ini ffmpeg berhenti
  setelah fragmen pertama (`Malformed AAC bitstream detected`).
- Format lama tetap ada: `--codec vp8` → `/screen.webm`, VP8 (`vp8enc`, CBR) + Opus dalam WebM.

### Kenapa bukan WebM/VP8

Diuji ke TV TCL (2026-11-10): TV melaporkan `playerState: PLAYING` dan `currentTime` terus maju,
tetapi layar hitam dan tidak ada suara. `MEDIA_STATUS` tidak pernah berisi `videoInfo`. File WebM
yang sama terbaca normal oleh ffmpeg dan GStreamer di laptop. Gejala yang sama (WebM/VP8 ke
Chromecast: layar kosong) juga dilaporkan di merge request GNOME Network Displays, dan VLC
berhenti memakai WebM sebagai default untuk Chromecast.

Selain itu ada bug satuan: `vp8enc target-bitrate` dalam **bit/s**, sedangkan dulu diisi
kbit/s (6000 = 6 kbit/s, gambar sangat buram). Sudah diperbaiki (× 1000).

### Kenapa ffmpeg, bukan `mp4mux`

`mp4mux` GStreamer 1.20 dengan `fragment-duration` (mode `dash-or-mss`) menulis satu `trun`
per sampel di setiap `traf`. GStreamer bisa membacanya, tetapi:

- ffmpeg 4.4: `Invalid NAL unit size (1835295092 > …)` (`1835295092` = teks `mdat`, offset sampel
  salah);
- Chrome (pemutaran progresif `<video src>`): frame pertama tampil, lalu
  `PIPELINE_ERROR_DECODE: Failed to send video packet for decoding`.

Mode `first-moov-then-finalise` terbaca benar, tetapi `moov` baru ditulis di akhir (butuh seek),
jadi tidak bisa untuk stream live. Hasil ffmpeg di Chrome: 426 frame terdekode, 0 drop, audio
terdekode, buffer ±1,7 detik.

## Audio: hanya di TV

Selama cast (`TvAudio` di `gcast.py`):

1. Sink sisa sesi yang mati tidak rapi dihapus, kecuali monitornya sedang direkam sesi lain.
2. `module-null-sink sink_name=ist_screencast rate=48000 channels=2` dibuat, lalu
   **di-unmute dan volume 100%**.
3. Sink itu dijadikan output default, dan semua sink-input yang ada dipindahkan ke sana. Aplikasi
   yang mulai bersuara kemudian otomatis ikut ke sink default.
4. Stream mengambil audio dari `ist_screencast.monitor`; speaker laptop diam.
5. Saat berhenti: output default dikembalikan, sink-input dipindahkan balik, modul di-unload.
   `gcast.sh` dan cleanup `cast.sh` (GUI) juga menjalankan `gcast.py --restore-audio` setelah
   `gcast.py` keluar, untuk berjaga-jaga.

Yang ditemukan saat pengujian:

- PulseAudio `module-device-restore` mengingat volume/mute per **nama** sink. Mute dari sesi
  sebelumnya (tombol mute laptop saat sink ini menjadi default) membuat TV senyap di sesi
  berikutnya. Karena itu langkah 2 selalu mereset mute dan volume.
- Monitor sink ikut volume dan mute sink, termasuk sink ALSA asli. "Mute speaker laptop tapi
  tetap rekam monitor-nya" tidak bisa dipakai; harus lewat sink terpisah.
- Selama cast, tombol volume/mute laptop mengatur sink `ist_screencast`, jadi mengatur suara ke TV.
  Mute dicatat di log (`Suara ke TV di-mute dari laptop`) dan ditampilkan di GUI.
- `--local-audio`: tanpa pengalihan; stream mengambil monitor output default (suara di laptop dan TV).

## Diagnosis

- Log `TV: video <lebar>x<tinggi>` berasal dari `videoInfo` di `MEDIA_STATUS`: TV benar-benar
  mendekode video. `PLAYING` tanpa `videoInfo` bisa berarti hanya timeline yang jalan (layar hitam).
- Status TV bisa dibaca tanpa mengganggu sesi lewat koneksi Cast kedua: `CONNECT` ke `receiver-0`,
  `GET_STATUS`, lalu `CONNECT` ke `transportId` aplikasi dan `GET_STATUS` di namespace media.
  Hasilnya: `playerState`, `currentTime`, `videoInfo`, volume receiver dan media.
- Laju data ke TV: `ss -tni` pada port 8099 (`bytes_acked`, `delivery_rate`).
- Uji lokal tanpa TV: jalankan `StreamServer` dari `gcast.py`, buka
  `http://127.0.0.1:<port>/screen.mp4` di Chrome (`<video>` + `getVideoPlaybackQuality()`,
  Web Audio analyser untuk level suara). Jangan jalankan uji yang membuat sink `ist_screencast`
  saat ada sesi cast yang hidup.

## Status

- [x] Discovery mDNS (`avahi-browse`), menu, `--device`, `--list`, `--tsv` (untuk GUI)
- [x] Cast channel: TLS, `CastMessage` di-encode manual, framing, heartbeat PING/PONG
- [x] `LAUNCH CC1AD845`, `CONNECT` ke app, `LOAD` LIVE, `STOP` saat berhenti
- [x] Stream H.264 + AAC dalam MP4 terfragmentasi (ffmpeg remux); WebM/VP8 sebagai `--codec vp8`
- [x] Uji ke TV TCL: gambar dan suara (480p dan 720p, `videoInfo` sesuai)
- [x] Suara hanya di TV, reset mute/volume, `--restore-audio`, `--local-audio`
- [x] Integrasi ke GUI (daftar perangkat yang sama dengan Miracast)
- [x] Extend (`--virtual`): monitor virtual lewat Mutter ScreenCast, sama dengan Miracast
      (terlihat di journal: `Added virtual monitor Meta-0` saat sesi Google Cast). Lewat
      xdg-desktop-portal tanpa monitor virtual tidak bisa; GUI lalu hanya menawarkan Mirror
- [ ] Menu terminal `cast.sh` (sekarang terminal memakai `gcast.sh` terpisah)
- [ ] Ukur delay (stopwatch) dan kelancaran. Sempat terlihat `currentTime` tertahan ±15 detik
      walau status PLAYING dan data tetap mengalir ±5 Mbit/s; perlu dicek di layar TV
- [ ] Tuning bitrate dan interval keyframe untuk TV yang sering BUFFERING
