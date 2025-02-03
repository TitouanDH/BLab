<template>
  <div id="topology-controls" class="flex justify-end p-4">
    <button @click="$emit('save')" class="bg-emerald-700 hover:bg-emerald-500 text-white font-bold py-2 px-4 rounded mr-2">
      Save Topology
    </button>
    <button @click="$emit('load')" class="bg-cyan-700 hover:bg-cyan-500 text-white font-bold py-2 px-4 rounded">
      Load Topology
    </button>
  </div>
</template>
