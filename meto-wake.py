#!/opt/bin/python3
"""
meto-wake — custom wake word "мето" for Yandex Station.
Listens on mic, detects "мето", triggers voice assistant via KEY_VOICECOMMAND.
Uses template matching with a recorded reference sample.
"""
import os, sys, struct, time, wave, array, math, subprocess, signal

RATE = 16000
CHANNELS = 1
CHUNK_MS = 100
CHUNK = RATE * CHUNK_MS // 1000
INPUT_DEV = "/dev/input/event1"
KEY_VOICECOMMAND = 246
EV_KEY = 1
EV_SYN = 0
SYN_REPORT = 0

REF_DIR = "/data/opt/meto-ref"
REF_FILE = os.path.join(REF_DIR, "meto.raw")
ENERGY_THRESHOLD = 500
COOLDOWN = 3.0

# ── input event injection ──

def inject_key(dev, code):
    ts = time.time()
    sec = int(ts)
    usec = int((ts - sec) * 1e6)
    ev_press = struct.pack("llHHi", sec, usec, EV_KEY, code, 1)
    ev_syn = struct.pack("llHHi", sec, usec, EV_SYN, SYN_REPORT, 0)
    ev_release = struct.pack("llHHi", sec, usec, EV_KEY, code, 0)
    ev_syn2 = struct.pack("llHHi", sec, usec, EV_SYN, SYN_REPORT, 0)
    with open(dev, "wb") as f:
        f.write(ev_press)
        f.write(ev_syn)
        time.sleep(0.05)
        f.write(ev_release)
        f.write(ev_syn2)

def activate():
    print("[!] МЕТО detected -> activating voice assistant")
    try:
        inject_key(INPUT_DEV, KEY_VOICECOMMAND)
    except Exception as e:
        print(f"[!] inject failed: {e}, trying evdev fallback")
        subprocess.run(["input", "keyevent", str(KEY_VOICECOMMAND)], capture_output=True)

# ── audio helpers ──

def rms(samples):
    if not samples:
        return 0
    s = sum(x * x for x in samples)
    return math.sqrt(s / len(samples))

def read_raw(path):
    with open(path, "rb") as f:
        data = f.read()
    return array.array("h", data)

def correlate(signal, template):
    """Normalized cross-correlation, returns max correlation coefficient."""
    n = len(template)
    if len(signal) < n:
        return 0.0
    t_mean = sum(template) / n
    t_std = math.sqrt(sum((x - t_mean) ** 2 for x in template) / n)
    if t_std < 1:
        return 0.0
    best = 0.0
    step = n // 4
    for offset in range(0, len(signal) - n, step):
        seg = signal[offset:offset + n]
        s_mean = sum(seg) / n
        s_std = math.sqrt(sum((x - s_mean) ** 2 for x in seg) / n)
        if s_std < 1:
            continue
        cov = sum((seg[i] - s_mean) * (template[i] - t_mean) for i in range(n)) / n
        corr = cov / (s_std * t_std)
        if corr > best:
            best = corr
    return best

# ── recording reference sample ──

def record_reference():
    os.makedirs(REF_DIR, exist_ok=True)
    print("[*] Recording reference for 'мето'")
    print("[*] Say 'мето' clearly after the beep...")
    time.sleep(1)
    print("[*] Recording 2 seconds...")
    tmp = "/tmp/meto_ref.wav"
    subprocess.run([
        "arecord", "-D", "hw:0,1", "-f", "S16_LE",
        "-r", str(RATE), "-c", str(CHANNELS),
        "-d", "2", tmp
    ], capture_output=True)
    with wave.open(tmp, "r") as w:
        frames = w.readframes(w.getnframes())
    samples = array.array("h", frames)
    # trim silence: find the loudest 0.6s window
    win = RATE * 6 // 10
    best_e = 0
    best_i = 0
    for i in range(0, len(samples) - win, RATE // 20):
        e = rms(samples[i:i + win])
        if e > best_e:
            best_e = e
            best_i = i
    trimmed = samples[best_i:best_i + win]
    with open(REF_FILE, "wb") as f:
        f.write(trimmed.tobytes())
    print(f"[+] Reference saved ({len(trimmed)} samples, RMS={best_e:.0f})")
    os.remove(tmp)
    return trimmed

# ── main loop ──

def main():
    print("=" * 40)
    print("  meto-wake · custom wake word daemon")
    print("  metoinc develop × hackisa")
    print("=" * 40)

    if "--record" in sys.argv:
        record_reference()
        print("[+] Done. Run without --record to start detection.")
        return

    if not os.path.exists(REF_FILE):
        print("[!] No reference recording found.")
        print("[!] Run with --record first to record 'мето'")
        record_reference()

    template = read_raw(REF_FILE)
    print(f"[*] Template loaded: {len(template)} samples")
    print(f"[*] Listening for 'мето'...")
    print(f"[*] Cooldown: {COOLDOWN}s between activations")

    buf = array.array("h")
    window = RATE * 2  # 2 second sliding window
    last_trigger = 0
    threshold = 0.45

    proc = subprocess.Popen(
        ["arecord", "-D", "hw:0,1", "-f", "S16_LE",
         "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )

    def cleanup(sig=None, frame=None):
        proc.terminate()
        print("\n[*] Stopped.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    chunk_bytes = CHUNK * 2  # 16-bit = 2 bytes per sample
    try:
        while True:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            samples = array.array("h", data)
            buf.extend(samples)
            # keep window size
            if len(buf) > window:
                buf = buf[-window:]

            energy = rms(samples)
            if energy < ENERGY_THRESHOLD:
                continue

            now = time.time()
            if now - last_trigger < COOLDOWN:
                continue

            # voice activity detected — check correlation
            corr = correlate(buf, template)
            if corr > threshold:
                print(f"[!] Match: corr={corr:.3f} energy={energy:.0f}")
                activate()
                last_trigger = now
                buf = array.array("h")
    except Exception as e:
        print(f"[!] Error: {e}")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
