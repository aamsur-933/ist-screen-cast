#!/usr/bin/env python3
"""Cast the desktop to a Google Cast (Chromecast built-in) device, e.g. Android TV.

How it works (same idea as GNOME Network Displays' src/cc, protocol only, no code copied):
  1. Discovery: mDNS _googlecast._tcp (avahi-browse).
  2. Control: TLS to port 8009, CastMessage (protobuf, encoded by hand below) with a 4-byte
     big-endian length prefix. CONNECT -> LAUNCH Default Media Receiver (CC1AD845) ->
     CONNECT to the app -> LOAD a LIVE stream URL.
  3. Media: this script serves http://<laptop>:<port>/screen.webm; every request gets a fresh
     GStreamer pipeline (screen capture (Mutter ScreenCast or xdg-desktop-portal) -> VP8 + Opus -> WebM).

Run via gcast.sh (opens the HTTP port in the firewall), or directly as the desktop user.
"""
import argparse
import itertools
import json
import os
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# python3 -I (used by gcast.sh) does not put the script's directory on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wfd_source import GLib, Gst, ScreenCast, default_monitor, log  # noqa: E402

APP_ID = "CC1AD845"  # Default Media Receiver
NS_CONN = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
SENDER = "sender-0"


# --------------------------------------------------------------------------- discovery
def discover(timeout=5):
    """[(name, model, ip, port)] of Google Cast devices on the LAN."""
    try:
        out = subprocess.run(["avahi-browse", "-rpt", "_googlecast._tcp"], capture_output=True,
                             text=True, timeout=timeout + 5).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log(f"avahi-browse gagal: {e}")
        return []
    found = {}
    for line in out.splitlines():
        f = line.split(";")
        if len(f) < 10 or f[0] != "=" or f[2] != "IPv4":
            continue
        txt = {}
        for item in f[9].split('" "'):
            k, _, v = item.strip('"').partition("=")
            txt[k] = v
        ip, port = f[7], int(f[8])
        found[ip] = (txt.get("fn") or f[3], txt.get("md", "?"), ip, port)
    return sorted(found.values())


# --------------------------------------------------------------------------- CastMessage (protobuf)
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field_str(num, s):
    b = s.encode()
    return _varint((num << 3) | 2) + _varint(len(b)) + b


def encode_msg(src, dst, ns, payload):
    """CastMessage: 1 protocol_version=0, 2 source_id, 3 destination_id, 4 namespace,
    5 payload_type=0 (STRING), 6 payload_utf8."""
    return (_varint((1 << 3) | 0) + _varint(0) + _field_str(2, src) + _field_str(3, dst)
            + _field_str(4, ns) + _varint((5 << 3) | 0) + _varint(0) + _field_str(6, payload))


def decode_msg(data):
    fields, i = {}, 0
    while i < len(data):
        key, shift = 0, 0
        while True:
            b = data[i]; i += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, shift = 0, 0
            while True:
                b = data[i]; i += 1
                v |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            fields[num] = v
        elif wt == 2:
            n, shift = 0, 0
            while True:
                b = data[i]; i += 1
                n |= (b & 0x7F) << shift; shift += 7
                if not b & 0x80:
                    break
            fields[num] = data[i:i + n]; i += n
        else:
            raise ValueError(f"wire type {wt} tidak didukung")
    s = lambda k: fields.get(k, b"").decode(errors="replace")  # noqa: E731
    return {"src": s(2), "dst": s(3), "ns": s(4), "payload": s(6)}


class CastChannel:
    def __init__(self, ip, port=8009, verbose=False):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # Cast devices use self-signed certificates
        raw = socket.create_connection((ip, port), timeout=10)
        self.sock = ctx.wrap_socket(raw, server_hostname=ip)
        self.verbose = verbose
        self.req = itertools.count(1)
        self.lock = threading.Lock()
        self.alive = True

    def send(self, dst, ns, obj):
        payload = json.dumps(obj)
        if self.verbose:
            log(f">> {dst} {ns.rsplit('.', 1)[-1]} {payload[:200]}")
        data = encode_msg(SENDER, dst, ns, payload)
        with self.lock:
            self.sock.sendall(struct.pack(">I", len(data)) + data)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("TV menutup koneksi Cast")
            buf += chunk
        return buf

    def recv(self, timeout=None):
        self.sock.settimeout(timeout)
        n = struct.unpack(">I", self._recv_exact(4))[0]
        msg = decode_msg(self._recv_exact(n))
        try:
            msg["json"] = json.loads(msg["payload"])
        except ValueError:
            msg["json"] = {}
        if msg["ns"] == NS_HEARTBEAT and msg["json"].get("type") == "PING":
            self.send(msg["src"], NS_HEARTBEAT, {"type": "PONG"})
        elif self.verbose and msg["ns"] != NS_HEARTBEAT:
            log(f"<< {msg['src']} {msg['ns'].rsplit('.', 1)[-1]} {msg['payload'][:300]}")
        return msg

    def wait_for(self, pred, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self.recv(max(0.2, end - time.time()))
            except socket.timeout:
                break
            if pred(msg):
                return msg
        return None

    def heartbeat_loop(self):
        while self.alive:
            try:
                self.send("receiver-0", NS_HEARTBEAT, {"type": "PING"})
            except OSError:
                return
            time.sleep(5)

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- HTTP stream server
class StreamServer:
    """Serves /screen.webm. Each GET starts a fresh pipeline (WebM header first); a new request
    replaces the running one (the TV may reconnect)."""

    def __init__(self, cast, port, w, h, fps, bitrate):
        self.cast, self.w, self.h, self.fps, self.bitrate = cast, w, h, fps, bitrate
        self.current = None
        self.cur_lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # close-delimited body, no Content-Length

            def log_message(self, fmt, *a):
                log("http: " + fmt % a)

            def _headers(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/webm")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

            def do_HEAD(self):
                if self.path.split("?")[0] != "/screen.webm":
                    return self.send_error(404)
                self._headers()

            def do_GET(self):
                if self.path.split("?")[0] != "/screen.webm":
                    return self.send_error(404)
                self._headers()
                outer.stream_to(self.wfile, self.client_address[0])

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def describe(self):
        return (
            f"{self.cast.pipewire_src()} do-timestamp=true keepalive-time=500 ! "
            f"videorate ! video/x-raw,framerate={self.fps}/1 ! videoconvert ! videoscale ! "
            f"video/x-raw,format=I420,width={self.w},height={self.h},pixel-aspect-ratio=1/1 ! "
            "queue max-size-buffers=3 leaky=downstream ! "
            f"vp8enc deadline=1 cpu-used=8 threads=4 end-usage=cbr target-bitrate={self.bitrate} "
            f"keyframe-max-dist={self.fps * 2} error-resilient=default ! queue ! mux. "
            f"pulsesrc device={default_monitor()} do-timestamp=true ! audioconvert ! audioresample ! "
            "audio/x-raw,rate=48000,channels=2 ! opusenc bitrate=128000 ! queue ! mux. "
            "webmmux name=mux streamable=true ! appsink name=out sync=false max-buffers=300"
        )

    def stream_to(self, wfile, client):
        pipe = Gst.parse_launch(self.describe())
        sink = pipe.get_by_name("out")
        with self.cur_lock:
            if self.current is not None:
                self.current.set_state(Gst.State.NULL)  # only one live stream at a time
            self.current = pipe
        pipe.set_state(Gst.State.PLAYING)
        log(f"Streaming ke {client}")
        sent = 0
        try:
            while self.current is pipe:
                sample = sink.emit("try-pull-sample", Gst.SECOND)
                if sample is None:
                    if sink.get_property("eos"):
                        break
                    continue
                buf = sample.get_buffer()
                ok, info = buf.map(Gst.MapFlags.READ)
                if ok:
                    wfile.write(bytes(info.data))
                    sent += info.size
                    buf.unmap(info)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            pipe.set_state(Gst.State.NULL)
            with self.cur_lock:
                if self.current is pipe:
                    self.current = None
            log(f"Stream ke {client} selesai ({sent / 1e6:.1f} MB)")

    def close(self):
        with self.cur_lock:
            if self.current is not None:
                self.current.set_state(Gst.State.NULL)
                self.current = None
        self.httpd.shutdown()


# --------------------------------------------------------------------------- main
def local_ip_for(ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def pick_device(devices, wanted):
    if wanted:
        for d in devices:
            if wanted.lower() in (d[0].lower(), d[2]):
                return d
        log(f"Perangkat '{wanted}' tidak ditemukan")
        return None
    print()
    for i, (name, model, ip, _) in enumerate(devices, 1):
        print(f"   {i}) {name:<24} {ip:<15} model {model}")
    print()
    with open("/dev/tty") as tty:
        sys.stdout.write("Pilih nomor perangkat [Enter = 1]: ")
        sys.stdout.flush()
        ans = tty.readline().strip() or "1"
    if not ans.isdigit() or not 1 <= int(ans) <= len(devices):
        log(f"Pilihan tidak valid: {ans}")
        return None
    return devices[int(ans) - 1]


def main():
    ap = argparse.ArgumentParser(description="Cast desktop ke perangkat Google Cast")
    ap.add_argument("--device", help="nama perangkat (fn) atau IP; tanpa ini muncul menu")
    ap.add_argument("--list", action="store_true", help="hanya tampilkan perangkat")
    ap.add_argument("--virtual", action="store_true", help="extend (monitor virtual) bukan mirror")
    ap.add_argument("--port", type=int, default=8099, help="port HTTP stream di laptop")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bitrate", type=int, default=6000, help="VP8 kbit/s")
    ap.add_argument("--status-only", action="store_true",
                    help="uji koneksi Cast (CONNECT + GET_STATUS), tanpa membuka aplikasi di TV")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--capture", choices=["auto", "mutter", "portal"], default="auto",
                    help="tangkapan layar: auto = Mutter dulu, portal kalau tidak ada")
    args = ap.parse_args()
    os.environ["MIRACAST_CAPTURE"] = args.capture

    log("Mencari perangkat Google Cast...")
    devices = discover()
    if not devices:
        log("Tidak ada perangkat Google Cast di jaringan ini")
        return 1
    if args.list:
        for name, model, ip, port in devices:
            print(f"{name} | {model} | {ip}:{port}")
        return 0
    dev = pick_device(devices, args.device)
    if not dev:
        return 1
    name, model, ip, port = dev
    log(f"Perangkat: {name} ({model}) {ip}:{port}")

    ch = CastChannel(ip, port, verbose=args.verbose)
    ch.send("receiver-0", NS_CONN, {"type": "CONNECT"})
    threading.Thread(target=ch.heartbeat_loop, daemon=True).start()
    if args.status_only:
        rid = next(ch.req)
        ch.send("receiver-0", NS_RECEIVER, {"type": "GET_STATUS", "requestId": rid})
        st = ch.wait_for(lambda m: m["json"].get("type") == "RECEIVER_STATUS", 10)
        if not st:
            log("Tidak ada RECEIVER_STATUS dari perangkat")
            return 1
        status = st["json"].get("status", {})
        apps = [a.get("displayName") for a in status.get("applications", [])]
        log(f"Koneksi Cast OK. Volume {status.get('volume', {}).get('level')}, aplikasi aktif: {apps or '-'}")
        ch.close()
        return 0

    w, h = (int(x) for x in args.size.split("x"))
    cast = ScreenCast(args.virtual)
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()  # ScreenCast D-Bus signals
    for _ in range(50):
        if cast.node is not None:
            break
        time.sleep(0.1)
    if cast.node is None:
        log("Tangkapan layar tidak memberi PipeWire node")
        return 1
    srv = StreamServer(cast, args.port, w, h, args.fps, args.bitrate)
    url = f"http://{local_ip_for(ip)}:{args.port}/screen.webm"
    log(f"Stream tersedia di {url}")

    rid = next(ch.req)
    ch.send("receiver-0", NS_RECEIVER, {"type": "LAUNCH", "appId": APP_ID, "requestId": rid})
    st = ch.wait_for(lambda m: m["json"].get("type") == "RECEIVER_STATUS" and any(
        a.get("appId") == APP_ID for a in m["json"].get("status", {}).get("applications", [])), 20)
    if not st:
        log("TV tidak membuka Default Media Receiver")
        return 1
    app = next(a for a in st["json"]["status"]["applications"] if a.get("appId") == APP_ID)
    transport, session = app["transportId"], app["sessionId"]
    ch.send(transport, NS_CONN, {"type": "CONNECT"})
    rid = next(ch.req)
    ch.send(transport, NS_MEDIA, {
        "type": "LOAD", "requestId": rid, "sessionId": session, "autoplay": True, "currentTime": 0,
        "media": {"contentId": url, "contentType": "video/webm", "streamType": "LIVE",
                  "metadata": {"metadataType": 0, "title": f"{socket.gethostname()} (desktop)"}},
    })
    log("Perintah LOAD terkirim; TV akan mengambil stream dari laptop. Ctrl+C untuk berhenti.")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def reader():
        while not stop.is_set():
            try:
                m = ch.recv(1.0)
            except socket.timeout:
                continue
            except (OSError, ConnectionError) as e:
                log(f"Koneksi Cast putus: {e}")
                stop.set()
                return
            t = m["json"].get("type")
            if t in ("LOAD_FAILED", "LOAD_CANCELLED", "INVALID_REQUEST", "ERROR"):
                log(f"TV melaporkan {t}: {m['payload'][:300]}")
            elif t == "MEDIA_STATUS":
                for s in m["json"].get("status", []):
                    log(f"TV: {s.get('playerState')}"
                        + (f" ({s['idleReason']})" if s.get("idleReason") else ""))
            elif t == "CLOSE" and m["ns"] == NS_CONN:
                log("TV menutup sesi")
                stop.set()

    threading.Thread(target=reader, daemon=True).start()
    while not stop.is_set():
        stop.wait(0.5)

    log("Berhenti...")
    threading.Timer(3.0, lambda: os._exit(1)).start()
    try:
        ch.send("receiver-0", NS_RECEIVER, {"type": "STOP", "sessionId": session, "requestId": next(ch.req)})
    except OSError:
        pass
    srv.close()
    ch.close()
    cast.stop()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
