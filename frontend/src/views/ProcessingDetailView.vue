<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useProcessingStore } from '@/stores/processing'
import AppLayout from '@/components/AppLayout.vue'

const route = useRoute()
const processingStore = useProcessingStore()

const runId = computed(() => Number(route.params.id))
const activeTab = ref<'overview' | 'logs' | 'scores' | 'files'>('overview')
const selectedFile = ref<number | null>(null)

onMounted(async () => {
  await processingStore.loadRun(runId.value)
})

function statusClass(status: string): string {
  switch (status) {
    case 'completed':
      return 'badge-success'
    case 'running':
      return 'badge-info'
    case 'failed':
      return 'badge-danger'
    default:
      return 'badge-secondary'
  }
}

function selectFile(id: number) {
  selectedFile.value = selectedFile.value === id ? null : id
}

function getSelectedFileContent() {
  if (!selectedFile.value || !processingStore.currentRun) return null

  const allFiles = [
    ...processingStore.currentRun.score_files,
    ...processingStore.currentRun.routes_files,
    ...processingStore.currentRun.bbsinfo_files
  ]

  return allFiles.find(f => f.id === selectedFile.value)
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <!-- Loading -->
      <div v-if="processingStore.loading && !processingStore.currentRun" class="loading-state">
        <div class="spinner"></div>
        <p>Loading processing run...</p>
      </div>

      <!-- Error -->
      <div v-else-if="processingStore.error && !processingStore.currentRun" class="alert alert-error">
        {{ processingStore.error }}
        <router-link to="/processing" class="btn btn-sm btn-secondary mt-2">Back to Runs</router-link>
      </div>

      <!-- Run Details -->
      <template v-else-if="processingStore.currentRun">
        <header class="page-header">
          <div>
            <div class="breadcrumb">
              <router-link to="/processing">Processing</router-link>
              <span>/</span>
              <span>Run #{{ processingStore.currentRun.id }}</span>
            </div>
            <h1>Processing Run #{{ processingStore.currentRun.id }}</h1>
            <p class="text-muted">
              {{ processingStore.currentRun.league_name || 'All Leagues' }}
            </p>
          </div>
          <span class="badge badge-lg" :class="statusClass(processingStore.currentRun.status)">
            {{ processingStore.currentRun.status }}
          </span>
        </header>

        <!-- Tabs -->
        <div class="tabs">
          <button
            class="tab"
            :class="{ active: activeTab === 'overview' }"
            @click="activeTab = 'overview'"
          >
            Overview
          </button>
          <button
            class="tab"
            :class="{ active: activeTab === 'logs' }"
            @click="activeTab = 'logs'"
          >
            Logs
          </button>
          <button
            class="tab"
            :class="{ active: activeTab === 'scores' }"
            @click="activeTab = 'scores'"
            v-if="processingStore.currentRun.score_files.length > 0"
          >
            Score Files ({{ processingStore.currentRun.score_files.length }})
          </button>
          <button
            class="tab"
            :class="{ active: activeTab === 'files' }"
            @click="activeTab = 'files'"
            v-if="processingStore.currentRun.routes_files.length + processingStore.currentRun.bbsinfo_files.length > 0"
          >
            Other Files
          </button>
        </div>

        <!-- Overview Tab -->
        <div v-if="activeTab === 'overview'" class="tab-content">
          <div class="content-grid">
            <!-- Info Card -->
            <div class="card">
              <div class="card-header">
                <h3>Run Information</h3>
              </div>
              <div class="card-body">
                <dl class="info-list">
                  <div class="info-item">
                    <dt>Started</dt>
                    <dd>{{ processingStore.currentRun.started_at }}</dd>
                  </div>
                  <div class="info-item">
                    <dt>Completed</dt>
                    <dd>{{ processingStore.currentRun.completed_at || 'Still running...' }}</dd>
                  </div>
                  <div class="info-item">
                    <dt>Duration</dt>
                    <dd class="font-mono">{{ processingStore.currentRun.duration || '-' }}</dd>
                  </div>
                  <div class="info-item">
                    <dt>Packets Processed</dt>
                    <dd>{{ processingStore.currentRun.packets_processed }}</dd>
                  </div>
                </dl>
              </div>
            </div>

            <!-- Error Card (if failed) -->
            <div v-if="processingStore.currentRun.error_message" class="card card-error">
              <div class="card-header">
                <h3>Error Message</h3>
              </div>
              <div class="card-body">
                <pre class="error-output">{{ processingStore.currentRun.error_message }}</pre>
              </div>
            </div>
          </div>

          <!-- Packets Processed -->
          <div v-if="processingStore.currentRun.packets.length > 0" class="card mt-4">
            <div class="card-header">
              <h3>Packets Processed</h3>
            </div>
            <div class="card-body" style="padding: 0;">
              <table class="table">
                <thead>
                  <tr>
                    <th>Filename</th>
                    <th>League</th>
                    <th>Route</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="packet in processingStore.currentRun.packets" :key="packet.filename">
                    <td class="font-mono">{{ packet.filename }}</td>
                    <td>{{ packet.league_name }}</td>
                    <td class="font-mono">{{ packet.route }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <!-- Logs Tab -->
        <div v-if="activeTab === 'logs'" class="tab-content">
          <div class="card">
            <div class="card-header">
              <h3>DOSEmu Output</h3>
            </div>
            <div class="card-body">
              <div
                v-if="processingStore.currentRun.dosemu_output_html"
                class="log-output ansi-output"
                v-html="processingStore.currentRun.dosemu_output_html"
              ></div>
              <pre
                v-else-if="processingStore.currentRun.dosemu_output"
                class="log-output"
              >{{ processingStore.currentRun.dosemu_output }}</pre>
              <p v-else class="text-muted">No log output available</p>
            </div>
          </div>
        </div>

        <!-- Scores Tab -->
        <div v-if="activeTab === 'scores'" class="tab-content">
          <div class="files-grid">
            <div
              v-for="file in processingStore.currentRun.score_files"
              :key="file.id"
              class="card file-card"
              :class="{ 'file-card-selected': selectedFile === file.id }"
              @click="selectFile(file.id)"
            >
              <div class="card-header">
                <h4 class="font-mono">{{ file.filename }}</h4>
              </div>
            </div>
          </div>

          <!-- Selected Score File Preview -->
          <div v-if="selectedFile && getSelectedFileContent()" class="card mt-4">
            <div class="card-header">
              <h3>{{ getSelectedFileContent()?.filename }}</h3>
            </div>
            <div class="card-body">
              <div
                v-if="getSelectedFileContent()?.file_data_html"
                class="score-output ansi-output"
                v-html="getSelectedFileContent()?.file_data_html"
              ></div>
              <pre
                v-else
                class="score-output"
              >{{ getSelectedFileContent()?.file_data }}</pre>
            </div>
          </div>
        </div>

        <!-- Other Files Tab -->
        <div v-if="activeTab === 'files'" class="tab-content">
          <!-- Routes Files -->
          <div v-if="processingStore.currentRun.routes_files.length > 0" class="mb-4">
            <h3 class="section-title">Routes Files</h3>
            <div class="files-grid">
              <div
                v-for="file in processingStore.currentRun.routes_files"
                :key="file.id"
                class="card file-card"
                :class="{ 'file-card-selected': selectedFile === file.id }"
                @click="selectFile(file.id)"
              >
                <div class="card-header">
                  <h4 class="font-mono">{{ file.filename }}</h4>
                </div>
              </div>
            </div>
          </div>

          <!-- BBS Info Files -->
          <div v-if="processingStore.currentRun.bbsinfo_files.length > 0">
            <h3 class="section-title">BBS Info Files</h3>
            <div class="files-grid">
              <div
                v-for="file in processingStore.currentRun.bbsinfo_files"
                :key="file.id"
                class="card file-card"
                :class="{ 'file-card-selected': selectedFile === file.id }"
                @click="selectFile(file.id)"
              >
                <div class="card-header">
                  <h4 class="font-mono">{{ file.filename }}</h4>
                </div>
              </div>
            </div>
          </div>

          <!-- Selected File Preview -->
          <div v-if="selectedFile && getSelectedFileContent()" class="card mt-4">
            <div class="card-header">
              <h3>{{ getSelectedFileContent()?.filename }}</h3>
            </div>
            <div class="card-body">
              <pre class="file-output">{{ getSelectedFileContent()?.file_data }}</pre>
            </div>
          </div>
        </div>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin: 0.25rem 0;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  margin-bottom: 0.25rem;
}

.breadcrumb a {
  color: var(--color-primary);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.badge-lg {
  font-size: 0.875rem;
  padding: 0.5rem 1rem;
}

.tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 1.5rem;
}

.tab {
  padding: 0.75rem 1rem;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  font-weight: 500;
  transition: color 0.15s, border-color 0.15s;
}

.tab:hover {
  color: var(--color-text);
}

.tab.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.tab-content {
  animation: fadeIn 0.15s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.content-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.info-list {
  margin: 0;
}

.info-item {
  display: flex;
  justify-content: space-between;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border);
}

.info-item:last-child {
  border-bottom: none;
}

.info-item dt {
  color: var(--color-text-muted);
}

.info-item dd {
  margin: 0;
  font-weight: 500;
}

.card-error {
  border-color: var(--color-danger);
}

.card-error .card-header {
  background: rgba(239, 68, 68, 0.1);
}

.error-output,
.log-output,
.score-output,
.file-output {
  background: var(--color-bg);
  border-radius: var(--radius-md);
  padding: 1rem;
  overflow-x: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: monospace;
  font-size: 0.875rem;
  line-height: 1.5;
  max-height: 600px;
  overflow-y: auto;
}

.ansi-output {
  background: #1a1a2e;
  color: #e0e0e0;
}

.files-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
}

.file-card {
  cursor: pointer;
  transition: box-shadow 0.15s, transform 0.15s;
}

.file-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.file-card-selected {
  box-shadow: 0 0 0 2px var(--color-primary);
}

.file-card .card-header {
  padding: 1rem;
}

.file-card .card-header h4 {
  margin: 0;
  font-size: 0.875rem;
}

.section-title {
  font-size: 1rem;
  margin-bottom: 1rem;
  color: var(--color-text-muted);
}

.mt-4 {
  margin-top: 1rem;
}

.mb-4 {
  margin-bottom: 1rem;
}

.mt-2 {
  margin-top: 0.5rem;
}
</style>
