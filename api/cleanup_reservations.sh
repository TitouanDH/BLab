#!/bin/bash

# Script to run cleanup command manually
# Usage: ./cleanup_reservations.sh [--once]

echo "🧹 Starting reservation cleanup service..."

if [ "$1" = "--once" ]; then
    echo "Running cleanup once..."
    python manage.py cleanup_expired_reservations --once
else
    echo "Starting continuous cleanup monitoring (5 minutes interval)..."
    echo "Press Ctrl+C to stop"
    python manage.py cleanup_expired_reservations --interval 300
fi
