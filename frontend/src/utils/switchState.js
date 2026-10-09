/**
 * The one state a Switch is in, with the label and badge tone every page shows for it
 * (words from CONTEXT.md). A Reservation wins: nobody can reserve a reserved Switch, and a
 * Switch that nobody holds says why it can't be reserved, if it can't.
 *
 * @param {object} s
 * @param {string|null} s.holder - the holder's username, null when not reserved
 * @param {boolean} s.mine - whether the current user is the holder
 * @param {string|null} s.unavailable - 'out_of_service', 'quarantine' or 'cleaning_up' (the
 *   API's `unavailable.state`), null when nothing stops a Reservation
 * @returns {{ key: string, label: string, tone: string }} key: free, mine, reserved,
 *   quarantine, out_of_service or cleaning_up; tone: a UiBadge tone
 */
export function switchState({ holder = null, mine = false, unavailable = null }) {
  if (holder) {
    return mine
      ? { key: 'mine', label: 'Reserved by me', tone: 'primary' }
      : { key: 'reserved', label: `Reserved by ${holder}`, tone: 'neutral' };
  }
  if (unavailable) return { key: unavailable, ...(UNAVAILABLE[unavailable] || { label: 'Unavailable', tone: 'neutral' }) };
  return { key: 'free', label: 'Free', tone: 'success' };
}

// Why a Switch that nobody holds can't be reserved
export const UNAVAILABLE = {
  quarantine: { label: 'Quarantine', tone: 'strong-warning' },
  out_of_service: { label: 'Out of service', tone: 'dark' },
  cleaning_up: { label: 'Being Cleaned up', tone: 'primary' },
};
