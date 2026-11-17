<script setup lang="ts">
import { computed } from 'vue'

import { useConfigStore } from '@/stores/config'

const props = defineProps<{ path: string; label?: string }>()
const config = useConfigStore()
const changed = computed(() => !config.isDefault(props.path))
</script>

<template>
  <button
    v-if="changed"
    type="button"
    class="reset-link btn btn-link btn-sm p-0 ms-2 align-baseline"
    @click="config.resetToDefault(path)"
  >
    {{ label ?? 'Reset to default' }}
  </button>
</template>
