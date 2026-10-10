import { defineStore } from 'pinia'
import { ref } from 'vue'
import { clientsApi } from '@/services/api'

export interface Client {
  id: number
  bbs_name: string
  client_id: string
  city?: string
  state?: string
  country?: string
  is_active: boolean
  last_seen?: string
  packets_sent_24h: number
  packets_received_24h: number
}

export interface ClientDetail {
  id: number
  bbs_name: string
  client_id: string
  city?: string
  state?: string
  country?: string
  is_active: boolean
  created_at?: string
  stats: {
    last_seen?: string
    total_sent: number
    sent_24h: number
    total_received: number
    received_24h: number
  }
  packets: PacketHistoryItem[]
  league_memberships: LeagueMembershipInfo[]
  ftn_addresses: FtnAddressInfo[]
  owners?: OwnerInfo[]
}

// An FTN address this BBS holds, and the leagues whose membership uses it.
// A sysop who runs this BBS. email is only filled in for admins.
export interface OwnerInfo {
  user_id: number
  username: string
  full_name?: string | null
  email?: string | null
}

export interface FtnAddressInfo {
  id: number
  address: string
  leagues: string[]
}

export interface PacketHistoryItem {
  filename: string
  direction: string
  league_name: string
  source: string
  dest: string
  timestamp: string
  processed_at?: string
  retrieved_at?: string
  processing_run_id?: number
}

export interface LeagueMembershipInfo {
  league_id: number
  league_name: string
  full_id: string
  bbs_index: number
  fidonet_address?: string
  nodelist_filename?: string | null  // null until the league has a nodelist
}

export interface ClientCreated {
  id: number
  bbs_name: string
  client_id: string
  client_secret: string
}

export const useClientsStore = defineStore('clients', () => {
  // State
  const clients = ref<Client[]>([])
  const currentClient = ref<ClientDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Actions
  async function loadClients(): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await clientsApi.list()
      clients.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load clients'
    } finally {
      loading.value = false
    }
  }

  async function loadClient(id: number): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await clientsApi.get(id)
      currentClient.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load client'
    } finally {
      loading.value = false
    }
  }

  async function createClient(bbsName: string, clientId: string): Promise<ClientCreated | null> {
    loading.value = true
    error.value = null

    try {
      const response = await clientsApi.create({ bbs_name: bbsName, client_id: clientId })
      await loadClients()
      return response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to create client'
      return null
    } finally {
      loading.value = false
    }
  }

  async function updateClient(id: number, data: {
    bbs_name?: string
    city?: string
    state?: string
    country?: string
    is_active?: boolean
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await clientsApi.update(id, data)
      await loadClients()
      if (currentClient.value?.id === id) {
        await loadClient(id)
      }
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to update client'
      return false
    } finally {
      loading.value = false
    }
  }

  async function deleteClient(id: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await clientsApi.delete(id)
      await loadClients()
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to delete client'
      return false
    } finally {
      loading.value = false
    }
  }

  async function regenerateSecret(id: number): Promise<string | null> {
    loading.value = true
    error.value = null

    try {
      const response = await clientsApi.regenerateSecret(id)
      return response.data.client_secret
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to regenerate secret'
      return null
    } finally {
      loading.value = false
    }
  }

  // FTN addresses (admin only). Each reloads the client so the list and its
  // league usage stay as the server sees them.
  async function changeFtnAddress(
    id: number,
    call: () => Promise<unknown>,
    failure: string
  ): Promise<boolean> {
    error.value = null
    try {
      await call()
      await loadClient(id)
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || failure
      return false
    }
  }

  function addFtnAddress(id: number, address: string): Promise<boolean> {
    return changeFtnAddress(id, () => clientsApi.addFtnAddress(id, address), 'Failed to assign address')
  }

  function updateFtnAddress(id: number, addressId: number, address: string): Promise<boolean> {
    return changeFtnAddress(
      id, () => clientsApi.updateFtnAddress(id, addressId, address), 'Failed to change address'
    )
  }

  function deleteFtnAddress(id: number, addressId: number): Promise<boolean> {
    return changeFtnAddress(id, () => clientsApi.deleteFtnAddress(id, addressId), 'Failed to remove address')
  }

  function clearError(): void {
    error.value = null
  }

  function clearCurrentClient(): void {
    currentClient.value = null
  }

  return {
    // State
    clients,
    currentClient,
    loading,
    error,
    // Actions
    loadClients,
    loadClient,
    createClient,
    updateClient,
    deleteClient,
    regenerateSecret,
    addFtnAddress,
    updateFtnAddress,
    deleteFtnAddress,
    clearError,
    clearCurrentClient
  }
})
