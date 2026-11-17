<script setup lang="ts">
import { computed } from 'vue'

import type { Job } from '@/api/types'
import { formatBytes } from '@/stores/hardware'
import { isActive } from '@/stores/models'

// One download or llama.cpp install: a progress bar, then what became of it.

const props = defineProps<{ job: Job; label: string }>()

const percent = computed(() => {
  if (props.job.state === 'done') return 100
  if (!props.job.bytes_total) return 0
  return Math.min(100, Math.round((props.job.bytes_done / props.job.bytes_total) * 100))
})

const detail = computed(() => {
  const job = props.job
  switch (job.state) {
    case 'queued':
      return 'Waiting to start…'
    case 'verifying':
      return 'Checking the downloaded files…'
    case 'installing':
      return 'Installing…'
    case 'done':
      return 'Done'
    case 'cancelled':
      return `Paused at ${formatBytes(job.bytes_done)} of ${formatBytes(job.bytes_total)}`
    case 'failed':
      return 'Failed'
    default: {
      const speed = job.bytes_per_second ? `, ${formatBytes(job.bytes_per_second)}/s` : ''
      return `${formatBytes(job.bytes_done)} of ${formatBytes(job.bytes_total)}${speed}`
    }
  }
})
</script>

<template>
  <div class="job-progress mb-3">
    <div class="d-flex flex-wrap justify-content-between gap-2 small mb-1">
      <span class="fw-medium">{{ label }}</span>
      <span class="text-secondary">{{ detail }}</span>
    </div>
    <div
      class="progress"
      role="progressbar"
      :aria-label="label"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
      style="height: 0.5rem"
    >
      <div
        class="progress-bar"
        :class="{
          'bg-success': job.state === 'done',
          'bg-secondary': job.state === 'cancelled',
          'bg-danger': job.state === 'failed',
          'progress-bar-striped progress-bar-animated': isActive(job) && job.state !== 'downloading',
        }"
        :style="{ width: `${isActive(job) && job.state !== 'downloading' ? 100 : percent}%` }"
      />
    </div>
    <div v-if="job.state === 'failed' && job.error" class="small text-danger mt-1">{{ job.error }}</div>
  </div>
</template>
