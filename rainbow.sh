#!/bin/sh
# Rainbow LED ring for Yandex Station Lite 1
# 3 zones, 120° phase shift, smooth HSV rotation
# Usage: sh rainbow.sh [speed] — speed in ms per frame (default 50)
# Ctrl+C to stop (LEDs turn off on exit)

LED=/sys/class/leds/micro
DELAY=${1:-50}
SLEEP_SEC=$(awk "BEGIN{printf \"%.3f\", $DELAY/1000}")

# Disable LED triggers so firmware animations don't override us
for c in red green blue; do
    for z in 1 2 3; do
        echo none > "$LED:${c}:ring${z}/trigger" 2>/dev/null
    done
done

cleanup() {
    for c in red green blue; do
        for z in 1 2 3; do
            echo 0 > "$LED:${c}:ring${z}/brightness"
            echo led_ring_fb > "$LED:${c}:ring${z}/trigger" 2>/dev/null
        done
    done
    exit 0
}
trap cleanup INT TERM EXIT

# HSV to RGB (H=0-359, S=V=255 fixed, output 0-255)
# Pure shell math — no awk in the hot loop
hsv() {
    H=$1
    # sector 0-5
    S=$((H / 60))
    F=$((H % 60))
    # V=200 (not blinding), fixed S=255
    V=200
    P=0
    Q=$(( V * (60 - F) / 60 ))
    T=$(( V * F / 60 ))
    case $S in
        0) R=$V; G=$T; B=$P ;;
        1) R=$Q; G=$V; B=$P ;;
        2) R=$P; G=$V; B=$T ;;
        3) R=$P; G=$Q; B=$V ;;
        4) R=$T; G=$P; B=$V ;;
        *) R=$V; G=$P; B=$Q ;;
    esac
}

set_zone() {
    echo "$2" > "$LED:red:ring${1}/brightness"
    echo "$3" > "$LED:green:ring${1}/brightness"
    echo "$4" > "$LED:blue:ring${1}/brightness"
}

HUE=0
while true; do
    # Zone 1: base hue
    hsv $HUE
    set_zone 1 $R $G $B

    # Zone 2: +120°
    H2=$(( (HUE + 120) % 360 ))
    hsv $H2
    set_zone 2 $R $G $B

    # Zone 3: +240°
    H3=$(( (HUE + 240) % 360 ))
    hsv $H3
    set_zone 3 $R $G $B

    HUE=$(( (HUE + 2) % 360 ))
    sleep "$SLEEP_SEC"
done
