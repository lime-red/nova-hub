import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { alertsApi } from '@/services/api'

export interface Alert {
  id: number
  league_id: number
  league_name: string
  source_bbs_index: string
  dest_bbs_index: string
  expected_sequence: number
  detected_at: string
  resolved_at?: string
}

export const useAlertsStore = defineStore('alerts', () => {
  // State
  const alerts = ref<Alert[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Computed
  const unresolvedCount = computed(() =>
    alerts.value.filter(a => !a.resolved_at).length
  )

  const resolvedCount = computed(() =>
    alerts.value.filter(a => a.resolved_at).length
  )

  // Actions
  async function loadAlerts(): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await alertsApi.list()
      alerts.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load alerts'
    } finally {
      loading.value = false
    }
  }

  async function resolveAlert(id: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await alertsApi.resolve(id)
      // Update local state
      const alert = alerts.value.find(a => a.id === id)
      if (alert) {
        alert.resolved_at = new Date().toISOString()
      }
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to resolve alert'
      return false
    } finally {
      loading.value = false
    }
  }

  async function unresolveAlert(id: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await alertsApi.unresolve(id)
      // Update local state
      const alert = alerts.value.find(a => a.id === id)
      if (alert) {
        alert.resolved_at = undefined
      }
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to unresolve alert'
      return false
    } finally {
      loading.value = false
    }
  }

  function clearError(): void {
    error.value = null
  }

  return {
    // State
    alerts,
    loading,
    error,
    // Computed
    unresolvedCount,
    resolvedCount,
    // Actions
    loadAlerts,
    resolveAlert,
    unresolveAlert,
    clearError
  }
})
