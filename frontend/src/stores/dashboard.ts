import { defineStore } from 'pinia'
import { ref } from 'vue'
import { dashboardApi } from '@/services/api'

export interface DashboardStats {
  total_packets: number
  packets_24h: number
  active_clients: number
  active_leagues: number
  pending_alerts: number
  processing_runs_24h: number
}

export interface ActivityItem {
  id: number
  type: string
  description: string
  timestamp: string
  client_name?: string
  filename?: string
  league_name?: string
  route?: string
  details?: Record<string, unknown>
}

export interface AlertSummary {
  id: number
  league_name: string
  source: string
  dest: string
  expected_sequence: number
  detected_at: string
}

export interface ChartData {
  labels: string[]
  data: number[]
}

export const useDashboardStore = defineStore('dashboard', () => {
  // State
  const stats = ref<DashboardStats | null>(null)
  const activity = ref<ActivityItem[]>([])
  const alerts = ref<AlertSummary[]>([])
  const activityChart = ref<ChartData | null>(null)
  const leagueChart = ref<ChartData | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Actions
  async function loadDashboard(): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await dashboardApi.getFull()
      const data = response.data

      stats.value = data.stats
      activity.value = data.activity
      alerts.value = data.alerts
      activityChart.value = data.activity_chart
      leagueChart.value = data.league_chart
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load dashboard'
    } finally {
      loading.value = false
    }
  }

  async function loadStats(): Promise<void> {
    try {
      const response = await dashboardApi.getStats()
      stats.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load stats'
    }
  }

  async function loadActivity(limit = 20): Promise<void> {
    try {
      const response = await dashboardApi.getActivity(limit)
      activity.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load activity'
    }
  }

  function clearError(): void {
    error.value = null
  }

  // WebSocket handling for real-time updates
  function handleWebSocketMessage(message: { type: string; [key: string]: unknown }): void {
    switch (message.type) {
      case 'stats_update':
        if (message.stats) {
          stats.value = message.stats as DashboardStats
        }
        break
      case 'packet_received':
        // Reload activity feed
        loadActivity()
        break
      case 'alert_created':
        // Reload alerts
        loadDashboard()
        break
      case 'processing_complete':
        // Reload stats
        loadStats()
        break
    }
  }

  return {
    // State
    stats,
    activity,
    alerts,
    activityChart,
    leagueChart,
    loading,
    error,
    // Actions
    loadDashboard,
    loadStats,
    loadActivity,
    clearError,
    handleWebSocketMessage
  }
})
