<template>
  <!-- A Switch's actions on the Reservation page: one Reserve (the default days) for a free
       Switch, and every other action its state allows in a ⋯ menu -->
  <div class="inline-flex items-center gap-1">
    <UiButton
      v-if="state === 'free'" size="sm" variant="primary"
      :pending="quickReserving === item.id" :disabled="quickReserving !== null"
      :title="`Reserve for ${DEFAULT_RESERVATION_DAYS} days`"
      @click="$emit('reserve', item.id)"
    >Reserve</UiButton>
    <!-- Keeps the Reserve width in a table row without one, so the ⋯ line up -->
    <span v-else-if="keepSlot" class="invisible px-2.5 text-xs font-semibold" aria-hidden="true">Reserve</span>
    <Menu v-if="items.length" as="div" class="relative">
      <MenuButton
        class="rounded-md p-1 text-gray-500 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
        :aria-label="`More actions for ${switchLabel(item)}`"
      >
        <EllipsisHorizontalIcon class="h-4 w-4" aria-hidden="true" />
      </MenuButton>
      <MenuItems class="absolute right-0 z-30 mt-1 w-60 origin-top-right rounded-md bg-white py-1 text-left text-sm shadow-lg ring-1 ring-black/5 focus:outline-none">
        <MenuItem v-for="entry in items" :key="entry.label" v-slot="{ active, disabled }" :disabled="entry.disabled">
          <router-link v-if="entry.to" :to="entry.to" :class="['block px-3 py-1.5 text-gray-800', active ? 'bg-gray-100' : '']">
            {{ entry.label }}
            <span class="block text-xs text-gray-500">{{ entry.hint }}</span>
          </router-link>
          <button
            v-else
            type="button"
            :disabled="disabled"
            :class="['block w-full px-3 py-1.5 text-left', active ? 'bg-gray-100' : '',
                     disabled ? 'cursor-not-allowed text-gray-400' : entry.danger ? 'text-danger-700' : 'text-gray-800']"
            @click="entry.run()"
          >
            {{ entry.label }}
            <span class="block text-xs text-gray-500">{{ entry.hint }}</span>
          </button>
        </MenuItem>
      </MenuItems>
    </Menu>
  </div>
</template>

<script setup>
// item: a Reservation page row (a list_switch Switch with its `reservation` and `mine`)
import { computed } from 'vue';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/vue';
import { EllipsisHorizontalIcon } from '@heroicons/vue/20/solid';
import UiButton from '../ui/UiButton.vue';
import { DEFAULT_RESERVATION_DAYS, MAX_RESERVATION_DAYS, RENEWAL_DAYS } from '../../utils/constants.js';
import { plural } from '../../utils/plural.js';
import { switchLabel } from '../../utils/switchLabel.js';

const props = defineProps({
  item: { type: Object, required: true },
  state: { type: String, required: true },  // its switchState() key
  renewing: Boolean,
  quickReserving: { type: Number, default: null },  // the Switch whose one-click Reserve is under way
  keepSlot: Boolean,
});

// reserve: for the default days at once; reserve-until: choose the end date first
const emit = defineEmits(['reserve', 'reserve-until', 'release', 'renew']);

const items = computed(() => {
  const { item, state } = props;
  const reservation = item.reservation;
  if (state === 'free') {
    return [{ label: 'Reserve until…', hint: `Choose the end date (up to ${MAX_RESERVATION_DAYS} days)`, run: () => emit('reserve-until', item.id) }];
  }
  if (reservation?.may_work) {
    return [
      ...(reservation.admin_exception ? [] : [{
        label: 'Renew',
        hint: reservation.renewals_left
          ? `+${RENEWAL_DAYS} days, ${plural(reservation.renewals_left, 'Renewal', 'Renewals')} left`
          : 'No Renewals left',
        disabled: !reservation.renewals_left || props.renewing,
        run: () => emit('renew', item.id),
      }]),
      { label: 'Release', hint: 'Tears down its Links, then Cleans it up', danger: true, disabled: props.renewing, run: () => emit('release', item.id) },
    ];
  }
  if (state === 'quarantine') {
    return [{ label: 'Re-check on Lab status', hint: 'Inspect it again to lift the Quarantine', to: '/status' }];
  }
  return [];
});
</script>
