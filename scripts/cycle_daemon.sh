#!/bin/bash
# Claude Code daily wake-up daemon
# Triggers Haiku once at 7:01 AM daily to start the 5-hour auto-reset cycle.
# The system then auto-resets at 12:01 and 17:01 — no extra triggers needed.
#
# Usage: nohup ./cycle_daemon.sh &
# Stop:  kill $(cat /tmp/claude_cycle_daemon.pid)

# ── Configuration ──────────────────────────────────────────────
TIMEZONE="Asia/Shanghai"
WAKEUP_TIME="07:01"
LOG_FILE="/tmp/claude_wakeup.log"
PID_FILE="/tmp/claude_cycle_daemon.pid"
MAX_RETRIES=3
# ───────────────────────────────────────────────────────────────

# Prevent duplicate instances
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Daemon already running (PID $(cat "$PID_FILE")). Exiting."
    exit 1
fi
echo $$ > "$PID_FILE"

trap 'rm -f "$PID_FILE"; log "Daemon stopped."; exit 0' SIGTERM SIGINT

log() {
    echo "$(TZ=$TIMEZONE date '+%Y-%m-%d %H:%M:%S %Z') - $1" | tee -a "$LOG_FILE"
}

trigger_cycle() {
    local attempt=1
    while [ $attempt -le $MAX_RETRIES ]; do
        log "Triggering daily wake-up (attempt $attempt/$MAX_RETRIES)..."
        claude --model claude-haiku-4-5-20251001 -p "早安！请简短回复一句早安。" >> "$LOG_FILE" 2>&1
        if [ $? -eq 0 ]; then
            log "Wake-up successful. 5h cycle started: 7-12, 12-17, 17-22."
            return 0
        fi
        log "Attempt $attempt failed."
        attempt=$((attempt + 1))
        [ $attempt -le $MAX_RETRIES ] && sleep 10
    done
    log "All $MAX_RETRIES attempts failed."
    return 1
}

# Calculate seconds until next 7:01 AM
seconds_until_wakeup() {
    local now_ts
    now_ts=$(TZ=$TIMEZONE date +%s)
    local today
    today=$(TZ=$TIMEZONE date +%Y-%m-%d)

    local target_ts
    target_ts=$(TZ=$TIMEZONE date -d "$today $WAKEUP_TIME" +%s)

    local diff=$((target_ts - now_ts))
    if [ $diff -le 0 ]; then
        # Already past today's 7:01, aim for tomorrow
        local tomorrow
        tomorrow=$(TZ=$TIMEZONE date -d "+1 day" +%Y-%m-%d)
        target_ts=$(TZ=$TIMEZONE date -d "$tomorrow $WAKEUP_TIME" +%s)
        diff=$((target_ts - now_ts))
    fi
    echo $diff
}

log "Daemon started (PID $$). Daily wake-up at $WAKEUP_TIME $TIMEZONE"

while true; do
    wait_secs=$(seconds_until_wakeup)
    hours=$((wait_secs / 3600))
    mins=$(( (wait_secs % 3600) / 60 ))
    log "Next wake-up in ${hours}h${mins}m (sleeping ${wait_secs}s)"
    sleep "$wait_secs"
    trigger_cycle
done
