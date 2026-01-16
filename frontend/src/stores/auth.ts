import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/services/api'

export interface User {
  id: number
  username: string
  is_admin: boolean
  created_at?: string
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
    // Actions
    login,
    logout,
    checkAuth,
    changePassword,
    clearError
  }
})
