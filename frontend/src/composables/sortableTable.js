// One sortable table: one column at a time, both ways, empty values always last. Lab status
// and the Reservation page share it.
//
//   const { sort, sortBy, sortIcon, ariaSort, sorted } = useSortableTable(SORT_VALUES, { key: 'switch' }, s => [s.model, s.id]);
//
// sortValues: { [column key]: row => the value it sorts on (a number, a string, or an array
// of them), or null for an empty cell }. tieBreak: row => the value that orders equal rows.
import { ref } from 'vue';
import { ChevronDownIcon, ChevronUpDownIcon, ChevronUpIcon } from '@heroicons/vue/20/solid';

// A date as a number to sort on, or null for none
export const time = (date) => {
  const t = date ? new Date(date).getTime() : NaN;
  return Number.isNaN(t) ? null : t;
};

export const compare = (a, b) => {
  if (Array.isArray(a)) {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
  }
  if (a === b) return 0;
  return a < b ? -1 : 1;
};

export function useSortableTable(sortValues, { key, dir = 'asc' }, tieBreak) {
  const sort = ref({ key, dir });

  const sortBy = (column) => {
    sort.value = sort.value.key === column
      ? { key: column, dir: sort.value.dir === 'asc' ? 'desc' : 'asc' }
      : { key: column, dir: 'asc' };
  };

  const sortIcon = (column) => {
    if (sort.value.key !== column) return ChevronUpDownIcon;
    return sort.value.dir === 'asc' ? ChevronUpIcon : ChevronDownIcon;
  };

  const ariaSort = (column) => {
    if (sort.value.key !== column) return 'none';
    return sort.value.dir === 'asc' ? 'ascending' : 'descending';
  };

  // The rows in the current order (a new array)
  const sorted = (rows) => {
    const value = sortValues[sort.value.key];
    const sign = sort.value.dir === 'asc' ? 1 : -1;
    return rows
      .map(row => ({ row, v: value(row) }))
      .sort((a, b) => {
        const empty = (a.v === null) - (b.v === null);
        if (empty) return empty;
        const order = a.v === null ? 0 : sign * compare(a.v, b.v);
        return order || compare(tieBreak(a.row), tieBreak(b.row));
      })
      .map(({ row }) => row);
  };

  return { sort, sortBy, sortIcon, ariaSort, sorted };
}
