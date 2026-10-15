// Switches by model family, then by model, in alphabetical order, each with how many of its
// Switches there are and how many are free: the Reservation page's sidebar and its cards.
import { familyOf } from './switchLabel.js';

const byKey = (list, keyOf) => {
  const groups = new Map();
  for (const item of list) {
    const key = keyOf(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
};

/**
 * @param {object[]} switches - list_switch rows (with `model`)
 * @param {(s: object) => boolean} isFree
 * @returns {{ name: string, total: number, free: number, switches: object[],
 *   models: { name: string, total: number, free: number }[] }[]}
 */
export function groupByFamily(switches, isFree) {
  const count = (list) => ({ total: list.length, free: list.filter(isFree).length });
  return byKey(switches, s => familyOf(s.model)).map(([name, list]) => ({
    name,
    switches: list,
    ...count(list),
    models: byKey(list, s => s.model).map(([model, ofModel]) => ({ name: model, ...count(ofModel) })),
  }));
}
