<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhCheckCircle from '~icons/ph/check-circle-fill'
import IPhCircle from '~icons/ph/circle'
import IPhCircleNotch from '~icons/ph/circle-notch'
import IPhMinusCircle from '~icons/ph/minus-circle'
import IPhPauseCircle from '~icons/ph/pause-circle'
import { computed, ref } from 'vue'

import { formatDuration, plural, STAGE_TEXT, STAGES, type StageProgress } from '@/stores/progress'
import { useRunStore } from '@/stores/run'

const run = useRunStore()
const showLog = ref(false)
const showAllNotices = ref(false)

const stages = computed(() => STAGES.map((name) => run.summary.stages[name]))
const notices = computed(() => run.summary.notices)
const shownNotices = computed(() => (showAllNotices.value ? notices.value : notices.value.slice(-3)))
const log = computed(() =>
  run.events
    .filter((e) => e.message)
    .slice(-300)
    .map((e) => `${new Date(e.time).toLocaleTimeString()}  ${e.message}`)
    .join('\n'),
)

function percent(stage: StageProgress): number {
  if (stage.state === 'done') return 100
  if (!stage.total) return 0
  return Math.min(100, Math.round((stage.done / stage.total) * 100))
}

function counts(stage: StageProgress): string {
  const unit = STAGE_TEXT[stage.name].unit
  if (stage.state === 'skipped') return 'Turned off'
  if (stage.state === 'waiting') return 'Waiting'
  if (stage.total === null) return 'Starting…'
  if (stage.total === 0) return "No pages left to read"
  return `${stage.done} of ${plural(stage.total, unit)}`
}

const icons = {
  waiting: IPhCircle,
  running: IPhCircleNotch,
  done: IPhCheckCircle,
  skipped: IPhMinusCircle,
  stopped: IPhPauseCircle,
}
</script>

<template>
  <div class="run-progress">
    <ol class="list-unstyled mb-0">
      <li
        v-for="stage in stages"
        :key="stage.name"
        class="run-progress__stage d-flex gap-3 py-3"
        :class="`run-progress__stage--${stage.state}`"
      >
        <app-icon
          :name="icons[stage.state]"
          :spin="stage.state === 'running'"
          size="lg"
          class="flex-shrink-0 mt-1 run-progress__icon"
        />
        <div class="flex-grow-1">
          <div class="d-flex flex-wrap justify-content-between gap-2">
            <span class="fw-medium">{{ STAGE_TEXT[stage.name].title }}</span>
            <span class="small text-secondary">{{ counts(stage) }}</span>
          </div>
          <div class="small text-secondary mb-2">{{ STAGE_TEXT[stage.name].description }}</div>
          <div
            v-if="stage.state === 'running' || stage.state === 'done' || stage.state === 'stopped'"
            class="progress"
            role="progressbar"
            :aria-label="STAGE_TEXT[stage.name].title"
            :aria-valuenow="percent(stage)"
            aria-valuemin="0"
            aria-valuemax="100"
            style="height: 0.5rem"
          >
            <div
              class="progress-bar"
              :class="{
                'bg-success': stage.state === 'done',
                'bg-secondary': stage.state === 'stopped',
                'progress-bar-striped progress-bar-animated': stage.state === 'running' && !stage.total,
              }"
              :style="{ width: `${stage.state === 'running' && !stage.total ? 100 : percent(stage)}%` }"
            />
          </div>
          <div v-if="stage.state === 'running' && stage.remaining !== null" class="small text-secondary mt-1">
            About {{ formatDuration(stage.remaining) }} left in this step
          </div>
        </div>
      </li>
    </ol>

    <div v-if="notices.length" class="mt-3">
      <div
        v-for="(notice, index) in shownNotices"
        :key="index"
        class="alert py-2 mb-2 small"
        :class="notice.kind === 'error' ? 'alert-danger' : 'alert-warning'"
      >
        <span v-if="notice.file" class="fw-medium">
          {{ notice.file }}<template v-if="notice.page">, page {{ notice.page }}</template>:
        </span>
        <span class="text-pre-wrap">{{ notice.message }}</span>
      </div>
      <button
        v-if="notices.length > 3"
        type="button"
        class="btn btn-link btn-sm px-0"
        @click="showAllNotices = !showAllNotices"
      >
        {{ showAllNotices ? 'Show fewer messages' : `Show all ${notices.length} messages` }}
      </button>
    </div>

    <div class="mt-3">
      <button type="button" class="btn btn-link btn-sm px-0" :aria-expanded="showLog" @click="showLog = !showLog">
        {{ showLog ? 'Hide' : 'Show' }} details
      </button>
      <pre v-if="showLog" class="run-progress__log border rounded p-3 mt-2 mb-0 small">{{ log || 'Nothing yet.' }}</pre>
    </div>
  </div>
</template>

<style scoped lang="scss">
.run-progress__stage + .run-progress__stage {
  border-top: 1px solid var(--bs-border-color-translucent);
}

.run-progress__stage--waiting,
.run-progress__stage--skipped {
  opacity: 0.6;
}

.run-progress__stage--running .run-progress__icon {
  color: var(--bs-action);
}

.run-progress__stage--done .run-progress__icon {
  color: var(--bs-success);
}

.run-progress__log {
  max-height: 20rem;
  overflow: auto;
  white-space: pre-wrap;
  background: var(--bs-tertiary-bg);
}

.text-pre-wrap {
  white-space: pre-wrap;
}
</style>
