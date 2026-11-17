<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhArrowClockwise from '~icons/ph/arrow-clockwise'
import IPhCheckCircle from '~icons/ph/check-circle-fill'
import IPhCircleNotch from '~icons/ph/circle-notch'
import IPhFolderOpen from '~icons/ph/folder-open'
import IPhGauge from '~icons/ph/gauge'
import IPhPlay from '~icons/ph/play-fill'
import IPhStop from '~icons/ph/stop-fill'
import IPhWarningCircle from '~icons/ph/warning-circle-fill'
import { BModal } from 'bootstrap-vue-next'
import { computed, ref } from 'vue'

import { friendlyMessage } from '@/api/schema'
import type { FieldError } from '@/api/types'
import OutputFolder from '@/components/OutputFolder.vue'
import RunProgress from '@/components/RunProgress.vue'
import StepFooter from '@/components/StepFooter.vue'
import StepHeader from '@/components/StepHeader.vue'
import { platform } from '@/platform'
import { useConfigStore } from '@/stores/config'
import { useModelsStore } from '@/stores/models'
import { useRunStore } from '@/stores/run'
import { type StepName, useUiStore } from '@/stores/ui'

const config = useConfigStore()
const models = useModelsStore()
const run = useRunStore()
const ui = useUiStore()

const folder = ref<InstanceType<typeof OutputFolder> | null>(null)
const confirmReplace = ref(false)
const openError = ref<string | null>(null)
const showTechnical = ref(false)

const output = computed(() => config.outputState)
const pdfCount = computed(() => config.pdfs.length)

// Problems that stop a run, with the screen where each one is fixed
function stepFor(error: FieldError): StepName {
  if (error.field.startsWith('inputs') || error.field === 'recursive') return 'documents'
  if (error.field.startsWith('triage.enabled') || error.field.startsWith('triage.pages')) return 'documents'
  if (error.field === 'output_dir') return 'run'
  return 'settings'
}
const problems = computed(() =>
  config.errors
    .filter((e) => stepFor(e) !== 'run')
    .map((e) => ({ step: stepFor(e), message: friendlyMessage(e), field: e.field })),
)
// The app's own model is off: the settings check does not see it, but a run would fail without it
const appModel = computed(() => models.mode === 'app' && models.servers !== null)
const modelOff = computed(() => appModel.value && ['stopped', 'failed'].includes(models.serversState))
const modelStarting = computed(() => appModel.value && models.serversState === 'starting')
const canStartModel = computed(() => models.runtimeInstalled && !!models.chosen && !models.needsDownload)
/** The model is off and only Settings can fix it (llama.cpp or the model is missing): "Start the run" cannot. */
const modelBlocks = computed(() => modelOff.value && !canStartModel.value)
/** "Start the run" loads the model first. */
const loadsModel = computed(() => modelOff.value && canStartModel.value)
const modelError = computed(() =>
  appModel.value && models.serversState === 'failed' ? (models.vision?.error ?? 'The model stopped while loading.') : null,
)
const startBusy = computed(() => run.starting || models.loadingForRun)
const stepTitles: Record<StepName, string> = { documents: 'Documents', settings: 'Settings', run: 'Run' }

const isResume = computed(() => output.value?.state === 'resume')
const isConflict = computed(() => output.value?.state === 'conflict')
const previousDone = computed(() => isResume.value && output.value?.previous_status === 'done')

const startLabel = computed(() => {
  if (isResume.value) return previousDone.value ? 'Run again' : 'Continue the run'
  return 'Start the run'
})

const followedRun = computed(() => run.active)
const showProgress = computed(() => run.running || run.finished || run.events.length > 0)
const outcome = computed(() => (run.finished ? run.result : null))
const sameFolder = computed(
  () => !!followedRun.value && !!config.config?.output_dir && followedRun.value.output_dir === config.config.output_dir,
)
const failedPages = computed(() => run.summary.finished?.failed_pages ?? 0)
const outputs = computed(() => run.summary.finished?.outputs ?? {})

const OUTPUT_TEXT: Record<string, string> = {
  relationships: 'Companies, people and their relationships',
  triage: 'Which pages triage kept',
  triage_times: 'How long each document took to check',
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

async function start(overwrite = false) {
  confirmReplace.value = false
  await config.validateNow()
  if (!config.canStart) return
  if (modelOff.value || modelStarting.value) {
    // Load the model first; once it is up, the run settings point at it and are checked again
    if (modelBlocks.value || !(await models.ensureStarted())) return
    await config.validateNow()
    if (!config.canStart) return
  }
  await run.start(overwrite)
}

async function chooseAnother() {
  run.dismissConflict()
  await folder.value?.chooseAnother()
}

async function openResults() {
  openError.value = null
  const path = followedRun.value?.output_dir
  if (!path) return
  try {
    await platform.openFolder(path)
  } catch (e) {
    openError.value = e instanceof Error ? e.message : String(e)
  }
}

const conflictShown = computed({
  get: () => run.conflict !== null,
  set: (shown: boolean) => {
    if (!shown) run.dismissConflict()
  },
})
</script>

<template>
  <div class="run-step">
    <step-header
      title="Run"
      lead="The run happens on this computer and can take a long time. You can stop it and continue later."
      hide-run-notice
    />

    <section v-if="!run.running" class="card mb-4">
      <div class="card-body p-4">
        <output-folder ref="folder" class="mb-3" />

        <div v-if="output && config.config?.output_dir" class="output-state mb-3">
          <div v-if="output.state === 'new'" class="alert alert-info py-2 mb-0">{{ output.message }}</div>
          <div v-else-if="output.state === 'resume'" class="alert alert-success py-2 mb-0">
            <template v-if="previousDone">
              This folder already holds finished results with these settings. Running again only reads pages that
              are missing or failed.
            </template>
            <template v-else>
              This folder holds a run with the same settings that did not finish. It will continue where it
              stopped.
            </template>
          </div>
          <div v-else-if="output.state === 'conflict'" class="alert alert-warning mb-0">
            <p class="mb-2">
              This folder holds results from a run with <strong>different settings</strong>. Choose another
              folder, or replace those results.
            </p>
            <div class="d-flex flex-wrap gap-2">
              <button-icon variant="action" :icon-left="IPhFolderOpen" label="Choose another folder" @click="chooseAnother" />
              <button type="button" class="btn btn-outline-danger" @click="confirmReplace = true">
                Replace the earlier results…
              </button>
            </div>
          </div>
          <div v-else-if="output.state === 'invalid'" class="alert alert-danger py-2 mb-0">{{ output.message }}</div>
        </div>

        <div v-if="problems.length || modelBlocks" class="alert alert-danger">
          <p class="mb-2 fw-medium">Fix these first:</p>
          <ul class="mb-0">
            <li v-if="modelBlocks">
              The model is not ready: finish setting it up.
              <button type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="ui.go('settings')">
                Go to Settings
              </button>
            </li>
            <li v-for="problem in problems" :key="problem.field + problem.message">
              {{ problem.message }}
              <button type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="ui.go(problem.step)">
                Go to {{ stepTitles[problem.step] }}
              </button>
            </li>
          </ul>
        </div>
        <div v-if="config.validationError" class="alert alert-danger py-2">
          The settings could not be checked: {{ config.validationError }}
        </div>
        <div v-if="run.error" class="alert alert-danger py-2">{{ run.error.message }}</div>
        <div v-if="loadsModel && (modelError || models.actionError)" class="alert alert-danger py-2">
          The model could not start: {{ modelError ?? models.actionError }}
          <button type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="ui.go('settings')">
            Go to Settings
          </button>
        </div>

        <p class="small text-secondary d-flex gap-2 mb-3">
          <app-icon :name="IPhGauge" class="flex-shrink-0 mt-1" />
          <span>
            While the run lasts, the computer is busy and other apps may slow down. Keep it plugged in, and keep a
            laptop’s lid open: the app keeps the computer awake, but closing the lid still puts it to sleep.
          </span>
        </p>

        <div class="d-flex align-items-center gap-3 flex-wrap">
          <button-icon
            v-if="!isConflict"
            variant="action"
            size="lg"
            :icon-left="IPhPlay"
            :label="startLabel"
            :loading="startBusy"
            :disabled="!config.canStart || startBusy || modelBlocks"
            @click="start()"
          />
          <span v-if="modelStarting" class="text-secondary small d-flex align-items-center gap-2">
            <app-icon :name="IPhCircleNotch" spin class="text-action" />
            Loading the model into memory. This can take a minute.
            <template v-if="models.loadingForRun">The run starts once it is ready.</template>
          </span>
          <span v-else-if="loadsModel && config.canStart && !modelError" class="text-secondary small">
            The model is not running yet: it loads first, which can take a minute.
          </span>
          <span v-else-if="pdfCount && config.canStart && !modelOff" class="text-secondary small">
            {{ pdfCount }} {{ pdfCount === 1 ? 'document' : 'documents' }} with {{ config.config?.server.model }}
          </span>
          <span v-else-if="config.validating" class="text-secondary small">Checking the settings…</span>
          <span v-else-if="!config.config?.output_dir" class="text-secondary small">
            Choose where to save the results first.
          </span>
        </div>
      </div>
    </section>

    <section v-if="showProgress" class="card mb-4">
      <div class="card-body p-4">
        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
          <h2 class="h5 mb-0">
            <template v-if="run.status.state === 'running'">Running</template>
            <template v-else-if="run.status.state === 'cancelling'">Stopping…</template>
            <template v-else-if="outcome === 'done'">Finished</template>
            <template v-else-if="outcome === 'cancelled'">Stopped</template>
            <template v-else-if="outcome === 'failed'">The run failed</template>
            <template v-else>Last run</template>
          </h2>
          <button-icon
            v-if="run.running"
            variant="outline-danger"
            :icon-left="IPhStop"
            :label="run.status.state === 'cancelling' ? 'Stopping…' : 'Stop'"
            :loading="run.cancelling || run.status.state === 'cancelling'"
            :disabled="run.status.state === 'cancelling'"
            @click="run.cancel()"
          />
        </div>
        <p v-if="followedRun" class="small text-secondary mb-2 text-break-all">
          Results folder: <span class="font-monospace">{{ followedRun.output_dir }}</span>
        </p>
        <p v-if="run.running" class="small text-secondary">
          Stopping is safe: pages already read are kept, and starting again with the same settings and folder
          continues from there.
        </p>

        <div v-if="run.connection === 'reconnecting'" class="alert alert-warning py-2">
          Reconnecting to the background service…
        </div>
        <div v-else-if="run.connection === 'lost'" class="alert alert-danger py-2 d-flex align-items-center gap-3">
          <span class="flex-grow-1">Lost contact with the app’s background service.</span>
          <button-icon variant="outline-danger" size="sm" :icon-left="IPhArrowClockwise" label="Try again" @click="run.init()" />
        </div>

        <div v-if="outcome === 'done'" class="alert alert-success d-flex gap-3 align-items-start">
          <app-icon :name="IPhCheckCircle" size="xl" class="text-success flex-shrink-0" />
          <div class="flex-grow-1">
            <p class="mb-2 fw-medium">
              Done.
              <template v-if="failedPages">
                {{ failedPages }} {{ failedPages === 1 ? 'page' : 'pages' }} could not be read; run again with
                the same settings and folder to retry {{ failedPages === 1 ? 'it' : 'them' }}.
              </template>
            </p>
            <ul class="list-unstyled small mb-3">
              <li v-for="(path, key) in outputs" :key="key">
                <span class="font-monospace">{{ fileName(path) }}</span>
                <span class="text-secondary"> — {{ OUTPUT_TEXT[key] ?? key }}</span>
              </li>
            </ul>
            <button-icon
              v-if="platform.hasNativeFiles"
              variant="success"
              :icon-left="IPhFolderOpen"
              label="Open results folder"
              @click="openResults"
            />
            <div v-if="openError" class="small text-danger mt-2">{{ openError }}</div>
          </div>
        </div>

        <div v-else-if="outcome === 'cancelled'" class="alert alert-secondary">
          <p class="mb-2">
            The run was stopped. The pages read so far are saved.
            <template v-if="sameFolder && isResume">Use “Continue the run” above to pick up where it stopped.</template>
            <template v-else>Start again with the same settings and folder to continue where it stopped.</template>
          </p>
          <button-icon
            v-if="platform.hasNativeFiles"
            variant="link"
            :icon-left="IPhFolderOpen"
            label="Open results folder"
            @click="openResults"
          />
        </div>

        <div v-else-if="outcome === 'failed'" class="alert alert-danger">
          <div class="d-flex gap-3 align-items-start">
            <app-icon :name="IPhWarningCircle" size="xl" class="flex-shrink-0" />
            <div class="flex-grow-1">
              <p class="mb-1 fw-medium">
                {{ followedRun?.progress.last_error || 'The run stopped because of an error.' }}
              </p>
              <p class="small mb-2">
                Check that the model server is running, then start again: pages already read are kept.
              </p>
              <button type="button" class="btn btn-link btn-sm px-0" @click="showTechnical = !showTechnical">
                {{ showTechnical ? 'Hide' : 'Show' }} technical details
              </button>
              <pre v-if="showTechnical" class="run-step__stderr small border rounded p-2 mt-2 mb-0">Exit code: {{ followedRun?.exit_code }}
{{ (followedRun?.stderr ?? []).join('\n') || 'No error output.' }}</pre>
            </div>
          </div>
        </div>

        <run-progress />
      </div>
    </section>

    <b-modal v-model="conflictShown" title="This folder holds other results" no-footer>
      <p>
        The folder holds results from a run with different settings. Choose another folder, or replace those results.
      </p>
      <div class="d-flex flex-wrap gap-2">
        <button-icon variant="action" :icon-left="IPhFolderOpen" label="Choose another folder" @click="chooseAnother" />
        <button type="button" class="btn btn-outline-danger" @click="run.dismissConflict(); confirmReplace = true">
          Replace the earlier results…
        </button>
      </div>
    </b-modal>

    <b-modal v-model="confirmReplace" title="Replace the earlier results?" no-footer>
      <p>
        The earlier results in
        <span class="font-monospace text-break-all">{{ config.config?.output_dir }}</span>
        will be deleted, and a new run will start there. Your documents are not touched.
      </p>
      <div class="d-flex justify-content-end gap-2">
        <button type="button" class="btn btn-outline-secondary" @click="confirmReplace = false">Cancel</button>
        <button type="button" class="btn btn-danger" @click="start(true)">Delete them and start</button>
      </div>
    </b-modal>

    <step-footer />
  </div>
</template>

<style scoped>
.run-step__stderr {
  max-height: 16rem;
  overflow: auto;
  white-space: pre-wrap;
  background: var(--bs-body-bg);
}
</style>
