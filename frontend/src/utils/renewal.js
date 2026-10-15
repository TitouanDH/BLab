// Renewal (CONTEXT.md): why a Reservation can't be Renewed, in the words of its rules
// (api/api/reservations.py), or null when it can.
import { MAX_RENEWALS } from './constants.js';

/**
 * @param {{ admin_exception: boolean, end_date: string|null, renewals_left: number }} reservation
 * @returns {string|null}
 */
export function whyNoRenewal(reservation) {
  if (reservation.admin_exception) return 'An admin set this end date: ask an admin to change it.';
  if (reservation.end_date && new Date(reservation.end_date) <= new Date()) return 'Expired: it is being Released.';
  if (!reservation.renewals_left) return `No Renewals left: it has been Renewed ${MAX_RENEWALS} times.`;
  return null;
}
