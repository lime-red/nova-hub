<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { authApi, relinkApi } from '@/services/api'

// Connecting a sign-in to an existing hub account. Public: the link names
// the account. Opening it only looks; it is used by signing in through it,
// which binds whoever signs in to that account.

interface RelinkStatus {
  username: string
  state: 'ready' | 'used' | 'superseded' | 'expired'
  expires_at: string
  has_sign_in: boolean
}

const route = useRoute()
const token = computed(() => String(route.params.token))

const loading = ref(true)
const notFound = ref(false)
const error = ref<string | null>(null)
const status = ref<RelinkStatus | null>(null)

onMounted(async () => {
  try {
    status.value = (await relinkApi.status(token.value)).data
  } catch (e: any) {
    if (e.response?.status === 404 || e.response?.status === 422) {
      notFound.value = true
    } else {
      error.value = e.response?.data?.detail || 'The hub could not be reached.'
    }
  } finally {
    loading.value = false
  }
})

const signInUrl = computed(() => authApi.ssoStartUrl('/dashboard', token.value))

const STATES: Record<string, string> = {
  used: 'This link has already been used.',
  superseded: 'A newer link has been issued for this account, so this one no longer works.',
  expired: 'This link has expired.',
}

function when(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : ''
}
</script>

<template>
  <div class="relink-page">
    <div class="relink-card">
      <h1>Nova Hub</h1>

      <p v-if="loading" class="text-muted">Checking your link...</p>

      <template v-else-if="notFound">
        <h2>This link is not valid</h2>
        <p>Check you copied all of it. If it still does not work, ask the hub admin for a new one.</p>
      </template>

      <div v-else-if="error" class="alert alert-error">{{ error }}</div>

      <template v-else-if="status && status.state !== 'ready'">
        <h2>This link cannot be used</h2>
        <p>{{ STATES[status.state] }} Ask the hub admin for a new one if you still need it.</p>
      </template>

      <template v-else-if="status">
        <h2>Connect your sign-in to {{ status.username }}</h2>
        <p>
          Sign in, and from then on that sign-in opens the hub account
          <strong>{{ status.username }}</strong>.
          <template v-if="status.has_sign_in">
            It replaces the sign-in the account has now.
          </template>
        </p>
        <p class="text-muted">
          Use the sign-in you want to keep using. This link works once, until
          {{ when(status.expires_at) }}.
        </p>
        <a :href="signInUrl" class="btn btn-primary btn-lg">Sign in to connect</a>
      </template>
    </div>
  </div>
</template>

<style scoped>
.relink-page {
  min-height: 100vh;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
  padding: 3rem 1rem;
}

.relink-card {
  width: 100%;
  max-width: 560px;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: 2rem;
}

.relink-card h1 {
  font-size: 1.25rem;
  color: var(--color-primary);
  margin-bottom: 1rem;
}

.relink-card h2 {
  font-size: 1.5rem;
  margin-bottom: 1rem;
}

.relink-card p {
  margin-bottom: 1rem;
}
</style>
