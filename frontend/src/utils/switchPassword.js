// The rules of a Switch password, checked as the user types. BLab checks them again
// (api/api/switch_password.py, which says why each one is there): keep the two in step.
const FORBIDDEN = ['switch@123', 'switch', 'admin'];

// Each rule: { id, label, ok(password, names) }, names being the user's username and Switch login
export const SWITCH_PASSWORD_RULES = [
  { id: 'length', label: '8 to 30 characters', ok: p => p.length >= 8 && p.length <= 30 },
  { id: 'upper', label: 'An upper case letter', ok: p => /[A-Z]/.test(p) },
  { id: 'lower', label: 'A lower case letter', ok: p => /[a-z]/.test(p) },
  { id: 'digit', label: 'A digit', ok: p => /[0-9]/.test(p) },
  { id: 'symbol', label: 'A symbol', ok: p => /[^A-Za-z0-9]/.test(p) },
  { id: 'chars', label: 'No spaces, accents, ! " or \'', ok: p => /^[\x21-\x7e]*$/.test(p) && !/[!"']/.test(p) },
  {
    id: 'username', label: 'Not containing your username',
    ok: (p, names) => !names.filter(Boolean).some(n => p.toLowerCase().includes(n.toLowerCase())),
  },
  { id: 'old', label: "Not the lab's old password", ok: p => !FORBIDDEN.includes(p.toLowerCase()) },
];

export function checkSwitchPassword(password, names) {
  return SWITCH_PASSWORD_RULES.map(rule => ({ ...rule, passed: rule.ok(password, names) }));
}
