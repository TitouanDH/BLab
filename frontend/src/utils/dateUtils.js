/**
 * Dates: one way to show them everywhere ("9 Oct 2026, 14:00"), and the helpers the
 * Reserve dialog's date input needs.
 */
import { MAX_RESERVATION_DAYS } from './constants.js';

const FORMAT = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
});

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * The one date formatter.
 * @param {string|Date} value - an ISO date string or a Date
 * @param {{ relative?: boolean }} options - relative: add how far it is from today
 *   ("in 3 days", "today", "ended 2 days ago"), for end dates
 * @returns {string} '' for no date
 */
export function formatDate(value, { relative = false } = {}) {
  if (!value) return '';
  const date = new Date(value);
  if (isNaN(date.getTime())) return '';
  const text = FORMAT.format(date);
  return relative ? `${text} (${relativeDay(date)})` : text;
}

function relativeDay(date) {
  const now = new Date();
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(date) - startOf(now)) / DAY_MS);
  if (date < now) {
    if (days === 0) return 'ended today';
    return days === -1 ? 'ended yesterday' : `ended ${-days} days ago`;
  }
  if (days === 0) return 'today';
  if (days === 1) return 'tomorrow';
  if (days <= 7) return `in ${days} days`;
  const weeks = Math.floor(days / 7);
  return weeks === 1 ? 'in 1 week' : `in ${weeks} weeks`;
}

/**
 * Format date for HTML input elements (YYYY-MM-DD)
 * @param {string|Date} dateInput - Date string or Date object
 * @returns {string} - Date in YYYY-MM-DD format
 */
export function formatForInput(dateInput) {
  if (!dateInput) return '';

  const date = new Date(dateInput);
  if (isNaN(date.getTime())) return '';

  // The local day, as the date input shows it
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * Get minimum date for reservations (tomorrow)
 * @returns {string} - Date in YYYY-MM-DD format
 */
export function getMinReservationDate() {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  return formatForInput(tomorrow);
}

/**
 * Get maximum date for reservations (14 days from now)
 * @returns {string} - Date in YYYY-MM-DD format
 */
export function getMaxReservationDate() {
  const maxDate = new Date();
  maxDate.setDate(maxDate.getDate() + MAX_RESERVATION_DAYS);
  return formatForInput(maxDate);
}

/**
 * The end of the chosen day, but never later than MAX_RESERVATION_DAYS from now, unless
 * an admin chose a day beyond it on purpose (an admin exception)
 * @param {string} day - Date in YYYY-MM-DD format
 * @returns {Date}
 */
export function reservationEnd(day) {
  const end = new Date(`${day}T23:59:59`);
  const limit = new Date(Date.now() + MAX_RESERVATION_DAYS * DAY_MS);
  if (day <= getMaxReservationDate() && end > limit) {
    return limit;
  }
  return end;
}

/**
 * Get default reservation end date (7 days from now)
 * @returns {string} - Date in YYYY-MM-DD format
 */
export function getDefaultReservationDate() {
  const defaultDate = new Date();
  defaultDate.setDate(defaultDate.getDate() + 7);
  return formatForInput(defaultDate);
}
