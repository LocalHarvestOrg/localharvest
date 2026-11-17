<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhArrowRight from '~icons/ph/arrow-right'
import IPhDownloadSimple from '~icons/ph/download-simple'
import IPhFiles from '~icons/ph/files'
import IPhGauge from '~icons/ph/gauge'
import IPhHourglassMedium from '~icons/ph/hourglass-medium'
import IPhLockSimple from '~icons/ph/lock-simple'
import IPhScan from '~icons/ph/scan'
import IPhTable from '~icons/ph/table'

import { APP_NAME } from '@/app'
import CapabilityWarning from '@/components/CapabilityWarning.vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

const HOW = [
  {
    icon: IPhFiles,
    title: 'Choose your PDFs',
    text: 'Single files or whole folders.',
  },
  {
    icon: IPhScan,
    title: 'The AI model reads every page',
    text: 'Or the pages you select. It finds tables and extracts the data into a structured format.',
  },
  {
    icon: IPhTable,
    title: 'Open the results',
    text: 'Spreadsheets (CSV files) with the structured data.',
  },
]

const EXPECT = [
  {
    icon: IPhDownloadSimple,
    title: 'A one-time setup',
    text: 'The first time, the app downloads the program and the AI model that read the pages: Requires several GB of space on your computer. This is the only step that connects to the internet. After that, it works fully offline so your documents stay secure.',
    },
  {
    icon: IPhHourglassMedium,
    title: 'It takes time',
    text: 'Pages are read one by one. A few documents take minutes, a large folder can take hours. You can stop a run and continue it later.',
  },
  {
    icon: IPhGauge,
    title: 'Your computer will be busy',
    text: 'While a run lasts, the model uses most of the memory and graphics processor. Other apps may slow down and the fans may get loud. Keep the computer plugged in. The app keeps it awake, but closing a laptop’s lid still puts it to sleep.'
  },
  {
    icon: IPhLockSimple,
    title: 'Your documents stay private',
    text: 'Everything happens on this computer. Documents and pages are never sent over the internet.',
  },
]
</script>

<template>
  <div class="welcome-step">
    <header class="mb-4">
      <h1 class="h3 mb-2">Welcome to {{ APP_NAME }}</h1>
      <p class="text-secondary mb-0 welcome-step__lead">
{{ APP_NAME }} turns PDF tables into spreadsheets you can search and analyze easily. <br>
It’s built to pull data out of corporate ownership documents, but it can work on other tables too. <br>
Your documents stay private: everything runs on your own computer, with no uploads and no cloud services. The AI that reads your files runs locally, so no one else ever sees them.

      </p>
    </header>

    <capability-warning compact class="mb-4" />

    <section class="card mb-4">
      <div class="card-body p-4">
        <h2 class="h5 mb-3">How it works</h2>
        <ol class="welcome-step__how list-unstyled mb-0">
          <li v-for="item in HOW" :key="item.title" class="welcome-step__how-item">
            <app-icon :name="item.icon" size="2xl" class="text-action mb-2" />
            <div class="fw-medium mb-1">{{ item.title }}</div>
            <div class="small text-secondary">{{ item.text }}</div>
          </li>
        </ol>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-body p-4">
        <h2 class="h5 mb-3">What to expect</h2>
        <ul class="list-unstyled mb-0">
          <li v-for="item in EXPECT" :key="item.title" class="d-flex gap-3 mb-3">
            <app-icon :name="item.icon" size="lg" class="text-action flex-shrink-0 mt-1" />
            <div>
              <div class="fw-medium">{{ item.title }}</div>
              <div class="small text-secondary">{{ item.text }}</div>
            </div>
          </li>
        </ul>
      </div>
    </section>

    <footer class="d-flex flex-wrap align-items-center gap-3 border-top pt-3">
      <button-icon variant="action" size="lg" :icon-right="IPhArrowRight" label="Get started" @click="ui.leaveWelcome()" />
      <span class="small text-secondary">You can read this again from “How it works”, at the bottom of the sidebar.</span>
    </footer>
  </div>
</template>

<style scoped lang="scss">
.welcome-step__lead {
  max-width: 46rem;
  font-size: 1.125rem;
}

.welcome-step__how {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: 1.5rem;
}

.welcome-step__how-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
</style>
