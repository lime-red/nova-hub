import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/services/api'

export interface User {
  id: number
  username: string
  is_admin: boolean
  email?: string | null
  full_name?: string | null
  has_password?: boolean // can use the local login
  sso_linked?: boolean // can sign in through the identity provider
  owned_clients?: { id: number; bbs_name: string }[]
  created_at?: string | null
  last_login?: string | null
}

export const useAuthStore = defineStore('auth', () => {
  // State
  const user = ref<User | null>(null)
  const initialized = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Getters
  const isAuthenticated = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.is_admin ?? false)
  const username = computed(() => user.value?.username ?? '')
  // Whether this user can open a BBS's page: admins any, a sysop their own.
  // (The server enforces it; this only avoids links that lead to a 404.)
  const ownedClientIds = computed(() => new Set((user.value?.owned_clients ?? []).map((c) => c.id)))
  function canSeeClient(clientId: number): boolean {
    return isAdmin.value || ownedClientIds.value.has(clientId)
  }

  // Actions
  async function login(usernameInput: string, password: string): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      const response = await authApi.login(usernameInput, password)
      user.value = response.data.user
      initialized.value = true
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Login failed'
      return false
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // Ignore errors on logout
    } finally {
      user.value = null
    }
  }

  async function checkAuth(): Promise<void> {
    if (initialized.value) return

    try {
      const response = await authApi.me()
      user.value = response.data
    } catch {
      user.value = null
    } finally {
      initialized.value = true
    }
  }

  async function changePassword(
    currentPassword: string,
    newPassword: string,
    confirmPassword: string
  ): Promise<boolean> {
    loading.value = true
    error.value = null

    try {
      await authApi.changePassword(currentPassword, newPassword, confirmPassword)
      return true
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string } } }
      error.value = axiosError.response?.data?.detail || 'Password change failed'
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
    user,
    initialized,
    loading,
    error,
    // Getters
    isAuthenticated,
    isAdmin,
    username,
    canSeeClient,
    // Actions
    login,
    logout,
    checkAuth,
    changePassword,
    clearError
  }
})
