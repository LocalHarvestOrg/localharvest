<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhArrowClockwise from '~icons/ph/arrow-clockwise'
import IPhCheckCircle from '~icons/ph/check-circle-fill'
import IPhCircleNotch from '~icons/ph/circle-notch'
import IPhDownloadSimple from '~icons/ph/download-simple'
import IPhPlay from '~icons/ph/play'
import IPhSparkle from '~icons/ph/sparkle'
import IPhStop from '~icons/ph/stop'
import IPhX from '~icons/ph/x'
import { computed, ref } from 'vue'

import CapabilityWarning from '@/components/CapabilityWarning.vue'
import JobProgress from '@/components/JobProgress.vue'
import TextField from '@/components/TextField.vue'
import { formatBytes } from '@/stores/hardware'
import { isActive, useModelsStore, VERDICT_TEXT } from '@/stores/models'

// The card at the top of Settings: the model, whether it fits this computer, and
// the one thing to do now. Until the model can run, it walks through the one-time
// setup: install llama.cpp, download the model, start it.

const models = useModelsStore()

const showDownloads = ref(false)
const showLog = ref(false)

const runtimeVariant = computed(() => {
  const runtime = models.runtime
  return runtime?.variants.find((v) => v.id === runtime.recommended_variant) ?? null
})
const runtimeJob = computed(() => models.runtimeJob)
const installing = computed(() => isActive(runtimeJob.value))

const chosen = computed(() => models.chosen)
const verdict = computed(() => (chosen.value ? VERDICT_TEXT[chosen.value.estimate.verdict] : null))
const isRecommendation = computed(() => !!chosen.value && chosen.value.id === models.recommended?.id)
const downloadBytes = computed(() =>
  models.downloads.filter((d) => !d.model.installed).reduce((sum, d) => sum + d.model.download_bytes, 0),
)
const paused = computed(() => models.downloads.some((d) => !d.model.installed && d.job?.state === 'cancelled'))
const visibleJobs = computed(() => models.downloads.filter((d) => d.job && (!d.model.installed || isActive(d.job))))

/** The one-time setup is not finished: the card shows its three steps. */
const settingUp = computed(
  () => !models.runtimeInstalled || installing.value || models.needsDownload || models.downloading,
)
const setupSteps = computed(() => [
  { title: 'Install the program that runs the model', done: models.runtimeInstalled && !installing.value },
  { title: 'Download the model', done: !models.needsDownload && !models.downloading },
  { title: 'Load the model', done: false },
])

/** The model the servers run when it is not the chosen one, by name. */
const otherRunning = computed(() => {
  const vision = models.vision
  if (models.runningChosen || !vision || !['starting', 'ready'].includes(models.serversState)) return null
  return models.options.find((o) => o.model_id === vision.model_id)?.label ?? vision.alias
})
const startError = computed(() => {
  const vision = models.vision
  return vision?.state === 'failed' ? (vision.error ?? 'The model stopped while loading.') : null
})
const log = computed(() => models.vision?.log.join('\n') ?? '')

/** Memory needs to a tenth of a GB, as the model descriptions give them. */
function formatMemory(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}
</script>

<template>
  <div class="model-setup">
    <div v-if="models.loadError" class="alert alert-warning d-flex align-items-center gap-2">
      <span class="flex-grow-1">Could not check the models: {{ models.loadError }}</span>
      <button-icon variant="outline-secondary" size="sm" :icon-left="IPhArrowClockwise" label="Try again" @click="models.load()" />
    </div>
    <div v-if="models.actionError" class="alert alert-danger d-flex align-items-start gap-2">
      <span class="flex-grow-1">{{ models.actionError }}</span>
      <button type="button" class="btn-close" aria-label="Dismiss" @click="models.actionError = null" />
    </div>

    <capability-warning />

    <!-- The app runs the model; with no model that fits, the warning above says so instead -->
    <section
      v-if="models.mode === 'app' && models.recommendation && (chosen || models.capability?.level !== 'cannot')"
      class="card mb-4 model-setup__card"
      :class="{ 'model-setup__card--setup': settingUp }"
    >
      <div class="card-body p-4">
        <div class="small text-secondary mb-1">
          <app-icon :name="IPhSparkle" class="me-1 text-primary" />
          {{ isRecommendation || !chosen ? 'The model recommended for this computer' : 'The model you chose' }}
        </div>

        <p v-if="!chosen" class="mb-0">{{ models.recommendation.reason }}</p>

        <template v-else>
          <div class="d-flex flex-wrap align-items-center gap-2 mb-1">
            <h2 class="h5 mb-0">{{ chosen.label }}</h2>
            <span class="badge" :class="`text-bg-${verdict!.tone}`">{{ verdict!.label }}</span>
          </div>
          <p class="small text-secondary mb-3">
            {{ verdict!.text }}
            <template v-if="isRecommendation && chosen.estimate.verdict !== 'gpu' && !models.capability">{{ models.recommendation.reason }}</template>
            Needs about {{ formatMemory(chosen.estimate.total_bytes) }} of memory.
            <button
              v-if="!isRecommendation && models.recommended"
              type="button"
              class="btn btn-link btn-sm p-0 align-baseline"
              @click="models.choose(null)"
            >
              Back to the recommendation ({{ models.recommended.label }})
            </button>
          </p>

          <p v-if="otherRunning" class="small text-secondary mb-3">
            The app is still running {{ otherRunning }}.
            <button type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="models.stopServers()">
              Stop it
            </button>
          </p>

          <!-- The one-time setup -->
          <template v-if="settingUp">
            <p class="mb-3">
              <span class="fw-medium">Before the first run, the app needs a one-time setup.</span>
              It downloads what reads the pages, then works offline.
            </p>
            <ol class="model-setup__steps list-unstyled mb-3">
              <li
                v-for="(item, index) in setupSteps"
                :key="item.title"
                class="model-setup__step"
                :class="{ 'model-setup__step--done': item.done }"
              >
                <span class="model-setup__number">
                  <app-icon v-if="item.done" :name="IPhCheckCircle" class="text-success" size="lg" />
                  <template v-else>{{ index + 1 }}</template>
                </span>
                <div class="flex-grow-1 min-width-0">
                  <div class="fw-medium">{{ item.title }}</div>

                  <!-- 1. llama.cpp -->
                  <template v-if="index === 0 && !item.done">
                    <job-progress v-if="runtimeJob" :job="runtimeJob" label="The program" class="mt-2" />
                    <div class="d-flex flex-wrap align-items-center gap-2 mt-2">
                      <button-icon
                        v-if="installing"
                        variant="outline-secondary"
                        size="sm"
                        :icon-left="IPhX"
                        label="Cancel"
                        :loading="models.busy === 'cancel'"
                        @click="models.cancelJob(runtimeJob!.id)"
                      />
                      <button-icon
                        v-else
                        variant="action"
                        :icon-left="IPhDownloadSimple"
                        :label="runtimeJob?.state === 'failed' || runtimeJob?.state === 'cancelled'
                          ? 'Try again'
                          : `Install${runtimeVariant ? ` (${formatBytes(runtimeVariant.download_bytes)})` : ''}`"
                        :loading="models.busy === 'install'"
                        :disabled="models.offline"
                        @click="models.installRuntime()"
                      />
                    </div>
                  </template>

                  <!-- 2. The model files -->
                  <template v-else-if="index === 1 && !item.done">
                    <job-progress v-for="d in visibleJobs" :key="d.model.id" :job="d.job!" :label="d.model.label" class="mt-2" />
                    <div class="d-flex flex-wrap align-items-center gap-2 mt-2">
                      <button-icon
                        v-if="models.downloading"
                        variant="outline-secondary"
                        size="sm"
                        :icon-left="IPhX"
                        label="Cancel download"
                        :loading="models.busy === 'cancel'"
                        @click="models.cancelDownloads()"
                      />
                      <button-icon
                        v-else
                        :variant="models.runtimeInstalled || installing ? 'action' : 'outline-action'"
                        :icon-left="IPhDownloadSimple"
                        :label="`${paused ? 'Resume download' : 'Download'} (${formatBytes(downloadBytes)})`"
                        :loading="models.busy === 'download'"
                        :disabled="models.offline"
                        @click="models.download()"
                      />
                      <span v-if="models.downloading" class="small text-secondary">
                        You can cancel and resume later; nothing downloaded so far is lost.
                      </span>
                    </div>
                  </template>

                  <div v-else-if="index === 2" class="small text-secondary">
                    Once the first two are done. It then stays ready for your runs.
                  </div>
                </div>
              </li>
            </ol>

            <div v-if="models.offline" class="alert alert-warning py-2 d-flex flex-wrap align-items-center gap-2">
              <span class="flex-grow-1">“Work offline” is on, so nothing can be downloaded.</span>
              <button
                type="button"
                class="btn btn-sm btn-outline-secondary"
                :disabled="models.busy === 'offline'"
                @click="models.setOffline(false)"
              >
                Allow downloads
              </button>
            </div>

            <button
              type="button"
              class="btn btn-link btn-sm p-0"
              :aria-expanded="showDownloads"
              @click="showDownloads = !showDownloads"
            >
              What gets downloaded?
            </button>
            <div v-if="showDownloads" class="small text-secondary mt-2 model-setup__downloads">
              <p class="mb-1">
                <span class="fw-medium text-body">llama.cpp</span>, the free program that runs the model on this
                computer, from github.com<template v-if="runtimeVariant"> ({{ formatBytes(runtimeVariant.download_bytes) }}, {{ runtimeVariant.label }})</template>.
              </p>
              <p class="mb-1">
                <span class="fw-medium text-body">{{ chosen.label }}</span>, from huggingface.co ({{ formatBytes(chosen.download_bytes) }}).
              </p>
              <p class="mb-0">
                Downloaded once and stay on this computer. Your documents are never sent anywhere.
              </p>
            </div>
          </template>

          <!-- Set up: start, starting, ready -->
          <template v-else-if="models.serversState === 'starting' && models.runningChosen">
            <div class="d-flex align-items-center gap-2">
              <app-icon :name="IPhCircleNotch" spin class="text-action" />
              <span>Loading the model into memory. This can take a minute.</span>
            </div>
          </template>
          <template v-else-if="models.serversState === 'ready' && models.runningChosen">
            <div class="d-flex flex-wrap align-items-center gap-3">
              <div class="d-flex align-items-center gap-2 flex-grow-1">
                <app-icon :name="IPhCheckCircle" class="text-success" size="lg" />
                <span>Ready. The model is running on this computer, and runs will use it.</span>
              </div>
              <button-icon
                variant="outline-secondary"
                size="sm"
                :icon-left="IPhStop"
                label="Stop the model"
                :loading="models.busy === 'stop'"
                @click="models.stopServers()"
              />
            </div>
          </template>
          <template v-else>
            <div v-if="startError && models.runningChosen" class="alert alert-danger py-2">
              The model could not start: {{ startError }}
              <button v-if="log" type="button" class="btn btn-link btn-sm p-0 ms-1" @click="showLog = !showLog">
                {{ showLog ? 'Hide details' : 'Show details' }}
              </button>
              <pre v-if="showLog" class="small mt-2 mb-0 text-pre-wrap">{{ log }}</pre>
            </div>
            <div class="d-flex flex-wrap align-items-center gap-2">
              <button-icon
                variant="action"
                :icon-left="IPhPlay"
                :label="models.serversState === 'stopped' || models.runningChosen ? 'Load the model' : 'Switch to this model'"
                :loading="models.busy === 'start'"
                @click="models.startServers()"
              />
              <span class="small text-secondary">Downloaded. Start it to read documents.</span>
            </div>
          </template>
        </template>
      </div>
    </section>

    <!-- The user's own server -->
    <section v-if="models.mode === 'own'" class="card mb-4 model-setup__card">
      <div class="card-body p-4">
        <div class="d-flex flex-wrap align-items-start justify-content-between gap-2 mb-1">
          <h2 class="h5 mb-0">Your own model server</h2>
          <button type="button" class="btn btn-link btn-sm p-0" @click="models.setMode('app')">
            Let the app run the model instead
          </button>
        </div>
        <p class="text-secondary small mb-3">
          A llama.cpp or vLLM server already running on this computer. Only addresses on this computer are
          accepted, so page images never leave it.
        </p>
        <text-field
          path="server.url"
          errors-from="server"
          label="Server address"
          placeholder="http://localhost:8000/v1/chat/completions"
          monospace
        />
        <text-field
          path="server.model"
          label="Model name"
          help="The name the server knows the model by."
          monospace
        />
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.model-setup {
  &__card {
    border-color: var(--bs-primary-border-subtle);

    &--setup {
      border-width: 2px;
      border-color: var(--bs-primary);
    }
  }

  &__steps {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  &__step {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;

    &--done {
      color: var(--bs-secondary-color);
    }
  }

  &__number {
    width: 1.75rem;
    height: 1.75rem;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--bs-border-color);
    font-weight: 600;
    font-size: 0.875rem;
    flex-shrink: 0;
  }

  &__step--done &__number {
    border-color: transparent;
  }

  &__downloads {
    max-width: 40rem;
  }
}

.min-width-0 {
  min-width: 0;
}
</style>
