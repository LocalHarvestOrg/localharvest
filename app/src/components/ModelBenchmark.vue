<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhQuestion from '~icons/ph/question'
import { computed } from 'vue'

import type { ModelBenchmark, StageScore } from '@/api/types'

// A curated model's scores on ICIJ's benchmark documents: precision, recall and
// F1 per stage, and the time per page on the GPU it was measured on.

const props = defineProps<{ benchmark: ModelBenchmark | null }>()

// Column headers, each explained in a tooltip for readers new to the terms.
const METRICS = [
  { label: 'Precision', help: 'Of what the model found, the share that is right', numeric: true },
  { label: 'Recall', help: 'Of what there is to find, the share the model found', numeric: true },
  { label: 'F1', help: 'Precision and recall combined', numeric: true },
  {
    label: 'Speed',
    help:
      'Measured during benchmarking with default settings on a dedicated server GPU, not on this computer. ' +
      'Your speed depends on your hardware.',
    numeric: false,
  },
]

const stages = computed(() => {
  const benchmark = props.benchmark
  if (!benchmark) return []
  const rows: { label: string; score: StageScore }[] = []
  if (benchmark.triage) rows.push({ label: 'Finding pages with tables', score: benchmark.triage })
  if (benchmark.extraction) rows.push({ label: 'Extracting relationships', score: benchmark.extraction })
  return rows
})

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}
</script>

<template>
  <div v-if="stages.length" class="model-benchmark small">
    <div class="text-secondary mb-1">Tested on ICIJ documents:</div>
    <table class="table table-sm table-borderless mb-0 w-auto">
      <thead>
        <tr class="text-secondary">
          <th scope="col" class="fw-normal ps-0"></th>
          <th
            v-for="metric in METRICS"
            :key="metric.label"
            scope="col"
            class="fw-normal"
            :class="{ 'text-end': metric.numeric }"
          >
            <span v-b-tooltip.hover.focus.top :title="metric.help" tabindex="0" class="model-benchmark__term">
              {{ metric.label }}<app-icon :name="IPhQuestion" class="ms-1" />
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="stage in stages" :key="stage.label">
          <th scope="row" class="fw-normal ps-0">{{ stage.label }}</th>
          <td class="text-end">{{ percent(stage.score.precision) }}</td>
          <td class="text-end">{{ percent(stage.score.recall) }}</td>
          <td class="text-end fw-medium">{{ percent(stage.score.f1) }}</td>
          <td>
            <template v-if="stage.score.seconds_per_page !== null">
              {{ stage.score.seconds_per_page.toFixed(1) }} s per page
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-else class="small text-secondary">Not tested on ICIJ documents yet.</div>
</template>

<style scoped lang="scss">
.model-benchmark .table {
  --bs-table-bg: transparent;

  th,
  td {
    padding-top: 0.1rem;
    padding-bottom: 0.1rem;
    padding-right: 1rem;
  }
}

.model-benchmark__term {
  cursor: help;
  white-space: nowrap;
  text-decoration: underline dotted;
  text-underline-offset: 0.2em;

  :deep(svg) {
    opacity: 0.6;
  }
}
</style>
