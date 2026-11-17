<script setup lang="ts">
import { useRunStore } from '@/stores/run'

defineProps<{ title: string; lead?: string; hideRunNotice?: boolean }>()
const run = useRunStore()
</script>

<template>
  <header class="step-header mb-4">
    <h1 class="h3 mb-2">{{ title }}</h1>
    <p v-if="lead" class="text-secondary mb-0 step-header__lead">{{ lead }}</p>
    <div v-if="run.running && !hideRunNotice" class="alert alert-info mt-3 mb-0 py-2">
      A run is in progress. Changes here apply to the next run.
    </div>
  </header>
</template>

<style scoped>
.step-header__lead {
  max-width: 46rem;
}
</style>
