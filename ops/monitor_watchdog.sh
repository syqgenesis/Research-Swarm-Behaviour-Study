#!/bin/sh
set -u

if [ "$#" -eq 0 ]; then
    echo "usage: monitor_watchdog.sh COMMAND [ARG ...]" >&2
    exit 64
fi

restart_delay=${MONITOR_RESTART_DELAY_S:-1}
max_starts=${MONITOR_WATCHDOG_MAX_STARTS:-0}
state_file=${MONITOR_WATCHDOG_STATE_FILE:-}
starts=0
child_pid=

write_state() {
    [ -n "$state_file" ] || return 0
    state_tmp="${state_file}.tmp.$$"
    printf '%s %s\n' "$$" "${child_pid:-0}" > "$state_tmp"
    mv "$state_tmp" "$state_file"
}

clear_state() {
    [ -n "$state_file" ] || return 0
    rm -f "$state_file" "${state_file}.tmp.$$"
}

stop_watchdog() {
    trap - INT TERM HUP
    if [ -n "$child_pid" ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    clear_state
    exit 0
}

trap stop_watchdog INT TERM HUP
write_state

while :; do
    starts=$((starts + 1))
    "$@" &
    child_pid=$!
    write_state
    wait "$child_pid"
    status=$?
    child_pid=
    write_state

    if [ "$max_starts" -gt 0 ] && [ "$starts" -ge "$max_starts" ]; then
        clear_state
        exit "$status"
    fi

    echo "monitor exited with status $status; restarting in ${restart_delay}s" >&2
    sleep "$restart_delay"
done
