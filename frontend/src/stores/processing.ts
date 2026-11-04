import { defineStore } from 'pinia'
import { ref } from 'vue'
import { processingApi, apiError } from '@/services/api'

export interface ProcessingRun {
  id: number
  started_at: string
  completed_at?: string
  duration?: string
  packets_processed: number
  status: string  // "running", "completed", "failed"
  leagues: string[]  // what it processed, e.g. ["015B", "015F"]
  league_name?: string
}

export interface ProcessingRunPacket {
  filename: string
  league_name: string
  route: string
}

export interface ProcessingRunFile {
  id: number
  filename: string
  file_type: string  // "score", "routes", "bbsinfo"
  league_name?: string  // the league it came from, e.g. "015B"
  file_data?: string
  file_data_html?: string
}

export interface ProcessingRunDetail {
  id: number
  started_at: string
  completed_at?: string
  duration?: string
  packets_processed: number
  status: string
  leagues: string[]
  league_name?: string
  error_message?: string
  dosemu_output?: string
  dosemu_output_html?: string
  packets: ProcessingRunPacket[]
  score_files: ProcessingRunFile[]
  routes_files: ProcessingRunFile[]
  bbsinfo_files: ProcessingRunFile[]
}

export const useProcessingStore = defineStore('processing', () => {
  // State
  const runs = ref<ProcessingRun[]>([])
  const currentRun = ref<ProcessingRunDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Actions
  async function loadRuns(limit = 50): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await processingApi.listRuns(limit)
      runs.value = response.data
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to load processing runs')
    } finally {
      loading.value = false
    }
  }

  // The id last asked for. A page shows nothing rather than another one's
  // data while its own loads, and a slow answer for an id since left behind
  // is dropped rather than shown under the new one's URL (#124).
  let currentRunWanted: number | null = null

  async function loadRun(id: number): Promise<void> {
    currentRunWanted = id
    if (currentRun.value?.id !== id) currentRun.value = null
    loading.value = true
    error.value = null

    try {
      const response = await processingApi.getRun(id)
      if (currentRunWanted === id) currentRun.value = response.data
    } catch (err: unknown) {
      if (currentRunWanted === id) error.value = apiError(err, 'Failed to load processing run')
    } finally {
      if (currentRunWanted === id) loading.value = false
    }
  }

  async function triggerProcessing(): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await processingApi.trigger()
      // Reload runs after triggering
      await loadRuns()
      return true
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to trigger processing')
      return false
    } finally {
      loading.value = false
    }
  }

  function clearError(): void {
    error.value = null
  }

  function clearCurrentRun(): void {
    currentRun.value = null
  }

  return {
    // State
    runs,
    currentRun,
    loading,
    error,
    // Actions
    loadRuns,
    loadRun,
    triggerProcessing,
    clearError,
    clearCurrentRun,
  }
})
