/**
 * Date formatting utilities for consistent date handling across the application
 */

/**
 * Format date with relative time information (e.g., "Today", "Tomorrow", "In 3 days")
 * @param {string|Date} dateInput - Date string or Date object
 * @returns {string} - Formatted date string with relative info
 */
export function formatDateWithRelative(dateInput) {
  if (!dateInput) return '';
  
  const date = new Date(dateInput);
  if (isNaN(date.getTime())) return 'Invalid date';
  
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const targetDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  
  const daysDiff = Math.ceil((targetDate - today) / (1000 * 60 * 60 * 24));
  
  const options = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  };
  
  let baseDate = date.toLocaleDateString('en-US', options);
  
  if (daysDiff < 0) {
    const absDays = Math.abs(daysDiff);
    if (absDays === 1) {
      baseDate += ' (Yesterday)';
    } else {
      baseDate += ` (${absDays} days ago)`;
    }
  } else if (daysDiff === 0) {
    baseDate += ' (Today)';
  } else if (daysDiff === 1) {
    baseDate += ' (Tomorrow)';
  } else if (daysDiff <= 7) {
    baseDate += ` (In ${daysDiff} days)`;
  } else {
    const weeks = Math.floor(daysDiff / 7);
    if (weeks === 1) {
      baseDate += ' (In 1 week)';
    } else {
      baseDate += ` (In ${weeks} weeks)`;
    }
  }
  
  return baseDate;
}

/**
 * Format date with expiration status for UI display
 * @param {string|Date} dateInput - Date string or Date object
 * @returns {string} - Formatted date string with expiration status
 */
export function formatDateWithExpiration(dateInput) {
  if (!dateInput) return '';
  
  const formattedDate = formatDateWithRelative(dateInput);
  const expired = isDateExpired(dateInput);
  
  return expired ? formattedDate + ' - EXPIRED' : formattedDate;
}

/**
 * Check if a date is expired (in the past)
 * @param {string|Date} dateInput - Date string or Date object
 * @returns {boolean} - True if date is in the past
 */
export function isDateExpired(dateInput) {
  if (!dateInput) return false;
  
  const date = new Date(dateInput);
  if (isNaN(date.getTime())) return false;
  
  return date < new Date();
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
  
  return date.toISOString().split('T')[0];
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
 * Get maximum date for reservations (21 days from now)
 * @returns {string} - Date in YYYY-MM-DD format
 */
export function getMaxReservationDate() {
  const maxDate = new Date();
  maxDate.setDate(maxDate.getDate() + 21);
  return formatForInput(maxDate);
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
