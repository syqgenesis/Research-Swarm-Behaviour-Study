#!/bin/sh
set -u

usage() {
    echo "usage: monitor.sh start RUN_ID [PORT] | monitor.sh stop" >&2
    exit 64
}

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
session=hacky1-monitor
state_file="$project_dir/runs/monitor-watchdog.state"

kill_if_monitor_process() {
    process_id=$1
    expected=$2
    [ -n "$process_id" ] || return 0
    [ "$process_id" -gt 0 ] || return 0
    process_command=$(ps -p "$process_id" -o command= 2>/dev/null || true)
    case "$process_command" in
        *"$expected"*) kill -TERM "$process_id" 2>/dev/null || true ;;
    esac
}

stop_monitor() {
    old_watchdog=
    old_child=
    if [ -f "$state_file" ]; then
        IFS=' ' read -r old_watchdog old_child < "$state_file" || true
        case "$old_watchdog" in
            ''|*[!0-9]*) old_watchdog= ;;
        esac
        case "$old_child" in
            ''|*[!0-9]*) old_child= ;;
        esac
        kill_if_monitor_process "$old_watchdog" \
            "$project_dir/ops/monitor_watchdog.sh"
        kill_if_monitor_process "$old_child" "-m swarm.monitor"
    fi

    attempts=0
    while [ -n "$old_watchdog" ] && kill -0 "$old_watchdog" 2>/dev/null; do
        attempts=$((attempts + 1))
        [ "$attempts" -lt 20 ] || break
        sleep 0.1
    done

    if screen -ls 2>/dev/null | grep -q "[.]${session}[[:space:]]"; then
        screen -S "$session" -X quit 2>/dev/null || true
    fi
    rm -f "$state_file"
}

action=${1:-}
case "$action" in
    stop)
        [ "$#" -eq 1 ] || usage
        stop_monitor
        echo "monitor watchdog stopped"
        ;;
    start)
        [ "$#" -ge 2 ] && [ "$#" -le 3 ] || usage
        run_id=$2
        port=${3:-8767}
        case "$run_id" in
            ''|*[!A-Za-z0-9._-]*)
                echo "invalid run id: $run_id" >&2
                exit 64
                ;;
        esac
        case "$port" in
            ''|*[!0-9]*)
                echo "invalid port: $port" >&2
                exit 64
                ;;
        esac
        if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
            echo "invalid port: $port" >&2
            exit 64
        fi
        command -v screen >/dev/null 2>&1 || {
            echo "screen is required for a persistent monitor" >&2
            exit 69
        }

        stop_monitor
        screen -dmS "$session" \
            env MONITOR_WATCHDOG_STATE_FILE="$state_file" \
            "$project_dir/ops/monitor_watchdog.sh" \
            "$project_dir/.venv/bin/python" -u -m swarm.monitor \
            --run-id "$run_id" --port "$port"

        sleep 0.2
        if ! screen -ls 2>/dev/null | grep -q \
                "[.]${session}[[:space:]]"; then
            echo "monitor watchdog failed to start" >&2
            exit 70
        fi
        echo "monitor watchdog: $session"
        echo "URL: http://localhost:$port/"
        ;;
    *) usage ;;
esac
