import { defineStore } from 'pinia'
import { ref } from 'vue'
import { leaguesApi, apiError } from '@/services/api'

export interface League {
  id: number
  league_id: string
  game_type: string
  full_id: string
  name: string
  description?: string
  hub_fidonet_address?: string
  hub_routes_mail?: boolean
  is_active: boolean
  member_count: number
}

export interface FtnAddressRef {
  id: number
  address: string
}

export interface LeagueMember {
  membership_id: number
  client_id: number
  bbs_name: string
  bbs_index: number
  ftn_address_id?: number
  fidonet_address?: string
  client_ftn_addresses: FtnAddressRef[]
  client_oauth_id: string
  board_key?: string | null  // Immortal Barons: the board's public signing key
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
  ftn_addresses: FtnAddressRef[]
  // Its active memberships elsewhere, for pre-filling the index
  other_leagues: { full_id: string; bbs_index: number }[]
}

export interface LeagueDetail {
  id: number
  league_id: string
  game_type: string
  full_id: string
  name: string
  description?: string
  hub_fidonet_address?: string
  hub_routes_mail?: boolean
  dosemu_path?: string
  game_executable?: string
  is_active: boolean
  members: LeagueMember[]
  available_clients: AvailableClient[]
  stats: LeagueStats
  nodelist?: NodelistInfo | null  // null until one has been generated
  // Immortal Barons: what members record with -coord-key; the problem is admin-only
  coordinator_key?: string | null
  coordinator_key_problem?: string | null
  game?: LeagueGame | null  // admins only; null if the hub has no game for the league
}

// The hub's own copy of the league's game
export interface LeagueGame {
  folder: string
  path: string | null
  state: 'provisioning' | 'installed' | 'ready' | 'failed'
  runs: boolean  // ready and the league enabled: the schedule runs it
  image: string | null
  preflight_ok: boolean | null  // null until preflight has run
  preflight_at: string | null
  preflight_problems: string[]
  // The latest setup (New League), an order in the league's reset history
  setup_id: number | null
  setup_status: 'applying' | 'applied' | 'failed' | null
  setup_error: string | null
  can_enable: boolean  // ready, and the latest preflight passed
}

export interface NodelistInfo {
  filename: string
  size: number
  modified_at: string
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
      error.value = apiError(err, 'Failed to load leagues')
    } finally {
      loading.value = false
    }
  }

  // The id last asked for. A page shows nothing rather than another one's
  // data while its own loads, and a slow answer for an id since left behind
  // is dropped rather than shown under the new one's URL (#124).
  let currentLeagueWanted: number | null = null

  async function loadLeague(id: number): Promise<void> {
    currentLeagueWanted = id
    if (currentLeague.value?.id !== id) currentLeague.value = null
    loading.value = true
    error.value = null

    try {
      const response = await leaguesApi.get(id)
      if (currentLeagueWanted === id) currentLeague.value = response.data
    } catch (err: unknown) {
      if (currentLeagueWanted === id) error.value = apiError(err, 'Failed to load league')
    } finally {
      if (currentLeagueWanted === id) loading.value = false
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
      error.value = apiError(err, 'Failed to create league')
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateLeague(id: number, data: {
    name?: string
    description?: string
    hub_fidonet_address?: string
    hub_routes_mail?: boolean
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
      error.value = apiError(err, 'Failed to update league')
      return false
    } finally {
      loading.value = false
    }
  }

  async function deleteLeague(id: number, confirmationName: string): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.delete(id, confirmationName)
      await loadLeagues()
      return true
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to delete league')
      return false
    } finally {
      loading.value = false
    }
  }

  // Generate the league's nodelist now. Returns what the hub said, or null
  // (with `error` set) if it would not write one.
  async function generateNodelist(leagueId: number):
      Promise<{ filename: string, members: number, changed: boolean, warnings: string[] } | null> {
    loading.value = true
    error.value = null

    try {
      const response = await leaguesApi.generateNodelist(leagueId)
      await loadLeague(leagueId)
      return response.data
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to generate nodelist')
      return null
    } finally {
      loading.value = false
    }
  }

  // Check the hub's game for the league now. Returns its game with the
  // result, or null (with `error` set) if the check could not be asked for.
  async function runPreflight(leagueId: number): Promise<LeagueGame | null> {
    error.value = null
    try {
      const response = await leaguesApi.runPreflight(leagueId)
      if (currentLeague.value?.id === leagueId) currentLeague.value.game = response.data
      return response.data
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to run preflight')
      return null
    }
  }

  async function addMember(leagueId: number, data: {
    client_id: number
    bbs_index: number
    ftn_address_id: number
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.addMember(leagueId, data)
      await loadLeague(leagueId)
      return true
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to add member')
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
      error.value = apiError(err, 'Failed to remove member')
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateMember(leagueId: number, membershipId: number, data: {
    bbs_index?: number
    ftn_address_id?: number
    board_key?: string
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await leaguesApi.updateMember(leagueId, membershipId, data)
      await loadLeague(leagueId)
      return true
    } catch (err: unknown) {
      error.value = apiError(err, 'Failed to update member')
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
    generateNodelist,
    runPreflight,
    addMember,
    removeMember,
    updateMember,
    clearError,
    clearCurrentLeague
  }
})
