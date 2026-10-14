// Copying to the clipboard, the same way everywhere: a toast says it is done. Where this browser
// has no clipboard (plain HTTP, an old browser), the toast gives the text to copy by hand, unless
// it is a secret (reveal: false): the caller then shows it on the page instead.
import { toast } from '../composables/toast.js';

/**
 * @param {string} text - what to copy
 * @param {string} what - what it is, for the toast ("Invite link", "SSH command")
 * @returns {Promise<boolean>} whether it was copied
 */
export async function copyText(text, what, { reveal = true } = {}) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied.`);
    return true;
  } catch {
    toast.error(reveal ? `Couldn't copy here. ${what}: ${text}` : `Couldn't copy here: the ${what.toLowerCase()} is shown, copy it yourself.`);
    return false;
  }
}
