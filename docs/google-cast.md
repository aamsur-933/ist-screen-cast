# Rencana: dukungan Google Cast (Chromecast built-in)

Untuk TV yang tidak mendukung Miracast, misalnya TV TCL Android TV ("Living Room TV",
`192.168.32.57`). TV seperti ini hanya mengiklankan `_googlecast._tcp` dan tidak muncul di
Wi-Fi Direct.

## Pendekatan

Mengikuti pendekatan GNOME Network Displays (`src/cc/`). Kodenya GPL-3, jadi hanya alur
protokolnya yang ditiru, kodenya tidak disalin:

1. **Discovery**: mDNS `_googlecast._tcp`. TXT record `fn` berisi nama, `md` berisi model.
2. **Kontrol**: TLS ke port **8009**. Sertifikatnya self-signed, jadi verifikasi dimatikan.
   Isinya pesan `CastMessage` (protobuf) dengan prefix panjang 4 byte big-endian.
   - `urn:x-cast:com.google.cast.tp.connection`: `CONNECT` / `CLOSE`
   - `urn:x-cast:com.google.cast.tp.heartbeat`: `PING` / `PONG`
   - `urn:x-cast:com.google.cast.receiver`: `LAUNCH` app `CC1AD845` (Default Media Receiver)
   - `urn:x-cast:com.google.cast.media`: `LOAD` dengan `streamType: LIVE` dan URL stream
3. **Media**: HTTP server di laptop, berisi tangkapan layar GNOME (ScreenCast, sama seperti
   `wfd_source.py`) yang di-encode lalu dimux, misalnya WebM (VP8 + Opus) atau MP4 (H.264 + AAC).

Konsekuensinya, delay akan lebih besar daripada Miracast (kemungkinan beberapa detik), karena
TV mem-buffer stream HTTP. Untuk slide dan dokumen tidak masalah; untuk video, lebih baik
memakai tombol Cast di situsnya.

## Yang sudah dicek di laptop ini

- Port 8009 di TV TCL terbuka, dengan TLS 1.3 dan sertifikat self-signed.
- GStreamer: `vp8enc`, `webmmux`, `matroskamux`, `mp4mux`, `opusenc`, `vorbisenc` tersedia.
- `python3-protobuf` 3.12.4 terpasang. Alternatifnya, `CastMessage` di-encode manual
  karena hanya punya 7 field, sehingga tidak perlu dependensi tambahan.

## Langkah kerja

1. Discovery + koneksi TLS + `CONNECT` / `PING` (uji: TV merespons `PONG`).
2. `LAUNCH CC1AD845` (uji: TV membuka receiver kosong).
3. `LOAD` sebuah file uji dari HTTP server laptop (uji: TV memutar file).
4. Ganti file uji dengan stream tangkapan layar live (pilih container yang paling stabil).
5. Integrasi ke `cast.sh`: perangkat Google Cast muncul di menu yang sama dengan
   label `[googlecast]`.

## Belum diketahui

- Container dan codec yang paling stabil untuk `LIVE` di Android TV TCL.
- Delay sebenarnya: perlu diukur dengan stopwatch, seperti yang disarankan untuk Miracast.

## Status implementasi (`gcast.py`, `gcast.sh`)

- [x] Discovery mDNS (`avahi-browse`), menu, `--device`, `--list`
- [x] Cast channel: TLS, `CastMessage` di-encode manual, framing, heartbeat PING/PONG
- [x] `LAUNCH CC1AD845`, `CONNECT` ke app, `LOAD` LIVE, `STOP` saat Ctrl+C
- [x] HTTP stream WebM (VP8 + Opus) dari ScreenCast; diuji lokal: file valid, terbaca GStreamer
- [ ] Uji ke TV TCL asli (saat implementasi TV sedang mati/standby, tidak terjangkau)
- [ ] Integrasi ke menu `cast.sh`
