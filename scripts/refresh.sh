#!/bin/bash

# MatchLayer System Refresh Script
# This script is designed to be called by cron for periodic system refresh

# Configuration
API_URL="${MATCHLAYER_API_URL:-http://localhost:8000}"
LOG_DIR="${MATCHLAYER_LOG_DIR:-/var/log/matchlayer}"
LOG_FILE="$LOG_DIR/refresh.log"
MAX_LOG_SIZE=10485760  # 10MB

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR" 2>/dev/null

# Rotate log if too large
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null) -gt $MAX_LOG_SIZE ]; then
    mv "$LOG_FILE" "$LOG_FILE.old"
fi

# Timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Log start
echo "[$TIMESTAMP] Starting system refresh..." >> "$LOG_FILE"

# Make API call
RESPONSE=$(curl -s -X POST "$API_URL/system/refresh" \
    -H "Content-Type: application/json" \
    -w "\nHTTP_CODE:%{http_code}" \
    --max-time 300 \
    --connect-timeout 10)

# Extract HTTP code and body
HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | sed '/HTTP_CODE/d')

# Log result
if [ "$HTTP_CODE" = "200" ]; then
    echo "[$TIMESTAMP] ✓ SUCCESS" >> "$LOG_FILE"
    echo "$BODY" | python3 -m json.tool >> "$LOG_FILE" 2>/dev/null || echo "$BODY" >> "$LOG_FILE"
    EXIT_CODE=0
else
    echo "[$TIMESTAMP] ✗ FAILURE (HTTP $HTTP_CODE)" >> "$LOG_FILE"
    echo "$BODY" >> "$LOG_FILE"
    EXIT_CODE=1
fi

echo "[$TIMESTAMP] Refresh completed (exit code: $EXIT_CODE)" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"

exit $EXIT_CODE
