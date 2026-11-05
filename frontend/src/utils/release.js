// Release (CONTEXT.md): only the Reservation's holder or an admin may Release a Switch (#9);
// the other members of its Project still Renew it and Connect and Disconnect its Links. The
// server enforces it (api/api/topology.py may_release) and says per Switch "may_release".

/**
 * @param {boolean} mayRelease - the Switch's "may_release", as the server gives it
 * @param {string|null} holder - the holder's username
 * @returns {string|null} why the caller can't Release it, or null when they can
 */
export function whyNoReleaseBy(mayRelease, holder) {
  if (mayRelease) return null;
  return `Only ${holder || 'its holder'} or an admin can release this Switch.`;
}
