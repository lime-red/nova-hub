<script setup lang="ts">
import { onMounted } from 'vue'
import { useProcessingStore } from '@/stores/processing'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'

const processingStore = useProcessingStore()
const authStore = useAuthStore()

onMounted(async () => {
  await processingStore.loadRuns()
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

async function handleTrigger() {
  if (confirm('Trigger packet processing now?')) {
    await processingStore.triggerProcessing()
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Processing Runs</h1>
          <p class="text-muted">Game processing history and logs</p>
        </div>
        <button
          v-if="authStore.isAdmin"
          class="btn btn-primary"
          @click="handleTrigger"
          :disabled="processingStore.loading"
        >
          Trigger Processing
        </button>
      </header>

      <!-- Loading -->
      <div v-if="processingStore.loading && processingStore.runs.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading processing runs...</p>
      </div>

      <!-- Error -->
      <div v-else-if="processingStore.error" class="alert alert-error">
        {{ processingStore.error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="() => processingStore.loadRuns()">Retry</button>
      </div>

      <!-- Runs Table -->
      <div v-else class="card">
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Started</th>
                <th>Duration</th>
                <th>League</th>
                <th>Packets</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="processingStore.runs.length === 0">
                <td colspan="7" class="text-center text-muted" style="padding: 2rem;">
                  No processing runs found
                </td>
              </tr>
              <tr v-for="run in processingStore.runs" :key="run.id">
                <td class="font-mono">#{{ run.id }}</td>
                <td class="text-muted">{{ run.started_at }}</td>
                <td class="font-mono">{{ run.duration || '-' }}</td>
                <td>{{ run.league_name || '-' }}</td>
                <td>{{ run.packets_processed }}</td>
                <td>
                  <span class="badge" :class="statusClass(run.status)">
                    {{ run.status }}
                  </span>
                </td>
                <td class="actions">
                  <router-link :to="`/processing/${run.id}`" class="btn btn-sm btn-secondary">
                    View
                  </router-link>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.mt-2 {
  margin-top: 0.5rem;
}
</style>
