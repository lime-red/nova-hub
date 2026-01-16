<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const isSubmitting = ref(false)

async function handleLogin() {
  if (isSubmitting.value) return

  isSubmitting.value = true
  authStore.clearError()

  const success = await authStore.login(username.value, password.value)

  if (success) {
    // Redirect to original destination or dashboard
    const redirect = route.query.redirect as string || '/dashboard'
    router.push(redirect)
  }

  isSubmitting.value = false
}
</script>

<template>
  <div class="login-page">
    <div class="login-container">
      <div class="login-card">
        <div class="login-header">
          <h1>Nova Hub</h1>
          <p class="text-muted">BBS Inter-League Routing System</p>
        </div>

        <form @submit.prevent="handleLogin" class="login-form">
          <div v-if="authStore.error" class="alert alert-error">
            {{ authStore.error }}
          </div>

          <div class="form-group">
            <label for="username">Username</label>
            <input
              id="username"
              v-model="username"
              type="text"
              placeholder="Enter your username"
              required
              autocomplete="username"
              :disabled="isSubmitting"
            />
          </div>

          <div class="form-group">
            <label for="password">Password</label>
            <input
              id="password"
              v-model="password"
              type="password"
              placeholder="Enter your password"
              required
              autocomplete="current-password"
              :disabled="isSubmitting"
            />
          </div>

          <button
            type="submit"
            class="btn btn-primary btn-lg w-full"
            :disabled="isSubmitting || !username || !password"
          >
            <span v-if="isSubmitting" class="spinner"></span>
            <span v-else>Sign In</span>
          </button>
        </form>
      </div>

      <p class="login-footer text-muted">
        Nova Hub v0.1.0
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
  padding: 1rem;
}

.login-container {
  width: 100%;
  max-width: 400px;
}

.login-card {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 2rem;
}

.login-header {
  text-align: center;
  margin-bottom: 2rem;
}

.login-header h1 {
  font-size: 1.75rem;
  color: var(--color-primary);
  margin-bottom: 0.5rem;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.form-group label {
  font-weight: 500;
  font-size: 0.875rem;
}

.login-footer {
  text-align: center;
  margin-top: 1.5rem;
  font-size: 0.75rem;
}

.spinner {
  width: 1.25rem;
  height: 1.25rem;
  border-color: rgba(255, 255, 255, 0.3);
  border-top-color: white;
}
</style>
