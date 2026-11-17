<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhArrowClockwise from '~icons/ph/arrow-clockwise'
import IPhCpu from '~icons/ph/cpu'
import IPhGraphicsCard from '~icons/ph/graphics-card'
import IPhMemory from '~icons/ph/memory'
import { computed } from 'vue'

import { formatBytes, useHardwareStore } from '@/stores/hardware'

// "This computer" in Settings: the system, memory and graphics the app found,
// which decide the model recommendation.

const hardware = useHardwareStore()

const system = computed(() => hardware.hardware?.system)
const osName = computed(() => {
  const os = system.value?.os
  return os === 'Darwin' ? 'macOS' : os
})
</script>

<template>
  <div class="computer-facts">
    <div v-if="hardware.loading && !hardware.hardware" class="text-secondary">Checking this computer…</div>
    <div v-else-if="hardware.error" class="alert alert-warning mb-0">
      Could not check this computer: {{ hardware.error }}
    </div>
    <div v-else-if="hardware.hardware" class="row g-3 mb-2">
      <div class="col-md-4">
        <div class="computer-facts__fact">
          <app-icon :name="IPhCpu" class="me-2 text-action" size="lg" />
          <div>
            <div class="small text-secondary">System</div>
            <div class="fw-medium">{{ osName }} {{ system?.os_release }}</div>
            <div class="small">{{ system?.machine }}, {{ system?.cpu_physical_cores ?? system?.cpu_count }} cores</div>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="computer-facts__fact">
          <app-icon :name="IPhMemory" class="me-2 text-action" size="lg" />
          <div>
            <div class="small text-secondary">Memory</div>
            <div class="fw-medium">{{ formatBytes(hardware.hardware.memory.total_bytes) }}</div>
            <div class="small">{{ formatBytes(hardware.hardware.memory.available_bytes) }} free now</div>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="computer-facts__fact">
          <app-icon :name="IPhGraphicsCard" class="me-2 text-action" size="lg" />
          <div>
            <div class="small text-secondary">Graphics</div>
            <template v-if="hardware.gpus.length">
              <div v-for="gpu in hardware.gpus" :key="gpu.name">
                <div class="fw-medium">{{ gpu.description }}</div>
                <div class="small">
                  {{ formatBytes(gpu.memory_total_bytes) }} usable by the model
                  <span v-if="system?.unified_memory">(shared with the total memory)</span>
                </div>
              </div>
            </template>
            <div v-else-if="!hardware.hardware.llama_server.found" class="small">
              Not checked yet: llama.cpp is not installed.
            </div>
            <div v-else class="small">No graphics processor found; the model would run on the processor.</div>
          </div>
        </div>
      </div>
    </div>
    <button-icon
      v-if="hardware.hardware || hardware.error"
      variant="link"
      size="sm"
      class="px-0"
      :icon-left="IPhArrowClockwise"
      label="Check again"
      :loading="hardware.loading"
      @click="hardware.load(true)"
    />
  </div>
</template>

<style scoped lang="scss">
.computer-facts__fact {
  display: flex;
  align-items: flex-start;
}
</style>
