<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhFilePdf from '~icons/ph/file-pdf'
import IPhFiles from '~icons/ph/files'
import IPhFolderSimple from '~icons/ph/folder-simple'
import IPhFolderSimplePlus from '~icons/ph/folder-simple-plus'
import IPhPlus from '~icons/ph/plus'
import IPhTrash from '~icons/ph/trash'
import IPhUploadSimple from '~icons/ph/upload-simple'
import { computed, ref, useId } from 'vue'

import { friendlyMessage } from '@/api/schema'
import ChosenPages from '@/components/ChosenPages.vue'
import FieldMessages from '@/components/FieldMessages.vue'
import StepFooter from '@/components/StepFooter.vue'
import StepHeader from '@/components/StepHeader.vue'
import SwitchField from '@/components/SwitchField.vue'
import { platform } from '@/platform'
import { useConfigStore } from '@/stores/config'

defineProps<{ dragging?: boolean }>()

const config = useConfigStore()
const typed = ref('')
const showAll = ref(false)

const inputs = computed(() => config.config?.inputs ?? [])
const inputErrors = computed(() => config.errorsFor('inputs'))

function looksLikePdf(path: string): boolean {
  return /\.pdf$/i.test(path)
}

/** Messages that name a given input, e.g. "Not a PDF: /path". */
function messagesAbout(path: string): string[] {
  return inputErrors.value.filter((e) => e.message.includes(path)).map(friendlyMessage)
}

const otherMessages = computed(() =>
  inputErrors.value
    .filter((e) => !inputs.value.some((path) => e.message.includes(path)))
    .filter((e) => e.type !== 'too_short') // "add a document": the empty list says it, or it is out of date
    .map(friendlyMessage),
)

function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}

const pdfs = computed(() => config.pdfs)
const shownPdfs = computed(() => (showAll.value ? pdfs.value : pdfs.value.slice(0, 8)))

// "Which pages?" sets triage.enabled: the model finds the pages with tables, or every page is read
const pagesId = useId()
const PAGE_CHOICES = [
  {
    value: true,
    label: 'Find the pages with tables (recommended)',
    help: 'The model first looks at every page and keeps the ones that include tables and list owners, directors or subsidiaries. Only those are read.',
  },
  {
    value: false,
    label: 'Read every page',
    help: 'Slower, but no page is left out. Useful for short documents made only of tables.',
  },
]
const findPages = computed({
  get: () => !!config.config?.triage.enabled,
  set: (on: boolean) => config.set('triage.enabled', on),
})

async function addFiles() {
  config.addInputs(await platform.pickDocuments())
}

async function addFolders() {
  config.addInputs(await platform.pickDocumentFolders())
}

function addTyped() {
  if (!typed.value.trim()) return
  config.addInputs([typed.value])
  typed.value = ''
}
</script>

<template>
  <div class="documents-step">
    <step-header
      title="Documents"
      lead="Choose the PDF documents to read: single files, or whole folders. The documents stay where they are; the app only reads them."
    />

    <section class="card mb-4">
      <div class="card-body p-4">
        <div
          v-if="platform.hasNativeFiles"
          class="drop-zone mb-3"
          :class="{ 'drop-zone--active': dragging }"
        >
          <app-icon :name="IPhUploadSimple" size="2xl" class="text-secondary mb-2" />
          <div class="fw-medium">Drag and drop PDF files or folders here</div>
          <div class="small text-secondary mb-3">or</div>
          <div class="d-flex flex-wrap gap-2 justify-content-center">
            <button-icon variant="action" :icon-left="IPhFiles" label="Choose PDF files…" @click="addFiles" />
            <button-icon
              variant="outline-action"
              :icon-left="IPhFolderSimplePlus"
              label="Choose a folder…"
              @click="addFolders"
            />
          </div>
        </div>
        <form v-else class="mb-3" @submit.prevent="addTyped">
          <label for="typed-path" class="form-label fw-medium">Full path to a PDF file or a folder</label>
          <div class="input-group">
            <input
              id="typed-path"
              v-model="typed"
              type="text"
              class="form-control font-monospace"
              placeholder="For example /Users/me/Documents/annual-report.pdf"
              spellcheck="false"
            />
            <button-icon type="submit" variant="action" :icon-left="IPhPlus" label="Add" />
          </div>
          <div class="form-text">
            Browser preview: type paths here. The desktop app has file pickers and drag and drop.
          </div>
        </form>

        <ul v-if="inputs.length" class="list-group mb-3">
          <li v-for="(path, index) in inputs" :key="path" class="list-group-item d-flex align-items-start gap-2">
            <app-icon
              :name="looksLikePdf(path) ? IPhFilePdf : IPhFolderSimple"
              class="mt-1 text-secondary flex-shrink-0"
            />
            <div class="flex-grow-1 min-width-0">
              <div class="fw-medium text-truncate">{{ fileName(path) }}</div>
              <div class="small text-secondary text-break-all">{{ path }}</div>
              <div v-for="message in messagesAbout(path)" :key="message" class="small text-danger">
                {{ message }}
              </div>
            </div>
            <button-icon
              variant="link"
              class="text-secondary p-1"
              :icon-left="IPhTrash"
              hide-label
              :label="`Remove ${fileName(path)}`"
              @click="config.removeInput(index)"
            />
          </li>
        </ul>
        <p v-else class="text-secondary">No documents yet.</p>

        <div v-for="message in otherMessages" :key="message" class="alert alert-danger py-2">{{ message }}</div>

        <switch-field
          path="recursive"
          label="Also look inside subfolders"
          help="When a folder is chosen, include the PDFs in the folders inside it."
        />
      </div>
    </section>

    <section v-if="pdfs.length" class="card mb-4">
      <div class="card-body p-4">
        <h2 class="h5 mb-3">
          {{ pdfs.length }} PDF {{ pdfs.length === 1 ? 'document' : 'documents' }} will be read
        </h2>
        <ul class="list-unstyled mb-0 small">
          <li v-for="pdf in shownPdfs" :key="pdf" class="text-break-all mb-1">
            <app-icon :name="IPhFilePdf" class="me-1 text-secondary" />{{ pdf }}
          </li>
        </ul>
        <button v-if="pdfs.length > 8" type="button" class="btn btn-link btn-sm px-0" @click="showAll = !showAll">
          {{ showAll ? 'Show fewer' : `Show all ${pdfs.length}` }}
        </button>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-body p-4">
        <h2 class="h5 mb-1">Which pages?</h2>
        <p class="text-secondary small mb-3">
          Most pages of a long document hold no tables. Reading only the pages that do is much faster.
        </p>
        <div v-for="choice in PAGE_CHOICES" :key="String(choice.value)" class="form-check mb-2">
          <input
            :id="`${pagesId}-${choice.value}`"
            v-model="findPages"
            class="form-check-input"
            type="radio"
            :name="pagesId"
            :value="choice.value"
          />
          <label :for="`${pagesId}-${choice.value}`" class="form-check-label fw-medium">{{ choice.label }}</label>
          <div class="form-text mt-0">{{ choice.help }}</div>
        </div>
        <field-messages :messages="config.messagesFor('triage.enabled')" />
        <chosen-pages />
      </div>
    </section>

    <step-footer />
  </div>
</template>

<style scoped lang="scss">
.drop-zone {
  border: 2px dashed var(--bs-border-color);
  border-radius: var(--bs-border-radius-lg);
  padding: 2rem 1rem;
  text-align: center;
  background: var(--bs-tertiary-bg);
  transition: border-color 0.15s, background 0.15s;

  &--active {
    border-color: var(--bs-primary);
    background: var(--bs-primary-bg-subtle);
  }
}

.min-width-0 {
  min-width: 0;
}
</style>
