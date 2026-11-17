<script setup lang="ts">
defineProps<{ messages: string[] }>()
</script>

<template>
  <div v-if="messages.length" class="field-messages invalid-feedback d-block" role="alert">
    <div v-for="message in messages" :key="message">{{ message }}</div>
  </div>
</template>
