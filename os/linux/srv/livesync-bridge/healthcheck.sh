#!/usr/bin/env sh
#
# Fail while the Deno file watcher is wedged.
#
# Deno watches the vault through notify-rs, which services every watch on one
# inotify thread. When the kernel event queue overflows, that thread stops
# delivering events and spins inside the read loop instead: a full core of kernel
# time, zero user time, and not a single log line. The bridge looks alive to
# anything that only asks whether the container is up, which is how it can burn a
# core for hours unnoticed.
#
# A healthy watcher is almost entirely idle, so sustained kernel time in that
# thread is the pathology itself rather than a proxy for it.

SAMPLE_SECONDS=5
# 5s of spin is ~500 ticks at 100 Hz. 200 is 40% of a core sustained, far above
# anything real work in this thread produces and far below the wedge.
MAX_TICKS=200

notify_stime() {
    total=0
    for task in /proc/[0-9]*/task/[0-9]*; do
        [ -r "$task/comm" ] || continue
        case "$(cat "$task/comm" 2>/dev/null)" in
            notify*) ;;
            *) continue ;;
        esac
        ticks="$(awk '{print $15}' "$task/stat" 2>/dev/null)"
        case "$ticks" in
            ''|*[!0-9]*) continue ;;
        esac
        total=$((total + ticks))
    done
    echo "$total"
}

before="$(notify_stime)"
sleep "$SAMPLE_SECONDS"
after="$(notify_stime)"
delta=$((after - before))

if [ "$delta" -ge "$MAX_TICKS" ]; then
    echo "watcher wedged: notify thread burned ${delta} ticks of kernel time in ${SAMPLE_SECONDS}s"
    exit 1
fi

exit 0
