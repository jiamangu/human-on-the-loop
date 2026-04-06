#!/bin/bash
# Morning wake-up script for Claude Code
# Purpose: Trigger Claude Code's 5-hour usage cycle at 7:01 AM daily
# This locks the reset windows to: 7:00-12:00, 12:00-17:00, 17:00-22:00
#
# Usage: Add to crontab with: crontab -e
#   1 7 * * * /path/to/morning_wakeup.sh >> /tmp/claude_wakeup.log 2>&1

LOG_FILE="/tmp/claude_wakeup.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting morning wake-up..."

# Use Claude with Haiku model to send a simple "Good morning" message
# This triggers the 5-hour usage cycle timer
claude --model claude-haiku-4-5-20251001 -p "Good morning! Please reply with a brief good morning greeting." 2>&1

if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Wake-up successful. 5-hour cycle started."
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Wake-up failed. Will retry on next cron run."
fi
