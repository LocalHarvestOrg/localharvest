<script setup lang="ts">
import { computed } from 'vue'

import type { StageName } from '@/api/types'
import { APP_NAME } from '@/app'
import { useConfigStore } from '@/stores/config'

import ColumnsEditor from './ColumnsEditor.vue'
import ExampleRowsEditor from './ExampleRowsEditor.vue'
import PromptText from './PromptText.vue'
import RadioOtherField from './RadioOtherField.vue'
import RelationshipsEditor from './RelationshipsEditor.vue'

// The questions that describe the documents. Their answers are the guide (table_extract/prompts.py): the
// backend writes both prompts from them, so none of this needs to know how a prompt is written. They are grouped
// by the step that uses them: finding the pages (triage, only shown when the run uses it), reading the tables
// (extraction), or both.

const config = useConfigStore()

const STAGE_NAMES: Record<StageName, string> = { triage: 'finding the pages', extraction: 'reading the tables' }

/** The prompts used in this run that were edited by hand, so the answers no longer write them. */
const handEdited = computed(() =>
  (['triage', 'extraction'] as const).filter(
    (stage) => config.isHandEdited(stage) && (stage !== 'triage' || config.triageUsed),
  ),
)
</script>

<template>
  <div class="guided-prompt">
    <p class="small text-secondary mb-2">
      Answer these questions to describe your documents. {{ APP_NAME }} turns your answers into the instructions given
      to the AI. Every answer is set for corporate ownership documents: change only what does not fit yours.
    </p>
    <p v-if="config.triageUsed" class="small text-secondary mb-4">
      The AI works in two steps: first it finds the pages with a table like yours, then it reads the tables on those
      pages. Some questions are used in one step, some in both.
    </p>
    <p v-else class="small text-secondary mb-4">
      In Documents, you chose to read every page or chose the pages yourself, so the AI does not look for the pages:
      only the questions used to read the tables are shown.
    </p>

    <div v-for="stage in handEdited" :key="stage" class="alert alert-warning py-2 d-flex align-items-start gap-3">
      <span class="flex-grow-1 small">
        You edited the raw instructions for {{ STAGE_NAMES[stage] }} by hand, so your answers no longer change them{{
          stage === 'extraction' ? ', except for the columns' : ''
        }}.
      </span>
      <button type="button" class="btn btn-outline-secondary btn-sm flex-shrink-0" @click="config.rebuildPrompt(stage)">
        Rebuild from the answers
      </button>
    </div>

    <section class="guided-prompt__group">
      <h3 class="h6 mb-1">Your tables</h3>
      <p class="small text-secondary mb-3">
        {{ config.triageUsed ? 'Used to find the pages and to read the tables.' : 'Used to read the tables.' }}
      </p>

      <prompt-text
        class="mb-4"
        path="guide.table_types"
        label="What type of tables do you want to extract?"
        help="Name the kinds of tables that hold the information you want, separated by commas. The AI looks for information that looks like one of them, so be specific: “register of members” works better than “list of members”."
        :rows="3"
      />

      <radio-other-field
        path="guide.entity"
        label="What type of entity do the tables describe?"
        help="Most tables are about one main entity, such as a company, with other people or companies linked to it. Choose what that main entity is. In a register of shareholders it is the company; in a list of donations it could be a person."
        :options="[{ label: 'Company', value: 'company' }]"
        other-label="Other"
        other-placeholder="For example: person"
      />

      <relationships-editor />
    </section>

    <section v-if="config.triageUsed" class="guided-prompt__group">
      <h3 class="h6 mb-1">Finding the pages <span class="fw-normal text-secondary">· step 1</span></h3>
      <p class="small text-secondary mb-3">
        The AI looks at each page and keeps only those with a table like yours. These answers are only used for that.
      </p>

      <radio-other-field
        path="guide.document_type"
        label="What type of documents do you have?"
        help="This tells the AI what the documents are about, so it has some context. Keep Corporate ownership for company registers, share registers and similar documents. For anything else, choose Other and describe them in a few words."
        :options="[{ label: 'Corporate ownership', value: 'corporate ownership' }]"
        other-label="Other"
        other-placeholder="For example: political donations"
      />

      <prompt-text
        class="mb-4"
        path="guide.exclusions"
        label="Which similar pages should be ignored?"
        help="Describe pages that can look like a match but should be skipped. The text below is written for corporate documents: edit it, or clear it if it does not apply to yours."
        :rows="8"
      />
    </section>

    <section class="guided-prompt__group">
      <h3 class="h6 mb-1">
        Reading the tables <span v-if="config.triageUsed" class="fw-normal text-secondary">· step 2</span>
      </h3>
      <p class="small text-secondary mb-3">The AI reads the tables on each page and writes one row per relationship.</p>

      <columns-editor />

      <div class="mb-3">
        <div class="fw-medium">Give the AI an example</div>
        <p class="form-text mt-0 mb-0">
          One good example helps more than a long explanation. First describe a page from your documents in words, then
          fill in what the AI should find on it. Invented names are fine.
          <template v-if="config.triageUsed">The description of the page is also used to find the pages.</template>
        </p>
      </div>

      <prompt-text
        class="mb-4"
        path="guide.example_page"
        label="Describe an example page"
        help="Say what the page is called, which columns the table has and what each row says."
        :rows="7"
      />

      <example-rows-editor />
    </section>
  </div>
</template>

<style scoped lang="scss">
.guided-prompt__group + .guided-prompt__group {
  border-top: 1px solid var(--bs-border-color);
  padding-top: 1.25rem;
  margin-top: 0.5rem;
}
</style>
