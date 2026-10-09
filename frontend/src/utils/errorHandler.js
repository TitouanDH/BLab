/**
 * Turning API errors into plain sentences for the user (shown in toasts).
 * The server's own message is kept, because it says why (Quarantine, Out of service, ...),
 * but never raw backbone output: that only goes to the console.
 */

const UNREACHABLE_MESSAGE = 'Cannot reach BLab. Check your connection and try again.';

// Server messages that wrap what a backbone answered: the user gets the sentence, the
// console gets the rest (see api/links.py)
const BACKBONE_FAILURES = [
  [/^Ports failed to connect\b/i,
    "The Link couldn't be made: a backbone refused the change, and BLab undid what it had configured. Try again, or tell an admin if it keeps failing."],
  [/^Ports failed to disconnect\b/i,
    "The Link couldn't be torn down: a backbone refused the change. Try again, or tell an admin if it keeps failing."],
  [/^Restoring .* failed\b/i,
    "BLab couldn't build the Link again on a backbone. Tell an admin if it keeps failing."],
];

// Signs of text read from a backbone or Switch rather than written for a user
const RAW_OUTPUT = /\n|\(diag |' (failed|refused) on |Request to \S+ failed|Unexpected response format/;
const MAX_LENGTH = 300;

/**
 * A server message made fit to show: known backbone failures become a sentence, anything
 * else that looks like device output is cut to its first sentence.
 * @param {string} text - what the server said
 * @returns {string}
 */
export function plainMessage(text) {
  if (typeof text !== 'string') return '';
  const message = text.trim();
  for (const [pattern, sentence] of BACKBONE_FAILURES) {
    if (pattern.test(message)) return sentence;
  }
  if (RAW_OUTPUT.test(message) || message.length > MAX_LENGTH) {
    const first = message.split(/\n|(?<=\.)\s|: /)[0].trim();
    return first.endsWith('.') ? first : `${first}.`;
  }
  return message;
}

/**
 * What the server said, from the shapes BLab's API uses: { detail }, { error },
 * { warning }, or a form's field errors ({ username: ['...'] }).
 * @returns {string|null}
 */
export function serverMessage(data) {
  if (!data || typeof data !== 'object') return null;
  for (const key of ['detail', 'error', 'warning']) {
    if (typeof data[key] === 'string' && data[key]) return data[key];
  }
  const fieldErrors = Object.values(data).flat().filter(v => typeof v === 'string');
  return fieldErrors.length ? fieldErrors.join(' ') : null;
}

/**
 * A plain sentence for a failed API call.
 * @param {Error} error - the axios error
 * @param {string} action - what was being done, as a verb phrase ("reserve the Switch")
 * @returns {string}
 */
export function handleApiError(error, action = '') {
  const status = error?.response?.status;
  if (!error?.response) return UNREACHABLE_MESSAGE;
  if (status >= 500) {
    return status === 500
      ? 'BLab ran into an error. Please try again later, or tell an admin.'
      : UNREACHABLE_MESSAGE;
  }
  const message = serverMessage(error.response.data);
  // A rejected token or a missing session; a wrong password says so itself
  if (status === 401 && (!message || /token|credentials were not provided/i.test(message))) {
    return 'Your session has ended. Please log in again.';
  }
  if (message) return plainMessage(message);
  if (status === 403) return 'You do not have permission to do this.';
  if (status === 404) return 'BLab could not find what you asked for. It may have changed meanwhile.';
  return action ? `Couldn't ${action}. Please try again.` : 'Something went wrong. Please try again.';
}

/**
 * Log errors with everything the server said, for debugging
 * @param {Error} error - The error object
 * @param {string} context - Context of where the error occurred
 */
export function logError(error, context = '') {
  console.error(`[${context}] Error:`, {
    message: error?.message,
    status: error?.response?.status,
    data: error?.response?.data,
    stack: error?.stack
  });
}
