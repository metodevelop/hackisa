#!/usr/bin/env python3
"""
MITM WebSocket proxy for Yandex Station TTS voice replacement.
Uses websockets library for correct permessage-deflate handling.
"""
import asyncio
import ssl
import os
import time
import json

import websockets
from websockets.extensions.permessage_deflate import ClientPerMessageDeflateFactory, ServerPerMessageDeflateFactory

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 9443
REAL_HOST = "uniproxy.alice.yandex.net"
REAL_PORT = 443
REAL_URI = f"wss://{REAL_HOST}/uni.ws"

CERT_DIR = os.path.join(os.path.dirname(__file__), "mitm-certs")
DUMP_DIR = os.path.join(os.path.dirname(__file__), "mitm-dumps")
os.makedirs(DUMP_DIR, exist_ok=True)

session_counter = 0


def make_server_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(
        os.path.join(CERT_DIR, "server.crt"),
        os.path.join(CERT_DIR, "server.key"),
    )
    return ctx


def make_client_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_default_certs()
    return ctx


def describe(data):
    if not data or len(data) == 0:
        return "empty"
    if isinstance(data, str):
        return f"text({len(data)}): {data[:200]}"
    if data[:4] == b'AAPI':
        inner = data[4:]
        parts = []
        i = 0
        while i < len(inner):
            if inner[i] == 0x0a and i + 1 < len(inner):
                slen = inner[i + 1]
                if i + 2 + slen <= len(inner):
                    try:
                        t = inner[i + 2:i + 2 + slen].decode('utf-8', errors='strict')
                        if t.isprintable() and len(t) > 2:
                            parts.append(t)
                    except:
                        pass
            i += 1
        if parts:
            return f"AAPI [{'; '.join(parts[:4])}] ({len(data)}B)"
        return f"AAPI ({len(data)}B)"
    if data[:4] == b'OggS':
        return f"OGG ({len(data)}B)"
    if len(data) > 4:
        oggs_pos = data.find(b'OggS')
        if oggs_pos >= 0:
            return f"PROTO+OGG @{oggs_pos} ({len(data)}B)"
    if len(data) > 500:
        return f"AUDIO? ({len(data)}B) {data[:8].hex()}"
    try:
        j = json.loads(data)
        return f"JSON: {json.dumps(j, ensure_ascii=False)[:150]}"
    except:
        pass
    return f"bin({len(data)}B) {data[:16].hex()}"


async def handle_connection(ws_client):
    global session_counter
    session_counter += 1
    sid = session_counter
    ts = time.strftime("%H%M%S")

    print(f"[{sid}] Station connected", flush=True)

    # Grab incoming request headers to forward
    request = ws_client.request
    extra_headers = {}
    if request and request.headers:
        for key in request.headers:
            k = key.lower()
            if k not in ('host', 'upgrade', 'connection', 'sec-websocket-key',
                         'sec-websocket-version', 'sec-websocket-extensions',
                         'sec-websocket-protocol'):
                extra_headers[key] = request.headers[key]

    subprotocols = []
    if request and request.headers.get('Sec-WebSocket-Protocol'):
        subprotocols = [p.strip() for p in request.headers['Sec-WebSocket-Protocol'].split(',')]

    client_ssl = make_client_ssl()
    try:
        ws_remote = await websockets.connect(
            REAL_URI,
            ssl=client_ssl,
            additional_headers=extra_headers,
            subprotocols=subprotocols if subprotocols else None,
            extensions=[ClientPerMessageDeflateFactory(
                server_max_window_bits=15,
                client_max_window_bits=15,
                compress_settings={"memLevel": 8},
            )],
            max_size=2**22,
            ping_interval=None,
            ping_timeout=None,
            open_timeout=10,
        )
    except Exception as e:
        print(f"[{sid}] Failed to connect upstream: {e}", flush=True)
        return

    print(f"[{sid}] Connected to {REAL_HOST}", flush=True)

    logf = open(os.path.join(DUMP_DIR, f"session_{ts}_{sid}.log"), "w")
    tts_audio_chunks = []
    tts_active = [False]
    chunk_counter = [0]

    async def station_to_yandex():
        try:
            async for msg in ws_client:
                is_bin = isinstance(msg, bytes)
                desc = describe(msg)
                line = f"[C->S] {'bin' if is_bin else 'txt'} {desc}"
                print(f"[{sid}] {line}", flush=True)
                logf.write(line + "\n")
                logf.flush()

                if is_bin:
                    await ws_remote.send(msg)
                else:
                    await ws_remote.send(msg)
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            print(f"[{sid}] C->S error: {e}", flush=True)

    async def yandex_to_station():
        try:
            async for msg in ws_remote:
                is_bin = isinstance(msg, bytes)
                desc = describe(msg)

                is_tts = False
                if is_bin:
                    if b'audio/opus' in msg:
                        tts_active[0] = True
                        tts_audio_chunks.clear()
                        chunk_counter[0] = 0
                        desc = f"*** TTS START *** {desc}"

                    if tts_active[0] and len(msg) > 500:
                        is_tts = True
                        chunk_counter[0] += 1
                        fn = os.path.join(DUMP_DIR, f"tts_{sid}_{chunk_counter[0]:04d}.bin")
                        with open(fn, "wb") as bf:
                            bf.write(msg)
                        tts_audio_chunks.append(msg)
                        desc = f"*** TTS #{chunk_counter[0]} *** {desc}"

                    # Detect TTS end (small AAPI after audio)
                    if tts_active[0] and msg[:4] == b'AAPI' and len(msg) < 200 and tts_audio_chunks:
                        total = sum(len(c) for c in tts_audio_chunks)
                        desc = f"*** TTS END ({len(tts_audio_chunks)} chunks, {total}B) *** {desc}"
                        # Save combined TTS
                        combined = os.path.join(DUMP_DIR, f"tts_combined_{sid}_{ts}.bin")
                        with open(combined, "wb") as cf:
                            for chunk in tts_audio_chunks:
                                cf.write(chunk)
                        print(f"[{sid}] TTS saved: {combined} ({total}B)", flush=True)
                        tts_active[0] = False

                line = f"[S->C] {'bin' if is_bin else 'txt'} {desc}"
                print(f"[{sid}] {line}", flush=True)
                logf.write(line + "\n")
                logf.flush()

                if is_bin:
                    await ws_client.send(msg)
                else:
                    await ws_client.send(msg)
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            print(f"[{sid}] S->C error: {e}", flush=True)

    try:
        await asyncio.gather(station_to_yandex(), yandex_to_station())
    finally:
        logf.close()
        try:
            await ws_remote.close()
        except:
            pass
        print(f"[{sid}] Session closed", flush=True)


async def main():
    server_ssl = make_server_ssl()
    async with websockets.serve(
        handle_connection,
        LISTEN_HOST,
        LISTEN_PORT,
        ssl=server_ssl,
        extensions=[ServerPerMessageDeflateFactory(
            server_max_window_bits=15,
            client_max_window_bits=15,
        )],
        max_size=2**22,
        ping_interval=None,
        ping_timeout=None,
        process_request=None,
    ):
        print(f"MITM proxy on :{LISTEN_PORT}", flush=True)
        print(f"-> {REAL_HOST}:{REAL_PORT}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
