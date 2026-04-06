#!/bin/bash
# Claude Code 5-hour cycle daemon
# Replaces crontab in environments where cron is unavailable.
# Triggers Haiku at 7:01, 12:01, 17:01 daily to lock reset windows.
#
# Usage: nohup ./cycle_daemon.sh &

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/tmp/claude_wakeup.log"
PID_FILE="/tmp/claude_cycle_daemon.pid"

# Prevent duplicate instances
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Daemon already running (PID $(cat "$PID_FILE")). Exiting."
    exit 1
fi
echo $$ > "$PID_FILE"

trap 'rm -f "$PID_FILE"; exit 0' SIGTERM SIGINT

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

trigger_cycle() {
    log "Triggering Claude Code cycle reset..."
    claude --model claude-haiku-4-5-20251001 -p "早安！请简短回复一句早安。" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        log "Cycle triggered successfully."
    else
        log "Trigger failed."
    fi
}

# Target hours (24h format)
TARGET_HOURS="7 12 17"

log "Daemon started (PID $$). Target hours: $TARGET_HOURS"

while true; do
    current_hour=$(date +%H | sed 's/^0//')
    current_min=$(date +%M | sed 's/^0//')

    for h in $TARGET_HOURS; do
        if [ "$current_hour" -eq "$h" ] && [ "$current_min" -eq 1 ]; then
            trigger_cycle
            # Sleep 60s to avoid re-triggering in the same minute
            sleep 60
            break
        fi
    done

    # Check every 30 seconds
    sleep 30
done
