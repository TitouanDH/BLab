<template>
  <div class="help-panel bg-white border border-gray-300 rounded p-4 shadow-lg">
    <h3 class="text-lg font-semibold mb-2">How to Interact with the Topology</h3>
    <p class="mb-1">Right-click on a switch to release it.</p>
    <p class="mb-1">Right-click on a link to disconnect it.</p>
    <p class="mb-1">Shift-click on two ports to connect them.</p>
  </div>
</template>

<style scoped>
.help-panel {
  position: fixed;
  bottom: 20px;
  right: 80px;
  background-color: #fff;
  border: 1px solid #ccc;
  border-radius: 4px;
  padding: 10px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  z-index: 999;
}

.help-panel h3 {
  margin-top: 0;
  margin-bottom: 10px;
}

.help-panel p {
  margin: 5px 0;
}
</style>
