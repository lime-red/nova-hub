import { defineStore } from 'pinia'
import { ref } from 'vue'
import { leaguesApi } from '@/services/api'

export interface League {
  id: number
  league_id: string
  game_type: string
  full_id: string
  name: string
  description?: string
  is_active: boolean
  member_count: number
}

export interface LeagueMember {
  membership_id: number
  client_id: number
  bbs_name: string
  bbs_index: number
  fidonet_address?: string
  client_oauth_id: string
  joined_at?: string
  is_active: boolean
}

export interface LeagueStats {
  total_packets: number
  processed_packets: number
  processing_runs: number
}

export interface AvailableClient {
  id: number
  bbs_name: string
  client_id: string
}

export interface LeagueDetail {
  id: number
  league_id: string
  game_type: string
  full_id: string
  name: string
  description?: string
  dosemu_path?: string
  game_executable?: string
  is_active: boolean
  members: LeagueMember[]
  available_clients: AvailableClient[]
  stats: LeagueStats
}

export const useLeaguesStore = defineStore('leagues', () => {
  // State
  const leagues = ref<League[]>([])
  const currentLeague = ref<LeagueDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Actions
  async function loadLeagues(): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await leaguesApi.list()
      leagues.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load leagues'
    } finally {
      loading.value = false
    }
  }

  async function loadLeague(id: number): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await leaguesApi.get(id)
      currentLeague.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load league'
    } finally {
      loading.value = false
    }
  }

  async function createLeague(data: {
    league_id: string
    game_type: string
    name: string
    description?: string
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.create(data)
      await loadLeagues()
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to create league'
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateLeague(id: number, data: {
    name?: string
    description?: string
    is_active?: boolean
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.update(id, data)
      await loadLeagues()
      if (currentLeague.value?.id === id) {
        await loadLeague(id)
      }
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to update league'
      return false
    } finally {
      loading.value = false
    }
  }

  async function deleteLeague(id: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.delete(id)
      await loadLeagues()
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to delete league'
      return false
    } finally {
      loading.value = false
    }
  }

  async function addMember(leagueId: number, data: {
    client_id: number
    bbs_index: number
    fidonet_address: string
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.addMember(leagueId, data)
      await loadLeague(leagueId)
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to add member'
      return false
    } finally {
      loading.value = false
    }
  }

  async function removeMember(leagueId: number, clientId: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.removeMember(leagueId, clientId)
      await loadLeague(leagueId)
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to remove member'
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateBbsIndex(leagueId: number, membershipId: number, bbsIndex: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.updateBbsIndex(leagueId, membershipId, bbsIndex)
      await loadLeague(leagueId)
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to update BBS index'
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateFidonet(leagueId: number, membershipId: number, address: string): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.updateFidonet(leagueId, membershipId, address)
      await loadLeague(leagueId)
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to update Fidonet address'
      return false
    } finally {
      loading.value = false
    }
  }

  function clearError(): void {
    error.value = null
  }

  function clearCurrentLeague(): void {
    currentLeague.value = null
  }

  return {
    // State
    leagues,
    currentLeague,
    loading,
    error,
    // Actions
    loadLeagues,
    loadLeague,
    createLeague,
    updateLeague,
    deleteLeague,
    addMember,
    removeMember,
    updateBbsIndex,
    updateFidonet,
    clearError,
    clearCurrentLeague
  }
})
