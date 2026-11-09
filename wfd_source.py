#!/usr/bin/env python3
"""Minimal Wi-Fi Display (Miracast) source for GNOME Wayland.

- Screen capture: org.gnome.Mutter.ScreenCast (RecordMonitor = mirror, RecordVirtual = extend)
- Control: WFD RTSP (M1..M7 + keepalive) on TCP :7236, the sink connects to us
- Media: H.264 (openh264) + AAC (voaacenc) in MPEG-TS over RTP/UDP to the sink

Run as the desktop user (not root), after the Wi-Fi Direct link is up (or before; it waits).
"""
import argparse
import os
import re
import signal
import subprocess
import socket
import sys
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gio, GLib, Gst, GstVideo  # noqa: E402

Gst.init(None)

# WFD CEA resolution bitmap: bit -> (w, h, fps, min H.264 level bit)
# level bits: 0x01=3.1 0x02=3.2 0x04=4 0x08=4.1 0x10=4.2
CEA = {
    0: (640, 480, 60, 0x01),
    5: (1280, 720, 30, 0x01),
    6: (1280, 720, 60, 0x02),
    7: (1920, 1080, 30, 0x04),
    8: (1920, 1080, 60, 0x08),
}
PREFERENCE = {"720p30": [5, 0], "720p60": [6, 5, 0], "1080p30": [7, 5, 0], "480p60": [0]}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# --------------------------------------------------------------------------- screencast
class ScreenCast:
    BUS = "org.gnome.Mutter.ScreenCast"

    def __init__(self, virtual):
        self.node = None
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def proxy(path, iface):
            return Gio.DBusProxy.new_sync(bus, 0, None, self.BUS, path, iface, None)

        sc = proxy("/org/gnome/Mutter/ScreenCast", "org.gnome.Mutter.ScreenCast")
        sess_path = sc.call_sync("CreateSession", GLib.Variant("(a{sv})", ({},)), 0, -1, None).unpack()[0]
        self.session = proxy(sess_path, "org.gnome.Mutter.ScreenCast.Session")
        props = {"cursor-mode": GLib.Variant("u", 1)}  # 1 = cursor embedded in the video
        if virtual:
            res = self.session.call_sync("RecordVirtual", GLib.Variant("(a{sv})", (props,)), 0, -1, None)
        else:
            # empty connector = primary monitor
            res = self.session.call_sync("RecordMonitor", GLib.Variant("(sa{sv})", ("", props)), 0, -1, None)
        self.stream = proxy(res.unpack()[0], "org.gnome.Mutter.ScreenCast.Stream")
        self.stream.connect("g-signal", self._on_signal)
        self.session.call_sync("Start", None, 0, -1, None)

    def _on_signal(self, _proxy, _sender, name, params):
        if name == "PipeWireStreamAdded":
            self.node = params.unpack()[0]
            log(f"ScreenCast: PipeWire node {self.node}")

    def stop(self):
        try:
            self.session.call_sync("Stop", None, 0, -1, None)
        except GLib.Error:
            pass


# --------------------------------------------------------------------------- media
def default_monitor():
    """Monitor source of the current default output (what the laptop is playing).
    'pulsesrc device=@DEFAULT_MONITOR@' is not resolved on this system and falls back to the
    default *input* (the microphone), so look the monitor name up explicitly."""
    try:
        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True,
                              timeout=3).stdout.strip()
        sources = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True,
                                 text=True, timeout=3).stdout
        mon = f"{sink}.monitor"
        if sink and mon in sources.split():
            log(f"Audio dari: {mon}")
            return mon
    except (OSError, subprocess.SubprocessError):
        pass
    log("Audio: monitor speaker tidak ditemukan, pakai @DEFAULT_MONITOR@")
    return "@DEFAULT_MONITOR@"


def crc32_mpeg(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
    return crc


class TsPatcher:
    """Make mpegtsmux output match Android's Wi-Fi Display TSPacketizer (AOSP 8.1):
    - PMT moved to PID 0x0100 (PAT rewritten, CRC recomputed).
    - PMT rebuilt with Android's descriptors only (mpegtsmux's Blu-ray 'HDMV' registration
      descriptor is dropped; under it stream_type 0x83 would mean Dolby TrueHD):
        H.264: AVC video descriptor 0x28 (profile/constraints/level from our SPS, 0x3f)
               + AVC timing and HRD descriptor 0x2a (7e 1f)
        LPCM : LPCM audio descriptor 0x83 (48 kHz, stereo: 83 02 46 2f)
    - LPCM: stream_type 0x8b -> 0x83, PES stream_id 0xfd -> 0xbd (private_stream_1).
    PCR stays on the video PID (Android uses a separate PCR PID; both are valid MPEG-TS)."""

    PMT_PID = 0x0100
    VIDEO_PID = 0x1011

    def __init__(self):
        self.src_pmt_pid = None
        self.audio_pid = None
        self.sps = bytes([0x42, 0xC0, 0x1F])  # profile 66, constrained, level 3.1 until seen in the stream

    @staticmethod
    def _section_put(b, i, new):
        """Rewrite the whole TS packet at offset i as: header (payload only) + pointer_field 0 + section.
        mpegtsmux right-aligns PSI after adaptation-field stuffing, so the rebuilt (longer) section
        would not fit in place; Android writes PSI packets exactly like this (no adaptation field).
        The packet size stays 188 bytes."""
        new_len = len(new) - 3 + 4
        new[1] = (new[1] & 0xF0) | ((new_len >> 8) & 0x0F)
        new[2] = new_len & 0xFF
        new += crc32_mpeg(new).to_bytes(4, "big")
        payload = b"\x00" + bytes(new)
        assert len(payload) <= 184, "PSI section too long for one TS packet"
        b[i + 3] = (b[i + 3] & 0xCF) | 0x10  # adaptation_field_control = 01 (payload only), keep CC
        b[i + 4:i + 188] = payload + b"\xFF" * (184 - len(payload))

    def _descriptors(self, stype):
        if stype == 0x1B:
            return bytes([0x28, 4]) + self.sps + bytes([0x3F]) + bytes([0x2A, 2, 0x7E, 0x1F])
        if stype == 0x83:
            return bytes([0x83, 2, (2 << 5) | (3 << 1), (1 << 5) | 0x0F])  # 48 kHz, stereo
        return b""

    def patch(self, data):
        b = bytearray(data)
        for i in range(0, len(b) - 187, 188):
            if b[i] != 0x47:
                continue
            pid = ((b[i + 1] & 0x1F) << 8) | b[i + 2]
            pusi = b[i + 1] & 0x40
            afc = (b[i + 3] >> 4) & 3
            if pid == self.src_pmt_pid and self.src_pmt_pid != self.PMT_PID:
                b[i + 1] = (b[i + 1] & 0xE0) | (self.PMT_PID >> 8)  # move PMT packets to 0x0100
                b[i + 2] = self.PMT_PID & 0xFF
                pid = self.PMT_PID
            if not (afc & 1) or not pusi:
                continue
            off = i + 4 + ((1 + b[i + 4]) if afc & 2 else 0)
            pkt_end = i + 188
            if pid == 0:  # PAT: point program 1 at PMT_PID
                s = off + 1 + b[off]
                end = s + 3 + (((b[s + 1] & 0x0F) << 8) | b[s + 2]) - 4
                new = bytearray(b[s:s + 8])
                j = s + 8
                while j + 4 <= end:
                    prog = (b[j] << 8) | b[j + 1]
                    if prog != 0:
                        if self.src_pmt_pid is None:
                            self.src_pmt_pid = ((b[j + 2] & 0x1F) << 8) | b[j + 3]
                        new += bytes([b[j], b[j + 1], 0xE0 | (self.PMT_PID >> 8), self.PMT_PID & 0xFF])
                    else:
                        new += b[j:j + 4]
                    j += 4
                self._section_put(b, i, new)
            elif pid == self.PMT_PID:
                s = off + 1 + b[off]  # section start (table_id)
                end = s + 3 + (((b[s + 1] & 0x0F) << 8) | b[s + 2]) - 4  # start of CRC
                if end + 4 > pkt_end:
                    continue  # PMT spans packets; not expected for 2 streams
                pil = ((b[s + 10] & 0x0F) << 8) | b[s + 11]
                es = bytearray()
                j = s + 12 + pil
                while j + 5 <= end:
                    stype = b[j]
                    if stype == 0x8B:
                        stype = 0x83
                        self.audio_pid = ((b[j + 1] & 0x1F) << 8) | b[j + 2]
                    d = self._descriptors(stype)
                    es += bytes([stype, b[j + 1], b[j + 2], 0xF0 | (len(d) >> 8), len(d) & 0xFF]) + d
                    j += 5 + (((b[j + 3] & 0x0F) << 8) | b[j + 4])
                # header up to PCR_PID, program_info_length = 0, ES loop with Android descriptors
                self._section_put(b, i, bytearray(b[s:s + 10]) + bytes([0xF0, 0x00]) + es)
            elif pid == self.VIDEO_PID:
                k = b.find(b"\x00\x00\x01\x67", off, pkt_end - 4)  # SPS NAL in this packet
                if k >= 0:
                    self.sps = bytes(b[k + 4:k + 7])
            elif pid == self.audio_pid and b[off:off + 3] == b"\x00\x00\x01" and b[off + 3] == 0xFD:
                b[off + 3] = 0xBD
        return bytes(b)


# --------------------------------------------------------------------------- video encoders
# Default: openh264 (software). Same output on every laptop, Constrained Baseline, proven on LG.
# --hw: try hardware encoders in this order; each is probed with a short test pipeline that must
# produce Constrained Baseline H.264, otherwise the next one is tried, finally openh264.
SW_ENCODER = "openh264enc"
HW_ENCODERS = ["vah264lpenc", "vah264enc", "vaapih264enc", "nvh264enc"]
CBP_CAPS = "video/x-h264,stream-format=byte-stream,alignment=au,profile=constrained-baseline"


def encoder_spec(name, bitrate, fps):
    """(input format, encoder element description) for a supported encoder."""
    kbps = max(1, bitrate // 1024)
    return {
        "openh264enc": ("I420", f"openh264enc name=venc bitrate={bitrate} max-bitrate={bitrate} "
                                f"gop-size={fps} complexity=low rate-control=bitrate usage-type=screen"),
        "vah264lpenc": ("NV12", f"vah264lpenc name=venc bitrate={kbps} key-int-max={fps} b-frames=0 rate-control=cbr"),
        "vah264enc": ("NV12", f"vah264enc name=venc bitrate={kbps} key-int-max={fps} b-frames=0 rate-control=cbr"),
        "vaapih264enc": ("NV12", f"vaapih264enc name=venc bitrate={kbps} keyframe-period={fps} "
                                 "max-bframes=0 rate-control=cbr"),
        "nvh264enc": ("NV12", f"nvh264enc name=venc bitrate={kbps} gop-size={fps} bframes=0 rc-mode=cbr "
                              "preset=low-latency-hq zerolatency=true"),
    }[name]


def probe_encoder(name, w=1280, h=720, fps=30, bitrate=4096 * 1024):
    """Encode a few test frames; OK only if the result negotiates as Constrained Baseline."""
    if not Gst.ElementFactory.find(name):
        return False, "tidak terpasang"
    fmt, enc = encoder_spec(name, bitrate, fps)
    desc = (f"videotestsrc num-buffers=5 ! video/x-raw,format={fmt},width={w},height={h},framerate={fps}/1 ! "
            f"{enc} ! h264parse ! {CBP_CAPS} ! fakesink")
    try:
        pipe = Gst.parse_launch(desc)
    except GLib.Error as e:
        return False, e.message
    pipe.set_state(Gst.State.PLAYING)
    msg = pipe.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipe.set_state(Gst.State.NULL)
    if msg is None:
        return False, "timeout"
    if msg.type == Gst.MessageType.ERROR:
        return False, msg.parse_error()[0].message
    return True, "ok"


def choose_encoder(hw):
    if hw:
        for name in HW_ENCODERS:
            ok, why = probe_encoder(name)
            log(f"Encoder hardware {name}: {'dipakai' if ok else 'tidak bisa (' + why + ')'}")
            if ok:
                return name
        log("Tidak ada encoder hardware yang cocok, kembali ke openh264")
    return SW_ENCODER


class Streamer:
    def __init__(self, node, host, port, w, h, fps, audio, bitrate, encoder=SW_ENCODER):
        self.desc = self._describe(node, host, port, w, h, fps, audio, bitrate, encoder, keepalive=True)
        try:
            self.pipe = Gst.parse_launch(self.desc)
        except GLib.Error:
            self.desc = self._describe(node, host, port, w, h, fps, audio, bitrate, encoder, keepalive=False)
            self.pipe = Gst.parse_launch(self.desc)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)
        self._pcm = b""
        self.sent_bytes = 0
        sink = self.pipe.get_by_name("pcmsink")
        if sink:
            self._lpcmsrc = self.pipe.get_by_name("lpcmsrc")
            sink.connect("new-sample", self._on_pcm)
        tsout = self.pipe.get_by_name("tsout")
        if tsout:
            self._tsin = self.pipe.get_by_name("tsin")
            self._patcher = TsPatcher()
            tsout.connect("new-sample", self._on_ts)

    def _on_ts(self, sink):
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        data = self._patcher.patch(bytes(info.data))
        buf.unmap(info)
        self.sent_bytes += len(data)
        out = Gst.Buffer.new_wrapped(data)
        out.pts, out.dts, out.duration = buf.pts, buf.dts, buf.duration
        self._tsin.emit("push-buffer", out)
        return Gst.FlowReturn.OK

    # WFD LPCM framing (as in Android's wifi-display Converter):
    # 4-byte header [0xA0, 6 AUs, 0x00, (q=0:16bit)<<6 | (fs=2:48kHz)<<3 | (ch=1:stereo)]
    # followed by 6 x 80 stereo S16BE frames = 1920 bytes (10 ms) per PES packet.
    LPCM_HDR = bytes([0xA0, 0x06, 0x00, (0 << 6) | (2 << 3) | 1])
    LPCM_CHUNK = 6 * 80 * 4

    def _on_pcm(self, sink):
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            self._pcm += bytes(info.data)
            buf.unmap(info)
        while len(self._pcm) >= self.LPCM_CHUNK:
            chunk, self._pcm = self._pcm[: self.LPCM_CHUNK], self._pcm[self.LPCM_CHUNK:]
            out = Gst.Buffer.new_wrapped(self.LPCM_HDR + chunk)
            out.duration = 10 * Gst.MSECOND
            self._lpcmsrc.emit("push-buffer", out)
        return Gst.FlowReturn.OK

    @staticmethod
    def _describe(node, host, port, w, h, fps, audio, bitrate, encoder, keepalive):
        ka = " keepalive-time=500" if keepalive else ""
        fmt, enc = encoder_spec(encoder, bitrate, fps)
        video = (
            f"pipewiresrc path={node} do-timestamp=true{ka} ! "
            # videorate first: GNOME delivers variable framerate (0/1), which videoconvert 1.20 rejects
            f"videorate ! video/x-raw,framerate={fps}/1 ! videoconvert ! videoscale ! "
            f"video/x-raw,format={fmt},width={w},height={h},pixel-aspect-ratio=1/1 ! "
            "queue max-size-buffers=3 leaky=downstream ! "
            f"{enc} ! "
            # always force Constrained Baseline: Miracast sinks (Epson) may reject Main/High
            f"h264parse config-interval=-1 ! {CBP_CAPS} ! "
            "queue ! mux. "
        )
        cap = f"pulsesrc device={default_monitor()} do-timestamp=true ! audioconvert ! audioresample ! "
        if audio == "aac":
            aud = (cap + "audio/x-raw,rate=48000,channels=2 ! voaacenc bitrate=128000 ! aacparse ! "
                   "audio/mpeg,mpegversion=4,stream-format=adts ! queue ! mux. ")
        elif audio == "lpcm":
            # WFD LPCM: raw S16BE captured into appsink, re-framed with the WFD LPCM header in Python,
            # pushed back via appsrc as audio/x-lpcm (mpegtsmux: stream_type 0x83, private_stream_1)
            aud = (cap + "audio/x-raw,format=S16BE,rate=48000,channels=2,layout=interleaved ! "
                   "appsink name=pcmsink emit-signals=true sync=false async=false max-buffers=50 drop=true "
                   "appsrc name=lpcmsrc is-live=true do-timestamp=true format=time "
                   "caps=audio/x-lpcm,width=16,rate=48000,channels=2,dynamic_range=0,emphasis=false,mute=false ! "
                   "queue ! mux. ")
        else:
            aud = ""
        # TS always goes through Python (TsPatcher) before RTP: strip HDMV descriptors, fix LPCM signalling.
        tail = ("mpegtsmux name=mux alignment=7 ! appsink name=tsout emit-signals=true sync=false async=false "
                "appsrc name=tsin is-live=true format=time caps=video/mpegts,systemstream=true,packetsize=188 ! "
                f"rtpmp2tpay pt=33 ! udpsink host={host} port={port} sync=false async=false")
        # WFD/Android PIDs: video 0x1011 (4113), audio 0x1100 (4352); mpegtsmux takes the PID from the pad name
        return (video + aud).replace("queue ! mux. ", "queue ! mux.sink_4113 ", 1).replace(
            "queue ! mux. ", "queue ! mux.sink_4352 ", 1) + tail

    def _on_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        log(f"GStreamer ERROR: {err.message} ({dbg})")

    def _on_warning(self, _bus, msg):
        err, _ = msg.parse_warning()
        log(f"GStreamer warning: {err.message}")

    def start(self):
        log("Pipeline:", self.desc)
        self.pipe.set_state(Gst.State.PLAYING)

    def idr(self):
        venc = self.pipe.get_by_name("venc")
        if venc:
            ev = GstVideo.video_event_new_upstream_force_key_unit(Gst.CLOCK_TIME_NONE, True, 0)
            venc.get_static_pad("src").send_event(ev)

    def stop(self):
        self.pipe.set_state(Gst.State.NULL)


# --------------------------------------------------------------------------- RTSP / WFD
class RtspError(Exception):
    pass


METHODS = "OPTIONS|GET_PARAMETER|SET_PARAMETER|SETUP|PLAY|PAUSE|TEARDOWN"
START_RE = re.compile(rf"RTSP/1\.0 \d{{3}}.*$|(?:{METHODS}) \S+ RTSP/1\.0$")


class WfdSession:
    def __init__(self, sock, peer, args, cast):
        self.sock, self.peer, self.args, self.cast = sock, peer, args, cast
        self.buf = b""
        self.cseq = 0
        self.session_id = "53" + str(int(time.time()))[-6:]
        self.client_port = None
        self.streamer = None
        self.mode = None
        self.audio = False
        self.alive = True
        self.local_ip = sock.getsockname()[0]

    # ---- low level
    def _read_msg(self, timeout):
        self.sock.settimeout(timeout)
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RtspError("sink closed the connection")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode(errors="replace").split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RtspError("sink closed the connection")
            self.buf += chunk
        body, self.buf = self.buf[:n].decode(errors="replace"), self.buf[n:]
        start = lines[0]
        # Epson firmware sometimes prefixes a message with binary garbage
        # (e.g. "192.168.157.1J<...>/dev/vpath/wfd0/fb0RTSP/1.0 200 OK"): keep only the real start line.
        m = START_RE.search(start)
        if m:
            if m.start() > 0 and self.args.verbose:
                log(f"(buang {m.start()} byte sampah di depan pesan)")
            start = m.group(0)
        else:
            start = "JUNK " + start.encode(errors="replace")[:40].hex()
        msg = {"start": start, "headers": headers, "body": body}
        if self.args.verbose:
            log("<<", start, "|", body.strip().replace("\r\n", " | "))
        return msg

    def _send(self, start, headers, body=""):
        hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        if body:
            hdr += f"Content-Type: text/parameters\r\nContent-Length: {len(body)}\r\n"
        data = f"{start}\r\n{hdr}\r\n{body}"
        if self.args.verbose:
            log(">>", start, "|", body.strip().replace("\r\n", " | "))
        self.sock.sendall(data.encode())

    def request(self, method, url, body="", extra=None, timeout=10):
        self.cseq += 1
        cseq = self.cseq
        headers = {"CSeq": cseq}
        if extra:
            headers.update(extra)
        self._send(f"{method} {url} RTSP/1.0", headers, body)
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise socket.timeout(f"no answer to {method}")
            msg = self._read_msg(left)
            if msg["start"].startswith("RTSP/1.0"):
                if msg["headers"].get("cseq") == str(cseq):
                    code = int(msg["start"].split()[1])
                    if code != 200:
                        raise RtspError(f"{method} -> {msg['start']}")
                    return msg
            else:
                self.handle_request(msg)

    def respond(self, req, code=200, extra=None, body=""):
        headers = {"CSeq": req["headers"].get("cseq", "0")}
        if extra:
            headers.update(extra)
        reason = {200: "OK", 400: "Bad Request", 551: "Option not supported"}.get(code, "OK")
        self._send(f"RTSP/1.0 {code} {reason}", headers, body)

    # ---- sink -> source requests
    def handle_request(self, req):
        method = req["start"].split()[0]
        if method == "JUNK":  # unparseable: ignore, never answer garbage
            return
        if method == "OPTIONS":  # M2
            self.respond(req, extra={"Public": "org.wfa.wfd1.0, SETUP, TEARDOWN, PLAY, PAUSE, GET_PARAMETER, SET_PARAMETER"})
        elif method == "SETUP":  # M6
            m = re.search(r"client_port=(\d+)", req["headers"].get("transport", ""))
            if m:
                self.client_port = int(m.group(1))
            self.respond(req, extra={
                "Session": f"{self.session_id};timeout=30",
                "Transport": f"RTP/AVP/UDP;unicast;client_port={self.client_port};server_port=5000-5001",
            })
        elif method == "PLAY":  # M7
            self.respond(req, extra={"Session": self.session_id, "Range": "npt=now-"})
            self.start_stream()
        elif method == "PAUSE":
            self.respond(req, extra={"Session": self.session_id})
        elif method == "TEARDOWN":
            self.respond(req, extra={"Session": self.session_id})
            log("Sink sent TEARDOWN")
            self.alive = False
        elif method == "SET_PARAMETER":
            if "wfd_idr_request" in req["body"] and self.streamer:
                self.streamer.idr()
            self.respond(req)
        elif method == "GET_PARAMETER":
            self.respond(req)
        else:
            self.respond(req, 551)

    # ---- negotiation helpers
    @staticmethod
    def _param(body, name):
        m = re.search(rf"^{name}:\s*(.*)$", body, re.M)
        return m.group(1).strip() if m else ""

    def choose_mode(self, vf):
        cea_mask = 0x1  # 640x480p60 is mandatory
        fields = vf.split()
        # wfd_video_formats: native pref [profile level cea vesa hh latency minslice sliceenc frc maxh maxv], ...
        if len(fields) >= 5:
            try:
                cea_mask = int(fields[4].rstrip(","), 16) or 0x1
            except ValueError:
                pass
        for bit in PREFERENCE[self.args.mode]:
            if cea_mask & (1 << bit):
                return bit
        return 0

    # ---- main flow
    def run(self):
        log(f"Sink connected from {self.peer[0]}:{self.peer[1]} (local {self.local_ip})")
        # M1
        self.request("OPTIONS", "*", extra={"Require": "org.wfa.wfd1.0"})
        # M2 comes from the sink; handled inside the next request's read loop.
        # M3
        r = self.request("GET_PARAMETER", "rtsp://localhost/wfd1.0",
                         body="wfd_content_protection\r\nwfd_video_formats\r\nwfd_audio_codecs\r\nwfd_client_rtp_ports\r\n")  # same set as Android
        vf = self._param(r["body"], "wfd_video_formats")
        ac = self._param(r["body"], "wfd_audio_codecs")
        rp = self._param(r["body"], "wfd_client_rtp_ports")
        log(f"Sink video: {vf}")
        log(f"Sink audio: {ac}")
        log(f"Sink rtp:   {rp}")
        cp = self._param(r["body"], "wfd_content_protection") or "(tidak dijawab)"
        log(f"Sink HDCP:  {cp}")
        m = re.search(r"unicast\s+(\d+)", rp)
        self.client_port = int(m.group(1)) if m else 1028
        bit = self.choose_mode(vf)
        w, h, fps, level = CEA[bit]
        self.mode = (w, h, fps)
        acu = ac.upper()
        if self.args.no_audio:
            self.audio = None
        elif self.args.audio in ("auto", "lpcm") and "LPCM" in acu and (self.args.audio == "lpcm" or "AAC" not in acu):
            self.audio = "lpcm"
        elif "AAC" in acu:
            self.audio = "aac"
        elif "LPCM" in acu:
            self.audio = "lpcm"
        else:
            self.audio = None
        audio_param = {"aac": "wfd_audio_codecs: AAC 00000001 00\r\n",
                       "lpcm": "wfd_audio_codecs: LPCM 00000002 00\r\n"}.get(self.audio, "")
        log(f"Chosen: {w}x{h}@{fps}, audio={self.audio or 'off'}, rtp port {self.client_port}")
        params = (
            f"wfd_video_formats: 00 00 01 {level:02x} {1 << bit:08x} 00000000 00000000 00 0000 0000 00 none none\r\n"
            + audio_param
            + f"wfd_presentation_URL: rtsp://{self.local_ip}/wfd1.0/streamid=0 none\r\n"
            + f"wfd_client_rtp_ports: RTP/AVP/UDP;unicast {self.client_port} 0 mode=play\r\n"
        )
        # M4
        self.request("SET_PARAMETER", "rtsp://localhost/wfd1.0", body=params)
        # M5 -> sink answers, then sends SETUP (M6) and PLAY (M7)
        self.request("SET_PARAMETER", "rtsp://localhost/wfd1.0", body="wfd_trigger_method: SETUP\r\n")
        last_keepalive = time.time()
        while self.alive:
            try:
                msg = self._read_msg(1.0)
                if not msg["start"].startswith("RTSP/1.0"):
                    self.handle_request(msg)
            except socket.timeout:
                pass
            if self.streamer and time.time() - last_keepalive > 20:  # M16 keepalive
                last_keepalive = time.time()
                log(f"stream: {self.streamer.sent_bytes / 1e6:.1f} MB terkirim ke {self.peer[0]}:{self.client_port}")
                try:
                    self.request("GET_PARAMETER", "rtsp://localhost/wfd1.0",
                                 extra={"Session": self.session_id}, timeout=5)
                except (socket.timeout, RtspError) as e:
                    # a lost/garbled keepalive answer must not kill a working stream
                    log(f"keepalive tanpa jawaban yang valid ({e}), lanjut")

    def start_stream(self):
        if self.streamer:
            return
        for _ in range(50):
            if self.cast.node is not None:
                break
            time.sleep(0.1)
        if self.cast.node is None:
            raise RtspError("no PipeWire node from ScreenCast")
        w, h, fps = self.mode
        self.streamer = Streamer(self.cast.node, self.peer[0], self.client_port, w, h, fps,
                                 self.audio, self.args.bitrate, self.args.encoder)
        self.streamer.start()
        log(f"Streaming to {self.peer[0]}:{self.client_port}")

    def close(self):
        if self.streamer:
            self.streamer.stop()
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- requirement check
# element -> Ubuntu package that provides it
REQUIRED_ELEMENTS = {
    "pipewiresrc": "gstreamer1.0-pipewire",
    "videorate": "gstreamer1.0-plugins-base", "videoconvert": "gstreamer1.0-plugins-base",
    "videoscale": "gstreamer1.0-plugins-base", "audioconvert": "gstreamer1.0-plugins-base",
    "audioresample": "gstreamer1.0-plugins-base", "appsrc": "gstreamer1.0-plugins-base",
    "appsink": "gstreamer1.0-plugins-base", "videotestsrc": "gstreamer1.0-plugins-base",
    "openh264enc": "gstreamer1.0-plugins-bad", "h264parse": "gstreamer1.0-plugins-bad",
    "mpegtsmux": "gstreamer1.0-plugins-bad",
    "rtpmp2tpay": "gstreamer1.0-plugins-good", "udpsink": "gstreamer1.0-plugins-good",
    "pulsesrc": "gstreamer1.0-pulseaudio",
}
OPTIONAL_ELEMENTS = {  # only needed for AAC audio (LPCM works without)
    "voaacenc": "gstreamer1.0-plugins-bad", "aacparse": "gstreamer1.0-plugins-good",
}


def run_checks(args):
    """Print a requirement report; exit code 1 if something mandatory is missing."""
    fatal = False

    def line(ok, text, hint=""):
        print(f"  {'OK ' if ok else 'XX '} {text}" + (f"\n        -> {hint}" if hint else ""), flush=True)

    print(f"  OK  {Gst.version_string()} + PyGObject")
    try:  # GNOME screen capture API
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync("org.gnome.Mutter.ScreenCast", "/org/gnome/Mutter/ScreenCast",
                            "org.freedesktop.DBus.Properties", "Get",
                            GLib.Variant("(ss)", ("org.gnome.Mutter.ScreenCast", "Version")),
                            None, 0, 3000, None)
        ver = res.unpack()[0]
        line(True, f"GNOME Mutter ScreenCast API versi {ver}")
        if args.virtual and ver < 4:
            line(False, "Extend (monitor virtual) butuh ScreenCast API versi 4+ (GNOME 42+)")
            fatal = True
    except GLib.Error as e:
        line(False, "GNOME Mutter ScreenCast API tidak tersedia",
             "butuh desktop GNOME (KDE/XFCE/dll. tidak didukung) - " + e.message)
        fatal = True
    missing = sorted({pkg for el, pkg in REQUIRED_ELEMENTS.items() if not Gst.ElementFactory.find(el)})
    gone = [el for el in REQUIRED_ELEMENTS if not Gst.ElementFactory.find(el)]
    if gone:
        line(False, "elemen GStreamer hilang: " + ", ".join(gone), "sudo apt install " + " ".join(missing))
        fatal = True
    else:
        line(True, f"elemen GStreamer wajib lengkap ({len(REQUIRED_ELEMENTS)})")
    gone_opt = [el for el in OPTIONAL_ELEMENTS if not Gst.ElementFactory.find(el)]
    if gone_opt:
        line(False, "audio AAC tidak tersedia (" + ", ".join(gone_opt) + "); LPCM tetap bisa",
             "opsional: sudo apt install " + " ".join(sorted({OPTIONAL_ELEMENTS[e] for e in gone_opt})))
    else:
        line(True, "audio AAC tersedia")
    if not gone:
        ok, why = probe_encoder(SW_ENCODER)
        line(ok, f"encoder openh264: {why}")
        fatal |= not ok
    mon = default_monitor()
    line(mon != "@DEFAULT_MONITOR@", f"sumber audio: {mon}",
         "" if mon != "@DEFAULT_MONITOR@" else "pactl tidak ditemukan / tidak ada output audio aktif")
    hw = []
    for name in HW_ENCODERS:
        ok, why = probe_encoder(name)
        hw.append(f"{name}={'ok' if ok else why}")
    print("  --  encoder hardware (untuk --hw): " + "; ".join(hw), flush=True)
    return 1 if fatal else 0


ACTIVE = []  # sessions to stop on exit


def serve(args, cast, loop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(1)
    log(f"Waiting for the sink on TCP :{args.port} ...")
    while True:
        conn, peer = srv.accept()
        sess = WfdSession(conn, peer, args, cast)
        ACTIVE.append(sess)
        try:
            sess.run()
        except (RtspError, OSError) as e:
            log(f"Session ended: {e}")
        finally:
            sess.close()
            ACTIVE.remove(sess)
        if args.once:
            loop.quit()
            return
        log("Waiting for the sink to reconnect ...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--virtual", action="store_true", help="extend (virtual monitor) instead of mirror")
    ap.add_argument("--mode", choices=sorted(PREFERENCE), default="720p30")
    # same as GNOME Network Displays: initial video bitrate capped at 512*8 kbit/s (wfd-media-factory.c)
    ap.add_argument("--bitrate", type=int, default=4096 * 1024, help="video bitrate in bit/s")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--audio", choices=["auto", "aac", "lpcm"], default="auto",
                    help="auto: AAC if the sink offers it, else LPCM")
    ap.add_argument("--port", type=int, default=7236)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--hw", action="store_true",
                    help="try a hardware H.264 encoder (VA-API/NVENC), fall back to openh264")
    ap.add_argument("--check", action="store_true", help="only check requirements, then exit")
    args = ap.parse_args()

    if args.check:
        return run_checks(args)
    args.encoder = choose_encoder(args.hw)
    log(f"Encoder video: {args.encoder}")
    cast = ScreenCast(args.virtual)
    loop = GLib.MainLoop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, loop.quit)
    threading.Thread(target=serve, args=(args, cast, loop), daemon=True).start()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    # Shutdown: never hang. GStreamer/PipeWire threads can block interpreter exit,
    # so stop what we can within 3 s and then hard-exit.
    threading.Timer(3.0, lambda: os._exit(1)).start()
    log("Stopping ...")
    for sess in list(ACTIVE):
        sess.close()
    cast.stop()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
