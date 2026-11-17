<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhArrowClockwise from '~icons/ph/arrow-clockwise'
import IPhCheck from '~icons/ph/check-bold'
import IPhCircleNotch from '~icons/ph/circle-notch'
import IPhCloudArrowDown from '~icons/ph/cloud-arrow-down'
import IPhShieldCheck from '~icons/ph/shield-check'
import IPhWarning from '~icons/ph/warning-fill'
import IPhWifiSlash from '~icons/ph/wifi-slash'
import { BApp } from 'bootstrap-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

import { APP_NAME } from '@/app'
import AboutDialog from '@/components/AboutDialog.vue'
import { platform } from '@/platform'
import { useConfigStore } from '@/stores/config'
import { useModelsStore } from '@/stores/models'
import { useRunStore } from '@/stores/run'
import { useSidecarStore } from '@/stores/sidecar'
import { STEPS, type StepName, useUiStore } from '@/stores/ui'
import DocumentsStep from '@/steps/DocumentsStep.vue'
import RunStep from '@/steps/RunStep.vue'
import SettingsStep from '@/steps/SettingsStep.vue'
import WelcomeStep from '@/steps/WelcomeStep.vue'

const sidecar = useSidecarStore()
const config = useConfigStore()
const run = useRunStore()
const models = useModelsStore()
const ui = useUiStore()

const dragging = ref(false)
const aboutShown = ref(false)
const offlineId = useId()
let stopDrop: (() => void) | null = null

const screens = { documents: DocumentsStep, settings: SettingsStep, run: RunStep }
const loaded = computed(() => sidecar.ready && config.config !== null)

async function start() {
  if (!(await sidecar.connect())) return
  await config.load()
  if (config.loadError) return
  await run.init()
  void models.load() // for the Settings step's mark and the model light; Settings loads it again when shown
  if (run.running || (config.restored && config.config?.inputs.length)) ui.go('run')
}

onMounted(async () => {
  stopDrop = await platform.onDrop((event) => {
    if (event.type === 'enter') dragging.value = true
    else if (event.type === 'leave') dragging.value = false
    else if (event.type === 'drop') {
      dragging.value = false
      config.addInputs(event.paths)
      ui.go('documents')
    }
  })
  await start()
})

watch(
  () => [ui.step, ui.welcome],
  () => window.scrollTo({ top: 0 }),
)

onBeforeUnmount(() => {
  stopDrop?.()
  run.stopFollowing()
})

const isUnder = (field: string, prefix: string) =>
  field === prefix || field.startsWith(`${prefix}.`) || field.startsWith(`${prefix}[`)

type StepMark = 'done' | 'problem' | 'busy' | null

const marks = computed<Record<StepName, StepMark>>(() => {
  const errors = config.errors
  const has = (prefixes: string[]) => errors.some((e) => prefixes.some((p) => isUnder(e.field, p)))
  const inputs = config.config?.inputs.length ?? 0
  const onDocuments = ['inputs', 'recursive', 'triage.enabled', 'triage.pages']
  const settingsErrors = errors.some((e) => ![...onDocuments, 'output_dir'].some((p) => isUnder(e.field, p)))
  return {
    documents: inputs === 0 ? null : has(onDocuments) ? 'problem' : 'done',
    // Settings holds the model: done once it runs, busy while it downloads
    // A computer that cannot run the model is a problem too, shown at the top of Settings
    settings: settingsErrors || (models.mode === 'app' && models.capability?.level === 'cannot')
      ? 'problem'
      : models.ready ? 'done' : models.downloading ? 'busy' : null,
    run: run.running ? 'busy' : run.result === 'done' ? 'done' : run.result === 'failed' ? 'problem' : null,
  }
})

/** The app's model server, on or off; not shown for the user's own server, which the app does not watch. */
const modelLight = computed(() => {
  const state = models.serversState
  const name = models.options.find((o) => o.model_id === models.vision?.model_id)?.label
  if (state === 'ready') return { tone: 'on', text: 'Model on', name }
  if (state === 'starting') return { tone: 'starting', text: 'Model starting…', name }
  if (state === 'failed') return { tone: 'failed', text: 'Model stopped with an error', name: undefined }
  // Off is the normal state until the model is started, not a problem
  return { tone: 'off', text: 'Model off', name: undefined }
})
</script>

<template>
  <b-app>
    <div class="app" :class="{ 'app--dragging': dragging }">
      <aside class="app-sidebar">
        <div class="app-sidebar__brand">
          <div class="app-sidebar__name">
            {{ APP_NAME }}
            <span class="app-sidebar__beta">beta</span>
          </div>
          <div class="app-sidebar__tagline">Tables from PDF documents</div>
        </div>

        <nav v-if="loaded" class="app-sidebar__nav" aria-label="Steps">
          <button
            v-for="(item, index) in STEPS"
            :key="item.name"
            type="button"
            class="app-sidebar__step"
            :class="{ 'app-sidebar__step--active': !ui.welcome && ui.step === item.name }"
            :aria-current="!ui.welcome && ui.step === item.name ? 'step' : undefined"
            @click="ui.go(item.name)"
          >
            <span class="app-sidebar__number">{{ index + 1 }}</span>
            <span class="app-sidebar__text">
              <span class="app-sidebar__title">{{ item.title }}</span>
              <span class="app-sidebar__hint">{{ item.hint }}</span>
            </span>
            <app-icon v-if="marks[item.name] === 'done'" :name="IPhCheck" class="app-sidebar__mark text-success" />
            <app-icon
              v-else-if="marks[item.name] === 'problem'"
              :name="IPhWarning"
              class="app-sidebar__mark text-warning"
            />
            <app-icon
              v-else-if="marks[item.name] === 'busy'"
              :name="IPhCircleNotch"
              spin
              class="app-sidebar__mark text-action"
            />
          </button>
        </nav>

        <button
          v-if="loaded && models.mode === 'app'"
          type="button"
          class="app-sidebar__model"
          :title="modelLight.name"
          @click="ui.go('settings')"
        >
          <span class="app-sidebar__light" :class="`app-sidebar__light--${modelLight.tone}`" aria-hidden="true" />
          <span class="app-sidebar__text">
            <span class="app-sidebar__title">{{ modelLight.text }}</span>
            <span v-if="modelLight.name" class="app-sidebar__hint text-truncate">{{ modelLight.name }}</span>
          </span>
        </button>

        <div class="app-sidebar__footer">
          <div v-if="models.online" class="d-flex gap-2 small">
            <app-icon :name="IPhCloudArrowDown" class="text-action flex-shrink-0 mt-1" />
            <span>Downloading from the internet. Your documents stay on this computer.</span>
          </div>
          <div v-else-if="models.offline" class="d-flex gap-2 small">
            <app-icon :name="IPhWifiSlash" class="text-success flex-shrink-0 mt-1" />
            <span>Offline. Your documents stay on this computer.</span>
          </div>
          <div v-else class="d-flex gap-2 small">
            <app-icon :name="IPhShieldCheck" class="text-success flex-shrink-0 mt-1" />
            <span>Works offline. Your documents stay on this computer.</span>
          </div>
          <div
            v-if="models.settings"
            class="form-check form-switch small mt-2 mb-0"
            title="When on, the app never connects to the internet, and downloads are turned off. Documents are never sent either way."
          >
            <input
              :id="offlineId"
              class="form-check-input"
              type="checkbox"
              role="switch"
              :checked="models.offline"
              :disabled="models.busy === 'offline'"
              @change="models.setOffline(($event.target as HTMLInputElement).checked)"
            />
            <label :for="offlineId" class="form-check-label">Work offline</label>
          </div>
          <div v-if="sidecar.health" class="small text-secondary mt-2">
            <button type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="ui.showWelcome()">
              How it works
            </button>
            ·
            <button type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="aboutShown = true">About</button>
            <div>Version {{ sidecar.health.version }}</div>
          </div>
        </div>
      </aside>

      <main class="app-main">
        <div class="app-main__content">
          <div v-if="!loaded" class="app-main__connect">
            <template v-if="sidecar.error || config.loadError">
              <h1 class="h4">The app could not start</h1>
              <p class="text-secondary">{{ sidecar.error || config.loadError }}</p>
              <div v-if="platform.kind === 'browser'" class="mb-3">
                <p class="small mb-1">Browser preview: start the sidecar in the repository folder, then try again.</p>
                <pre class="small border rounded p-2 bg-body-tertiary">TABLE_EXTRACT_SIDECAR_TOKEN=dev-token .venv_extract/bin/python -m table_extract serve --port 8765</pre>
              </div>
              <p v-else class="small">If this keeps happening, quit the app and open it again.</p>
              <button-icon
                variant="action"
                :icon-left="IPhArrowClockwise"
                label="Try again"
                :loading="sidecar.connecting"
                @click="start"
              />
            </template>
            <div v-else class="text-secondary d-flex align-items-center gap-2">
              <app-icon :name="IPhCircleNotch" spin /> Starting…
            </div>
          </div>

          <template v-else>
            <welcome-step v-if="ui.welcome" />
            <div v-else-if="config.restored && ui.step !== 'run'" class="alert alert-light border small d-flex align-items-center gap-2">
              <span class="flex-grow-1">The settings of your last run are back, so it can be continued.</span>
              <button type="button" class="btn btn-link btn-sm p-0" @click="config.startOver()">
                Start over with default settings
              </button>
            </div>
            <keep-alive v-if="!ui.welcome">
              <component :is="screens[ui.step]" :dragging="dragging" />
            </keep-alive>
          </template>
        </div>
      </main>

      <about-dialog v-if="sidecar.ready" v-model="aboutShown" />

      <div v-if="dragging" class="app__drop-overlay">
        <div>Drop PDF files or folders to add them</div>
      </div>
    </div>
  </b-app>
</template>

<style lang="scss">
@import '@/styles/settings';

.app {
  display: flex;
  min-height: 100vh;
}

.app-sidebar {
  width: $app-sidebar-width;
  flex-shrink: 0;
  background: $body-secondary-bg;
  display: flex;
  flex-direction: column;
  padding: $spacer * 1.5 $spacer;
  position: sticky;
  top: 0;
  height: 100vh;

  &__brand {
    padding: 0 $spacer * 0.5 $spacer * 1.5;
  }

  &__name {
    font-weight: 700;
    font-size: 1.25rem;
    color: $action;
    display: flex;
    align-items: center;
    gap: $spacer * 0.5;
  }

  &__beta {
    font-size: 0.625rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    line-height: 1;
    padding: 0.2rem 0.4rem;
    border-radius: $border-radius-pill;
    background: rgba($action, 0.12);
  }

  &__tagline {
    font-size: $font-size-sm;
    color: $text-muted;
  }

  &__nav {
    display: flex;
    flex-direction: column;
    gap: $spacer * 0.25;
  }

  &__step {
    display: flex;
    align-items: center;
    gap: $spacer * 0.75;
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
    padding: $spacer * 0.6 $spacer * 0.5;
    border-radius: $border-radius;
    color: $body-color;

    &:hover {
      background: rgba($action, 0.06);
    }

    &--active {
      background: $white;
      box-shadow: $box-shadow;

      .app-sidebar__number {
        background: $primary;
        color: $white;
      }
    }
  }

  &__number {
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: $white;
    border: 1px solid $tertiary;
    font-weight: 600;
    flex-shrink: 0;
  }

  &__step--active &__number {
    border-color: $primary;
  }

  &__text {
    display: flex;
    flex-direction: column;
    flex-grow: 1;
    min-width: 0;
  }

  &__title {
    font-weight: 600;
  }

  &__hint {
    font-size: $font-size-sm;
    color: $text-muted;
  }

  &__model {
    display: flex;
    align-items: center;
    gap: $spacer * 0.75;
    width: 100%;
    margin-top: $spacer;
    border: 0;
    border-top: 1px solid $tertiary;
    background: transparent;
    text-align: left;
    padding: $spacer $spacer * 0.5 $spacer * 0.6;
    color: $body-color;

    &:hover {
      background: rgba($action, 0.06);
    }
  }

  &__light {
    // Centred under the step numbers
    width: 0.75rem;
    height: 0.75rem;
    margin: 0 0.625rem;
    border-radius: 50%;
    flex-shrink: 0;

    &--on {
      background: $success;
      box-shadow: 0 0 0 3px rgba($success, 0.25);
    }

    &--starting {
      background: $warning;
      box-shadow: 0 0 0 3px rgba($warning, 0.25);
    }

    &--off {
      background: $gray-500;
      box-shadow: 0 0 0 3px rgba($gray-500, 0.25);
    }

    &--failed {
      background: $danger;
      box-shadow: 0 0 0 3px rgba($danger, 0.25);
    }
  }

  &__footer {
    margin-top: auto;
    padding: $spacer * 0.5;
  }
}

.app-main {
  flex-grow: 1;
  min-width: 0;

  &__content {
    max-width: 960px;
    padding: $spacer * 2 $spacer * 2.5;
  }

  &__connect {
    padding-top: 15vh;
    max-width: 40rem;
  }
}

.app__drop-overlay {
  position: fixed;
  inset: 0;
  background: rgba($primary, 0.12);
  border: 4px dashed $primary;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.5rem;
  font-weight: 600;
  color: $action;
  pointer-events: none;
  z-index: 2000;
}
</style>
