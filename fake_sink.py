#!/usr/bin/env python3
"""Fake WFD sink for local testing of wfd_source.py (acts like the projector side)."""
import socket, sys, time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 17236
RTP = 15560
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); udp.bind(("127.0.0.1", RTP)); udp.settimeout(0.2)
s = socket.create_connection(("127.0.0.1", PORT))
buf = b""; cseq = 100


def read():
    global buf
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    head, buf = buf.split(b"\r\n\r\n", 1)
    lines = head.decode().split("\r\n")
    h = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
    n = int(h.get("content-length", 0))
    while len(buf) < n:
        buf += s.recv(4096)
    body, buf = buf[:n].decode(), buf[n:]
    print("SINK <<", lines[0], "|", body.strip().replace("\r\n", " | "))
    return lines[0], h, body


def send(txt):
    s.sendall(txt.encode())


def reply(h, extra="", body=""):
    cl = f"Content-Type: text/parameters\r\nContent-Length: {len(body)}\r\n" if body else ""
    send(f"RTSP/1.0 200 OK\r\nCSeq: {h['cseq']}\r\n{extra}{cl}\r\n{body}")


def req(line, extra=""):
    global cseq
    cseq += 1
    send(f"{line} RTSP/1.0\r\nCSeq: {cseq}\r\n{extra}\r\n")
    while True:
        st, h, b = read()
        if st.startswith("RTSP/1.0") and h.get("cseq") == str(cseq):
            return h
        handle(st, h, b)


def handle(st, h, b):
    m = st.split()[0]
    if m == "OPTIONS":
        reply(h, "Public: org.wfa.wfd1.0, GET_PARAMETER, SET_PARAMETER\r\n")
        req("OPTIONS *", "Require: org.wfa.wfd1.0\r\n")  # M2
    elif m == "GET_PARAMETER" and b:
        reply(h, body=("wfd_video_formats: 00 00 01 10 0001ffff 1fffffff 00001fff 00 0000 0000 10 none none\r\n"
                       "wfd_audio_codecs: LPCM 00000003 00, AAC 0000000f 00, AC3 00000007 00\r\n"
                       f"wfd_client_rtp_ports: RTP/AVP/UDP;unicast {RTP} 0 mode=play\r\n"))
    elif m == "SET_PARAMETER" and "wfd_trigger_method" in b:
        reply(h)
        sh = req("SETUP rtsp://127.0.0.1/wfd1.0/streamid=0", f"Transport: RTP/AVP/UDP;unicast;client_port={RTP}\r\n")
        req("PLAY rtsp://127.0.0.1/wfd1.0/streamid=0", f"Session: {sh['session'].split(';')[0]}\r\n")
        n = 0; t = time.time()
        while time.time() - t < 4:
            try:
                n += len(udp.recv(65536))
            except socket.timeout:
                pass
        print(f"SINK: received {n} RTP bytes in 4 s -> {'OK' if n > 0 else 'NO DATA'}")
        sys.exit(0)
    else:
        reply(h)


while True:
    handle(*read())
