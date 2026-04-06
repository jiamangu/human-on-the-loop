#!/bin/bash
# Claude Code 5-hour cycle daemon
# Replaces crontab in environments where cron is unavailable.
# Calculates exact sleep duration to next target time — no polling.
#
# Usage: nohup ./cycle_daemon.sh &
# Stop:  kill $(cat /tmp/claude_cycle_daemon.pid)

# ── Configuration ──────────────────────────────────────────────
TIMEZONE="Asia/Shanghai"              # Your local timezone
TARGET_TIMES="07:01 12:01 17:01"      # Trigger times (in your timezone)
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
        log "Triggering cycle reset (attempt $attempt/$MAX_RETRIES)..."
        claude --model claude-haiku-4-5-20251001 -p "早安！请简短回复一句早安。" >> "$LOG_FILE" 2>&1
        if [ $? -eq 0 ]; then
            log "Cycle triggered successfully."
            return 0
        fi
        log "Attempt $attempt failed."
        attempt=$((attempt + 1))
        [ $attempt -le $MAX_RETRIES ] && sleep 10
    done
    log "All $MAX_RETRIES attempts failed."
    return 1
}

# Calculate seconds until the next target time
seconds_until_next() {
    local now_ts
    now_ts=$(TZ=$TIMEZONE date +%s)
    local today
    today=$(TZ=$TIMEZONE date +%Y-%m-%d)
    local tomorrow
    tomorrow=$(TZ=$TIMEZONE date -d "+1 day" +%Y-%m-%d)

    local nearest=999999
    for t in $TARGET_TIMES; do
        # Try today first
        local target_ts
        target_ts=$(TZ=$TIMEZONE date -d "$today $t" +%s)
        local diff=$((target_ts - now_ts))
        if [ $diff -gt 0 ] && [ $diff -lt $nearest ]; then
            nearest=$diff
        fi
        # Then tomorrow
        target_ts=$(TZ=$TIMEZONE date -d "$tomorrow $t" +%s)
        diff=$((target_ts - now_ts))
        if [ $diff -gt 0 ] && [ $diff -lt $nearest ]; then
            nearest=$diff
        fi
    done
    echo $nearest
}

log "Daemon started (PID $$). Timezone: $TIMEZONE, Targets: $TARGET_TIMES"

while true; do
    wait_secs=$(seconds_until_next)
    next_time=$(TZ=$TIMEZONE date -d "+${wait_secs} seconds" '+%H:%M:%S')
    log "Next trigger at $next_time (sleeping ${wait_secs}s)"
    sleep "$wait_secs"
    trigger_cycle
done
