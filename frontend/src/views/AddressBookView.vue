<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import { addressBookApi } from '@/services/api'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

// Every BBS against every league: its index and address in each. A BBS keeps
// one index across leagues, so a row whose indices differ is flagged.

interface League {
  id: number
  full_id: string
  name: string
}

interface Entry {
  league_id: number
  bbs_index: number
  fidonet_address?: string | null
}

interface Bbs {
  id: number
  bbs_name: string
  is_active: boolean
  addresses: string[]
  memberships: Entry[]
  index_differs: boolean
}

const leagues = ref<League[]>([])
const bbses = ref<Bbs[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const flagged = computed(() => bbses.value.filter((b) => b.index_differs).length)

function entry(bbs: Bbs, leagueId: number): Entry | undefined {
  return bbs.memberships.find((m) => m.league_id === leagueId)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = (await addressBookApi.get()).data
    leagues.value = data.leagues
    bbses.value = data.bbses
  } catch (e: any) {
    error.value = e.response?.data?.detail || 'Failed to load the address book'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Address Book</h1>
          <p class="text-muted">Every BBS's index and FTN address in each active league</p>
        </div>
      </header>

      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <p>Loading...</p>
      </div>

      <div v-else-if="error" class="alert alert-error">
        {{ error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="load">Retry</button>
      </div>

      <template v-else>
        <div v-if="flagged" class="alert alert-warning mb-4">
          {{ flagged }} BBS{{ flagged === 1 ? '' : 'es' }} use a different index in different leagues.
          A BBS should keep one index everywhere.
        </div>

        <div class="card">
          <div class="card-body table-scroll" style="padding: 0;">
            <table class="table">
              <thead>
                <tr>
                  <th>BBS</th>
                  <th>Addresses</th>
                  <th v-for="league in leagues" :key="league.id" :title="league.name">
                    <router-link :to="`/leagues/${league.id}`">{{ league.full_id }}</router-link>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!bbses.length">
                  <td :colspan="leagues.length + 2" class="text-center text-muted" style="padding: 2rem;">
                    No BBSes yet
                  </td>
                </tr>
                <tr v-for="bbs in bbses" :key="bbs.id" :class="{ 'row-flagged': bbs.index_differs }">
                  <td>
                    <router-link v-if="authStore.canSeeClient(bbs.id)" :to="`/clients/${bbs.id}`">{{ bbs.bbs_name }}</router-link>
                    <template v-else>{{ bbs.bbs_name }}</template>
                    <span v-if="!bbs.is_active" class="badge badge-danger ml-2">Inactive</span>
                    <span v-if="bbs.index_differs" class="badge badge-warning ml-2">Index differs</span>
                  </td>
                  <td class="font-mono">
                    <div v-for="a in bbs.addresses" :key="a">{{ a }}</div>
                    <span v-if="!bbs.addresses.length" class="text-muted">None</span>
                  </td>
                  <td v-for="league in leagues" :key="league.id" class="font-mono cell">
                    <template v-if="entry(bbs, league.id)">
                      <div class="index">#{{ entry(bbs, league.id)!.bbs_index }}</div>
                      <div class="text-muted">{{ entry(bbs, league.id)!.fidonet_address || 'no address' }}</div>
                    </template>
                    <span v-else class="text-muted">-</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.5rem;
}

.table-scroll {
  overflow-x: auto;
}

.cell {
  white-space: nowrap;
  font-size: 0.875rem;
}

.index {
  font-weight: 600;
}

.row-flagged {
  background: var(--color-warning-bg, #fffbeb);
}

.ml-2 {
  margin-left: 0.5rem;
}
</style>
