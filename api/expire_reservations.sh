#!/bin/bash

# Runs expiry by hand: releases the Reservations whose end date has passed.
# Usage: ./expire_reservations.sh [--once]
# Production's `expiry` compose service already runs it; never run it against the shared
# database from pre-prod (docs/adr/0002-preprod-shares-production-database.md).

if [ "$1" = "--once" ]; then
    echo "Expiring reservations once..."
    python manage.py expire_reservations --once
else
    echo "Expiring reservations every 5 minutes. Press Ctrl+C to stop."
    python manage.py expire_reservations --interval 300
fi
