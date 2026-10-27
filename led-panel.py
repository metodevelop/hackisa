#!/opt/bin/python3
"""LED control web panel for Yandex Station Lite 1."""
import http.server
import json
import os
import threading
import time
import signal
import sys

LED = "/sys/class/leds/micro"
PORT = 8080

# --- LED hardware ---

def set_led(zone, r, g, b):
    for color, val in [("red", r), ("green", g), ("blue", b)]:
        try:
            with open(f"{LED}:{color}:ring{zone}/brightness", "w") as f:
                f.write(str(max(0, min(255, int(val)))))
        except OSError:
            pass

def set_all(r, g, b):
    for z in (1, 2, 3):
        set_led(z, r, g, b)

def set_zones(zones):
    for i, (r, g, b) in enumerate(zones, 1):
        set_led(i, r, g, b)

def off():
    set_all(0, 0, 0)

def grab_leds():
    for c in ("red", "green", "blue"):
        for z in (1, 2, 3):
            try:
                with open(f"{LED}:{c}:ring{z}/trigger", "w") as f:
                    f.write("none")
            except OSError:
                pass

def release_leds():
    off()
    for c in ("red", "green", "blue"):
        for z in (1, 2, 3):
            try:
                with open(f"{LED}:{c}:ring{z}/trigger", "w") as f:
                    f.write("led_ring_fb")
            except OSError:
                pass

# --- Effects engine ---

class Effects:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self.current = "off"

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self, fn, name):
        self.stop()
        self.current = name
        self._stop.clear()
        self._thread = threading.Thread(target=fn, daemon=True)
        self._thread.start()

    def solid(self, r, g, b):
        self.stop()
        self.current = "solid"
        grab_leds()
        set_all(r, g, b)

    def off(self):
        self.stop()
        self.current = "off"
        off()

    def rainbow(self, speed=50):
        def run():
            grab_leds()
            hue = 0
            while not self._stop.is_set():
                for zi, offset in enumerate([0, 120, 240], 1):
                    h = (hue + offset) % 360
                    r, g, b = hsv_to_rgb(h, 1.0, 0.78)
                    set_led(zi, r, g, b)
                hue = (hue + 2) % 360
                self._stop.wait(speed / 1000)
            off()
        self._run(run, "rainbow")

    def breathe(self, r, g, b, speed=30):
        def run():
            grab_leds()
            t = 0
            import math
            while not self._stop.is_set():
                bright = (math.sin(t) + 1) / 2
                set_all(int(r * bright), int(g * bright), int(b * bright))
                t += 0.05
                self._stop.wait(speed / 1000)
            off()
        self._run(run, "breathe")

    def police(self, speed=150):
        def run():
            grab_leds()
            while not self._stop.is_set():
                set_zones([(255, 0, 0), (0, 0, 255), (255, 0, 0)])
                self._stop.wait(speed / 1000)
                if self._stop.is_set(): break
                set_zones([(0, 0, 255), (255, 0, 0), (0, 0, 255)])
                self._stop.wait(speed / 1000)
            off()
        self._run(run, "police")

    def fire(self, speed=60):
        import random
        def run():
            grab_leds()
            while not self._stop.is_set():
                for z in (1, 2, 3):
                    r = random.randint(180, 255)
                    g = random.randint(20, 80)
                    set_led(z, r, g, 0)
                self._stop.wait(speed / 1000)
            off()
        self._run(run, "fire")

    def pulse(self, speed=80):
        def run():
            grab_leds()
            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                      (255, 255, 0), (0, 255, 255), (255, 0, 255)]
            idx = 0
            import math
            while not self._stop.is_set():
                r, g, b = colors[idx % len(colors)]
                for step in range(20):
                    if self._stop.is_set(): break
                    bright = math.sin(step * math.pi / 20)
                    set_all(int(r * bright), int(g * bright), int(b * bright))
                    self._stop.wait(speed / 1000)
                idx += 1
            off()
        self._run(run, "pulse")


def hsv_to_rgb(h, s, v):
    h = h % 360
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:    r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


fx = Effects()

# --- Web UI ---

HTML = r'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Station LED</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;
  min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px}
h1{font-size:1.4em;margin-bottom:16px;opacity:.8}
.ring{width:160px;height:160px;border-radius:50%;margin:12px auto;
  border:6px solid #333;display:flex;align-items:center;justify-content:center;
  transition:box-shadow .3s}
.ring-inner{font-size:.7em;opacity:.5;text-align:center}
.picker-wrap{margin:16px 0;text-align:center}
input[type=color]{width:80px;height:80px;border:none;border-radius:50%;
  cursor:pointer;background:none;padding:0}
input[type=color]::-webkit-color-swatch-wrapper{padding:0}
input[type=color]::-webkit-color-swatch{border:3px solid #333;border-radius:50%}
.effects{display:grid;grid-template-columns:1fr 1fr;gap:10px;width:100%;max-width:340px;margin:16px 0}
.btn{padding:14px 8px;border:2px solid #333;border-radius:12px;background:#1a1a1a;
  color:#eee;font-size:1em;cursor:pointer;transition:all .2s;text-align:center}
.btn:active{transform:scale(.95)}
.btn.active{border-color:#4af;color:#4af}
.btn-off{grid-column:1/-1;background:#1a0000;border-color:#500}
.bright{width:100%;max-width:340px;margin:12px 0}
.bright input{width:100%;accent-color:#4af}
.status{font-size:.8em;opacity:.5;margin-top:12px}
</style>
</head>
<body>
<h1>&#x1f4a1; Station LED</h1>
<div class="ring" id="ring">
  <div class="ring-inner">Yandex<br>Lite</div>
</div>
<div class="picker-wrap">
  <input type="color" id="color" value="#ff4400">
</div>
<div class="bright">
  <input type="range" id="bright" min="0" max="100" value="78">
</div>
<div class="effects">
  <div class="btn" onclick="fx('rainbow')">&#x1f308; Rainbow</div>
  <div class="btn" onclick="fx('breathe')">&#x1f4a8; Breathe</div>
  <div class="btn" onclick="fx('police')">&#x1f6a8; Police</div>
  <div class="btn" onclick="fx('fire')">&#x1f525; Fire</div>
  <div class="btn" onclick="fx('pulse')">&#x1f49c; Pulse</div>
  <div class="btn btn-off" onclick="fx('off')">OFF</div>
</div>
<div class="status" id="st">ready</div>
<script>
const $ = s => document.querySelector(s);
const ring = $('#ring');
const colorEl = $('#color');
const brightEl = $('#bright');
let cur = '';

function api(path) {
  return fetch('/api/' + path, {method:'POST'})
    .then(r => r.json())
    .then(d => { $('#st').textContent = d.effect || 'ok'; updateBtns(d.effect); })
    .catch(() => { $('#st').textContent = 'error'; });
}

function updateBtns(name) {
  cur = name;
  document.querySelectorAll('.btn').forEach(b => {
    b.classList.toggle('active', b.textContent.toLowerCase().includes(name));
  });
}

function setColor() {
  const hex = colorEl.value;
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  const br = brightEl.value / 100;
  const R = Math.round(r*br), G = Math.round(g*br), B = Math.round(b*br);
  ring.style.boxShadow = `0 0 40px 10px ${hex}88, inset 0 0 30px ${hex}44`;
  api(`solid?r=${R}&g=${G}&b=${B}`);
}

function fx(name) {
  if (name === 'off') {
    ring.style.boxShadow = 'none';
  } else if (name === 'breathe') {
    const hex = colorEl.value;
    const r = parseInt(hex.slice(1,3),16);
    const g = parseInt(hex.slice(3,5),16);
    const b = parseInt(hex.slice(5,7),16);
    api(`breathe?r=${r}&g=${g}&b=${b}`);
    return;
  }
  api(name);
}

colorEl.addEventListener('input', setColor);
brightEl.addEventListener('input', setColor);
</script>
</body>
</html>'''

# --- HTTP server ---

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        path = self.path
        resp = {"ok": True}

        if path.startswith("/api/solid"):
            params = parse_qs(path)
            r, g, b = int(params.get("r", 0)), int(params.get("g", 0)), int(params.get("b", 0))
            fx.solid(r, g, b)
            resp["effect"] = "solid"
        elif path.startswith("/api/rainbow"):
            fx.rainbow()
            resp["effect"] = "rainbow"
        elif path.startswith("/api/breathe"):
            params = parse_qs(path)
            r, g, b = int(params.get("r", 255)), int(params.get("g", 70)), int(params.get("b", 0))
            fx.breathe(r, g, b)
            resp["effect"] = "breathe"
        elif path.startswith("/api/police"):
            fx.police()
            resp["effect"] = "police"
        elif path.startswith("/api/fire"):
            fx.fire()
            resp["effect"] = "fire"
        elif path.startswith("/api/pulse"):
            fx.pulse()
            resp["effect"] = "pulse"
        elif path.startswith("/api/off"):
            fx.off()
            resp["effect"] = "off"
        else:
            resp = {"ok": False}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())


def parse_qs(path):
    params = {}
    if "?" in path:
        for part in path.split("?", 1)[1].split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v
    return params


def shutdown(sig, frame):
    release_leds()
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    grab_leds()
    print(f"LED Panel: http://0.0.0.0:{PORT}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        release_leds()
