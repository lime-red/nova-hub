<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { auditApi } from '@/services/api'
import AppLayout from '@/components/AppLayout.vue'

// Who changed what, newest first. Admin only.

interface AuditEvent {
  id: number
  at: string
  actor: string | null
  action: string
  target_type: string | null
  target_id: number | null
  target: string | null
  detail: string | null
  ip: string | null
}

const PAGE = 100

const ACTIONS = [
  { value: '', label: 'Everything' },
  { value: 'account.', label: 'Accounts and sign-ins' },
  { value: 'owner.', label: 'BBS owners' },
  { value: 'claim_link.', label: 'Claim links' },
  { value: 'relink_link.', label: 'Sign-in links' },
  { value: 'client.', label: 'BBSes' },
  { value: 'ftn_address.', label: 'FTN addresses' },
  { value: 'user.', label: 'Users and roles' },
]

const events = ref<AuditEvent[]>([])
const action = ref('')
const loading = ref(false)
const error = ref<string | null>(null)
const more = ref(false)

async function load(append = false) {
  loading.value = true
  error.value = null
  try {
    const before = append && events.value.length ? events.value[events.value.length - 1].id : undefined
    const page: AuditEvent[] = (
      await auditApi.list({ action: action.value || undefined, before_id: before, limit: PAGE })
    ).data
    events.value = append ? [...events.value, ...page] : page
    more.value = page.length === PAGE
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Could not load the audit log.'
  } finally {
    loading.value = false
  }
}

onMounted(() => load())

function targetLink(e: AuditEvent): string | null {
  if (e.target_type === 'client' && e.target_id) return `/clients/${e.target_id}`
  return null
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Audit Log</h1>
          <p class="text-muted">Who changed what, newest first</p>
        </div>
        <select v-model="action" @change="load()">
          <option v-for="a in ACTIONS" :key="a.value" :value="a.value">{{ a.label }}</option>
        </select>
      </header>

      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <div class="card">
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>What</th>
                <th>To</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!loading && events.length === 0">
                <td colspan="5" class="text-center text-muted" style="padding: 2rem;">Nothing yet</td>
              </tr>
              <tr v-for="e in events" :key="e.id">
                <td class="nowrap text-muted">{{ new Date(e.at).toLocaleString() }}</td>
                <td>
                  {{ e.actor || 'Someone signed out' }}
                  <div v-if="e.ip" class="text-muted small font-mono">{{ e.ip }}</div>
                </td>
                <td class="font-mono small">{{ e.action }}</td>
                <td>
                  <router-link v-if="targetLink(e)" :to="targetLink(e)!">{{ e.target }}</router-link>
                  <span v-else>{{ e.target || '-' }}</span>
                </td>
                <td class="small">{{ e.detail }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="load-more">
        <button v-if="more" class="btn btn-secondary" :disabled="loading" @click="load(true)">
          Older
        </button>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.nowrap {
  white-space: nowrap;
}

.small {
  font-size: 0.8125rem;
}

.load-more {
  display: flex;
  justify-content: center;
  margin-top: 1rem;
}
</style>
