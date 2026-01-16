<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useAlertsStore } from '@/stores/alerts'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'

const alertsStore = useAlertsStore()
const authStore = useAuthStore()

const filter = ref<'all' | 'unresolved' | 'resolved'>('unresolved')

const filteredAlerts = computed(() => {
  switch (filter.value) {
    case 'unresolved':
      return alertsStore.alerts.filter(a => !a.resolved_at)
    case 'resolved':
      return alertsStore.alerts.filter(a => a.resolved_at)
    default:
      return alertsStore.alerts
  }
})

onMounted(async () => {
  await alertsStore.loadAlerts()
})

async function handleResolve(id: number) {
  if (confirm('Mark this alert as resolved?')) {
    await alertsStore.resolveAlert(id)
  }
}

async function handleUnresolve(id: number) {
  if (confirm('Mark this alert as unresolved?')) {
    await alertsStore.unresolveAlert(id)
  }
}

function formatRoute(source: string, dest: string): string {
  return `${source.toString().padStart(2, '0')} -> ${dest.toString().padStart(2, '0')}`
}

function formatSequence(seq: number): string {
  return seq.toString().padStart(3, '0')
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Alerts</h1>
          <p class="text-muted">Sequence gap detection and management</p>
        </div>
        <div class="alert-stats">
          <div class="stat-badge stat-danger">
            <span class="stat-value">{{ alertsStore.unresolvedCount }}</span>
            <span class="stat-label">Unresolved</span>
          </div>
          <div class="stat-badge stat-success">
            <span class="stat-value">{{ alertsStore.resolvedCount }}</span>
            <span class="stat-label">Resolved</span>
          </div>
        </div>
      </header>

      <!-- Filter Tabs -->
      <div class="filter-tabs">
        <button
          class="filter-tab"
          :class="{ active: filter === 'unresolved' }"
          @click="filter = 'unresolved'"
        >
          Unresolved ({{ alertsStore.unresolvedCount }})
        </button>
        <button
          class="filter-tab"
          :class="{ active: filter === 'resolved' }"
          @click="filter = 'resolved'"
        >
          Resolved ({{ alertsStore.resolvedCount }})
        </button>
        <button
          class="filter-tab"
          :class="{ active: filter === 'all' }"
          @click="filter = 'all'"
        >
          All ({{ alertsStore.alerts.length }})
        </button>
      </div>

      <!-- Loading -->
      <div v-if="alertsStore.loading && alertsStore.alerts.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading alerts...</p>
      </div>

      <!-- Error -->
      <div v-else-if="alertsStore.error" class="alert alert-error">
        {{ alertsStore.error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="() => alertsStore.loadAlerts()">Retry</button>
      </div>

      <!-- Alerts Table -->
      <div v-else class="card">
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>League</th>
                <th>Route</th>
                <th>Expected Seq</th>
                <th>Detected</th>
                <th>Status</th>
                <th v-if="authStore.isAdmin"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="filteredAlerts.length === 0">
                <td :colspan="authStore.isAdmin ? 7 : 6" class="text-center text-muted" style="padding: 2rem;">
                  <template v-if="filter === 'unresolved'">
                    No unresolved alerts
                  </template>
                  <template v-else-if="filter === 'resolved'">
                    No resolved alerts
                  </template>
                  <template v-else>
                    No alerts found
                  </template>
                </td>
              </tr>
              <tr v-for="alert in filteredAlerts" :key="alert.id" :class="{ 'row-resolved': alert.resolved_at }">
                <td class="font-mono">#{{ alert.id }}</td>
                <td>{{ alert.league_name }}</td>
                <td class="font-mono">{{ formatRoute(alert.source_bbs_index, alert.dest_bbs_index) }}</td>
                <td class="font-mono">{{ formatSequence(alert.expected_sequence) }}</td>
                <td class="text-muted">{{ alert.detected_at }}</td>
                <td>
                  <span class="badge" :class="alert.resolved_at ? 'badge-success' : 'badge-danger'">
                    {{ alert.resolved_at ? 'Resolved' : 'Unresolved' }}
                  </span>
                </td>
                <td v-if="authStore.isAdmin" class="actions">
                  <button
                    v-if="!alert.resolved_at"
                    class="btn btn-sm btn-success"
                    @click="handleResolve(alert.id)"
                    :disabled="alertsStore.loading"
                  >
                    Resolve
                  </button>
                  <button
                    v-else
                    class="btn btn-sm btn-secondary"
                    @click="handleUnresolve(alert.id)"
                    :disabled="alertsStore.loading"
                  >
                    Unresolve
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Info Card -->
      <div class="card mt-4">
        <div class="card-header">
          <h3>About Sequence Alerts</h3>
        </div>
        <div class="card-body">
          <p>
            Sequence alerts are generated when the system detects a gap in the packet sequence numbers
            for a particular route (source BBS to destination BBS). This may indicate:
          </p>
          <ul class="info-list-items">
            <li>Missing packets that were never uploaded</li>
            <li>Packets that were uploaded out of order</li>
            <li>Network or synchronization issues</li>
          </ul>
          <p class="text-muted mt-2">
            Resolved alerts are kept for historical reference but no longer appear in the dashboard.
          </p>
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

.alert-stats {
  display: flex;
  gap: 1rem;
}

.stat-badge {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0.75rem 1rem;
  border-radius: var(--radius-md);
  min-width: 80px;
}

.stat-danger {
  background: rgba(239, 68, 68, 0.1);
  color: var(--color-danger);
}

.stat-success {
  background: rgba(34, 197, 94, 0.1);
  color: var(--color-success);
}

.stat-value {
  font-size: 1.25rem;
  font-weight: 700;
}

.stat-label {
  font-size: 0.75rem;
  opacity: 0.8;
}

.filter-tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.filter-tab {
  padding: 0.5rem 1rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  cursor: pointer;
  font-weight: 500;
  transition: all 0.15s;
}

.filter-tab:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

.filter-tab.active {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: white;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.row-resolved {
  opacity: 0.6;
}

.actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.info-list-items {
  margin: 0.5rem 0;
  padding-left: 1.5rem;
}

.info-list-items li {
  margin: 0.25rem 0;
  color: var(--color-text-muted);
}

.mt-4 {
  margin-top: 1rem;
}

.mt-2 {
  margin-top: 0.5rem;
}
</style>
