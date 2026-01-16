<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'
import AppLayout from '@/components/AppLayout.vue'
import ActivityChart from '@/components/ActivityChart.vue'
import LeagueChart from '@/components/LeagueChart.vue'

const dashboardStore = useDashboardStore()
const wsConnection = ref<WebSocket | null>(null)

onMounted(async () => {
  await dashboardStore.loadDashboard()
  connectWebSocket()
})

onUnmounted(() => {
  if (wsConnection.value) {
    wsConnection.value.close()
  }
})

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/management/api/v1/ws/dashboard`

  try {
    wsConnection.value = new WebSocket(wsUrl)

    wsConnection.value.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data)
        dashboardStore.handleWebSocketMessage(message)
      } catch {
        // Ignore parse errors
      }
    }

    wsConnection.value.onclose = () => {
      // Reconnect after 5 seconds
      setTimeout(connectWebSocket, 5000)
    }
  } catch {
    // WebSocket not available, fall back to polling
    setTimeout(connectWebSocket, 10000)
  }
}

function formatNumber(num: number | undefined): string {
  return (num ?? 0).toLocaleString()
}

async function refresh() {
  await dashboardStore.loadDashboard()
}
</script>

<template>
  <AppLayout>
    <div class="dashboard">
      <header class="page-header">
        <div>
          <h1>Dashboard</h1>
          <p class="text-muted">Overview of Nova Hub activity</p>
        </div>
        <button class="btn btn-secondary" @click="refresh" :disabled="dashboardStore.loading">
          Refresh
        </button>
      </header>

      <!-- Loading State -->
      <div v-if="dashboardStore.loading && !dashboardStore.stats" class="loading-state">
        <div class="spinner"></div>
        <p>Loading dashboard...</p>
      </div>

      <!-- Error State -->
      <div v-else-if="dashboardStore.error" class="alert alert-error">
        {{ dashboardStore.error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="refresh">
          Retry
        </button>
      </div>

      <!-- Dashboard Content -->
      <template v-else>
        <!-- Stats Cards -->
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-value">{{ formatNumber(dashboardStore.stats?.packets_24h) }}</div>
            <div class="stat-label">Packets (24h)</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ formatNumber(dashboardStore.stats?.total_packets) }}</div>
            <div class="stat-label">Total Packets</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ formatNumber(dashboardStore.stats?.active_clients) }}</div>
            <div class="stat-label">Active Clients</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ formatNumber(dashboardStore.stats?.active_leagues) }}</div>
            <div class="stat-label">Active Leagues</div>
          </div>
          <div class="stat-card" :class="{ 'has-alerts': (dashboardStore.stats?.pending_alerts ?? 0) > 0 }">
            <div class="stat-value">{{ formatNumber(dashboardStore.stats?.pending_alerts) }}</div>
            <div class="stat-label">Pending Alerts</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ formatNumber(dashboardStore.stats?.processing_runs_24h) }}</div>
            <div class="stat-label">Processing (24h)</div>
          </div>
        </div>

        <!-- Charts Row -->
        <div class="charts-grid">
          <div class="card">
            <div class="card-header">
              <h3>Activity (24 Hours)</h3>
            </div>
            <div class="card-body">
              <ActivityChart
                v-if="dashboardStore.activityChart"
                :labels="dashboardStore.activityChart.labels"
                :data="dashboardStore.activityChart.data"
              />
              <div v-else class="empty-state">No activity data</div>
            </div>
          </div>

          <div class="card">
            <div class="card-header">
              <h3>Packets by League</h3>
            </div>
            <div class="card-body">
              <LeagueChart
                v-if="dashboardStore.leagueChart && dashboardStore.leagueChart.data.length > 0"
                :labels="dashboardStore.leagueChart.labels"
                :data="dashboardStore.leagueChart.data"
              />
              <div v-else class="empty-state">No league data</div>
            </div>
          </div>
        </div>

        <div class="dashboard-grid">
          <!-- Recent Activity -->
          <div class="card activity-card">
            <div class="card-header">
              <h3>Recent Activity</h3>
            </div>
            <div class="card-body">
              <div v-if="dashboardStore.activity.length === 0" class="empty-state">
                No recent activity
              </div>
              <ul v-else class="activity-list">
                <li v-for="item in dashboardStore.activity" :key="item.id" class="activity-item">
                  <span class="activity-icon" :class="item.type">
                    {{ item.type === 'upload' ? 'U' : 'D' }}
                  </span>
                  <div class="activity-content">
                    <div class="activity-description">
                      <strong>{{ item.client_name }}</strong>
                      {{ item.type === 'upload' ? 'uploaded' : 'downloaded' }}
                      <code class="activity-filename">{{ item.filename }}</code>
                    </div>
                    <div class="activity-meta text-muted">
                      <span>{{ item.league_name }}</span>
                      <span class="font-mono">{{ item.route }}</span>
                      <span>{{ item.timestamp }}</span>
                    </div>
                  </div>
                </li>
              </ul>
            </div>
          </div>

          <!-- Alerts -->
          <div class="card alerts-card">
            <div class="card-header">
              <h3>Pending Alerts</h3>
              <router-link to="/alerts" class="btn btn-sm btn-secondary">View All</router-link>
            </div>
            <div class="card-body">
              <div v-if="dashboardStore.alerts.length === 0" class="empty-state text-success">
                No pending alerts
              </div>
              <ul v-else class="alerts-list">
                <li v-for="alert in dashboardStore.alerts" :key="alert.id" class="alert-item">
                  <div class="alert-info">
                    <span class="alert-league">{{ alert.league_name }}</span>
                    <span class="alert-route font-mono">{{ alert.source }} → {{ alert.dest }}</span>
                  </div>
                  <div class="alert-meta text-muted">
                    <span>Expected: {{ alert.expected_sequence }}</span>
                    <span>{{ alert.detected_at }}</span>
                  </div>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.dashboard {
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
  justify-content: center;
  padding: 4rem;
  gap: 1rem;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.stat-card {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  padding: 1.25rem;
  border: 1px solid var(--color-border);
}

.stat-card.has-alerts {
  border-color: var(--color-warning);
  background: linear-gradient(135deg, var(--color-surface) 0%, #fef3c7 100%);
}

.stat-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--color-primary);
  line-height: 1;
  margin-bottom: 0.5rem;
}

.stat-card.has-alerts .stat-value {
  color: var(--color-warning);
}

.stat-label {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.charts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 1.5rem;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-header h3 {
  font-size: 1rem;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: var(--color-text-muted);
}

.activity-list,
.alerts-list {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 300px;
  overflow-y: auto;
}

.activity-item {
  display: flex;
  gap: 0.75rem;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border);
}

.activity-item:last-child {
  border-bottom: none;
}

.activity-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background-color: var(--color-bg);
  font-weight: 600;
  font-size: 0.75rem;
  flex-shrink: 0;
}

.activity-icon.upload {
  background-color: #dbeafe;
  color: #1e40af;
}

.activity-icon.download {
  background-color: #dcfce7;
  color: #166534;
}

.activity-icon.processing {
  background-color: #fef3c7;
  color: #92400e;
}

.activity-content {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 0;
}

.activity-description {
  font-size: 0.875rem;
  line-height: 1.4;
}

.activity-filename {
  background: var(--color-bg);
  padding: 0.125rem 0.375rem;
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
}

.activity-meta {
  display: flex;
  gap: 0.75rem;
  font-size: 0.75rem;
  margin-top: 0.25rem;
}

.activity-time {
  font-size: 0.75rem;
}

.alert-item {
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border);
}

.alert-item:last-child {
  border-bottom: none;
}

.alert-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
}

.alert-league {
  font-weight: 500;
}

.alert-route {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.alert-meta {
  display: flex;
  gap: 1rem;
  font-size: 0.75rem;
}
</style>
