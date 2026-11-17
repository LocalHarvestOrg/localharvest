<script setup lang="ts">
import { onDeactivated, ref } from 'vue'

import ModelBenchmark from '@/components/ModelBenchmark.vue'
import type { ModelOption } from '@/api/types'
import { formatBytes } from '@/stores/hardware'
import { useModelsStore, VERDICT_TEXT } from '@/stores/models'

// "Choose another model" in Settings: the models the app knows work, how each
// would run on this computer, and deleting downloaded ones.

const models = useModelsStore()
const confirmDelete = ref<string | null>(null)

onDeactivated(() => {
  confirmDelete.value = null
})

/** Memory needs to a tenth of a GB, as the model descriptions give them. */
function formatMemory(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

function verdictOf(option: ModelOption) {
  return VERDICT_TEXT[option.estimate.verdict]
}

async function remove(id: string) {
  confirmDelete.value = null
  await models.deleteModel(id)
}
</script>

<template>
  <div class="model-list">
    <ul class="list-group mb-3">
      <li v-for="option in models.options" :key="option.id" class="list-group-item py-3">
        <div class="d-flex flex-wrap align-items-start gap-3">
          <div class="flex-grow-1 model-list__option">
            <div class="d-flex flex-wrap align-items-center gap-2 mb-1">
              <span class="fw-medium">{{ option.label }}</span>
              <span v-if="option.id === models.recommended?.id" class="badge text-bg-primary">Recommended</span>
              <span class="badge" :class="`text-bg-${verdictOf(option).tone}`">{{ verdictOf(option).label }}</span>
              <span v-if="option.installed" class="badge text-bg-light border">Downloaded</span>
            </div>
            <div class="small mb-1">{{ option.description }}</div>
            <model-benchmark :benchmark="option.benchmark" class="mb-1" />
            <div class="small text-secondary">
              Needs about {{ formatMemory(option.estimate.total_bytes) }} of memory;
              {{ formatBytes(option.download_bytes) }} to download.
            </div>
          </div>
          <div class="d-flex gap-2 align-items-center">
            <template v-if="confirmDelete === option.model_id">
              <span class="small">Delete its files?</span>
              <button type="button" class="btn btn-danger btn-sm" @click="remove(option.model_id)">Delete</button>
              <button type="button" class="btn btn-outline-secondary btn-sm" @click="confirmDelete = null">Keep</button>
            </template>
            <template v-else>
              <button
                v-if="option.installed"
                type="button"
                class="btn btn-link btn-sm text-danger"
                :disabled="models.busy === 'delete'"
                @click="confirmDelete = option.model_id"
              >
                Delete
              </button>
              <span v-if="option.id === models.chosen?.id" class="small text-secondary px-2">Chosen</span>
              <button
                v-else
                type="button"
                class="btn btn-outline-action btn-sm"
                :disabled="option.estimate.verdict === 'too_big'"
                @click="models.choose(option.id)"
              >
                Choose
              </button>
            </template>
          </div>
        </div>
      </li>
    </ul>
    <p v-if="models.installed" class="small text-secondary mb-0">
      Downloaded models use {{ formatBytes(models.installed.disk.models_bytes) }};
      {{ formatBytes(models.installed.disk.free_bytes) }} free on this disk.
    </p>
  </div>
</template>

<style scoped lang="scss">
.model-list__option {
  min-width: 16rem;
}
</style>
