import { defineStore } from 'pinia'
import { ref } from 'vue'
import { usersApi } from '@/services/api'

export interface User {
  id: number
  username: string
  is_admin: boolean
  created_at?: string
}

export const useUsersStore = defineStore('users', () => {
  // State
  const users = ref<User[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Actions
  async function loadUsers(): Promise<void> {
    loading.value = true
    error.value = null

    try {
      const response = await usersApi.list()
      users.value = response.data
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to load users'
    } finally {
      loading.value = false
    }
  }

  async function createUser(data: {
    username: string
    password: string
    is_admin?: boolean
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await usersApi.create(data)
      await loadUsers()
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to create user'
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateUser(id: number, data: {
    username?: string
    password?: string
    is_admin?: boolean
  }): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await usersApi.update(id, data)
      await loadUsers()
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to update user'
      return false
    } finally {
      loading.value = false
    }
  }

  async function deleteUser(id: number): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await usersApi.delete(id)
      await loadUsers()
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Failed to delete user'
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
    users,
    loading,
    error,
    // Actions
    loadUsers,
    createUser,
    updateUser,
    deleteUser,
    clearError
  }
})
