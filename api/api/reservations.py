"""
Reservation limits and Renewal (see CONTEXT.md), enforced by the server.

A Reservation ends at most MAX_LENGTH from when it is made. A Renewal pushes its end date
back by RENEWAL, at most MAX_RENEWALS times, so it lasts four weeks at most. Admins may set
any end date: such a Reservation is an admin exception, and is not Renewed.
"""
from datetime import datetime, timedelta
from typing import Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Reservation

MAX_LENGTH = timedelta(days=14)
RENEWAL = timedelta(days=7)
MAX_RENEWALS = 2
# The end date is checked against the server's clock: a few minutes spent between the
# browser computing "14 days from now" and the request arriving don't make it too far
SLACK = timedelta(minutes=5)


class LimitError(Exception):
    """A Reservation or a Renewal the limits refuse. The message is meant for the user."""


class NotAllowed(LimitError):
    """The user may not Renew this Reservation."""


def parse_end_date(value) -> Optional[datetime]:
    """The end date from a request, timezone-aware, or None when it is missing or not ISO 8601."""
    if not value or not isinstance(value, str):
        return None
    try:
        end_date = parse_datetime(value)
    except ValueError:
        return None
    if end_date is not None and timezone.is_naive(end_date):
        end_date = timezone.make_aware(end_date)
    return end_date


def check_end_date(end_date: Optional[datetime], user: User, now: datetime) -> bool:
    """
    Raises LimitError if user may not reserve until end_date. Returns whether it is an admin
    exception: an end date only an admin may set.
    """
    if end_date is None:
        raise LimitError("An end date is required (ISO 8601).")
    if end_date <= now:
        raise LimitError("The end date must be in the future.")
    if end_date <= now + MAX_LENGTH + SLACK:
        return False
    if user.is_staff:
        return True
    raise LimitError(f"A Reservation lasts {MAX_LENGTH.days} days at most. Renew it to keep the Switch longer.")


def renewals_left(reservation: Reservation) -> int:
    """How many more times the Reservation can be Renewed: none for an admin exception."""
    if reservation.admin_exception:
        return 0
    return max(MAX_RENEWALS - reservation.renewals, 0)


def renew(reservation: Reservation, user: User, now: Optional[datetime] = None) -> Reservation:
    """
    Pushes the Reservation's end date back by RENEWAL, on behalf of user: the holder or a
    user the Topology is shared with. Raises NotAllowed, or LimitError when it can't be
    Renewed. Returns the Reservation as it is now.
    """
    from . import topology  # api.topology imports api.serializers, which imports this module
    now = now or timezone.now()
    if not topology.may_work(user, reservation.user_id):
        raise NotAllowed("Only the holder, or a user their Topology is shared with, may Renew this Reservation.")
    with transaction.atomic():
        # Locked, so that two Renewals at once count twice
        reservation = Reservation.objects.select_for_update().filter(pk=reservation.pk).first()
        if reservation is None:
            raise LimitError("This Reservation has ended.")
        if reservation.admin_exception:
            raise LimitError("An admin set this Reservation's end date: ask an admin to change it.")
        if reservation.end_date is None or reservation.end_date <= now:
            raise LimitError("This Reservation has expired: it is being released.")
        if reservation.renewals >= MAX_RENEWALS:
            raise LimitError(f"This Reservation has already been Renewed {MAX_RENEWALS} times.")
        reservation.end_date += RENEWAL
        reservation.renewals += 1
        reservation.save(update_fields=['end_date', 'renewals'])
    return reservation


def cap_unbounded(now: datetime) -> int:
    """
    Gives every Reservation with no end date, or one further than MAX_LENGTH from now that
    neither a Renewal nor an admin explains, the end date now + MAX_LENGTH. Those are the
    ones made before the limits existed (migration 0007 did the same once), or by
    production's older code until it runs this (docs/adr/0002). Returns how many.
    """
    limit = now + MAX_LENGTH
    return (Reservation.objects
            .filter(admin_exception=False, renewals=0)
            .filter(Q(end_date__isnull=True) | Q(end_date__gt=limit + SLACK))
            .update(end_date=limit))
