<template>
  <div>
    <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
      <Card 
        v-for="item in switches" 
        :key="item.id" 
        :item="item" 
        :isLoading="isLoading" 
        :expandedItemId="expandedItemId"
        @toggleDetails="$emit('toggleDetails', item.id)"
        @reserve="$emit('reserve', item.id)"
        @release="$emit('release', item.id)"
      />
    </div>
  </div>
</template>

<script setup>
import { defineProps } from 'vue';
import Card from './Card.vue';

const props = defineProps({
  switches: Array,
  isLoading: Boolean,
  expandedItemId: Number
});
</script>
