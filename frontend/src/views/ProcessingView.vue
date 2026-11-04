<script setup lang="ts">
/**
 * Processing runs, newest first, a page at a time (#125).
 *
 * An IB league runs every 15 minutes, so a bare list fills up within hours.
 * The tiles say how the window went at a glance, and clicking one shows those
 * runs. The filters live in the URL, as on Movements and Attacks.
 */
import { computed, onMounted, ref, watch } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import FilterBar from '@/components/FilterBar.vue'
import { asBool, asNumber, asString, useQueryRef } from '@/composables/useQueryRef'
import { processingApi, apiError, type ProcessingRunSummary } from '@/services/api'
import type { ProcessingRun } from '@/stores/processing'
import { useProcessingStore } from '@/stores/processing'
import { useAuthStore } from '@/stores/auth'

const PAGE = 50

const processingStore = useProcessingStore()
const authStore = useAuthStore()

const days = useQueryRef<number>('days', 7, asNumber)
const leagueId = useQueryRef<number | undefined>('league', undefined, asNumber)
const status = useQueryRef<string>('status', '', asString)
const problems = useQueryRef<boolean>('problems', false, asBool)

const runs = ref<ProcessingRun[]>([])
const summary = ref<ProcessingRunSummary | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
const more = ref(false)
const error = ref<string | null>(null)

const filtersActive = computed(() => !!(leagueId.value || status.value || problems.value))

function params(beforeId?: number) {
  return {
    limit: PAGE,
    before_id: beforeId,
    league_id: leagueId.value,
    status: status.value || undefined,
    problems: problems.value || undefined,
    days: days.value || undefined,
  }
}

// A slower answer for filters since changed is dropped, not shown under the new ones.
let generation = 0

async function load() {
  const mine = ++generation
  loading.value = true
  error.value = null
  try {
    const window = { league_id: leagueId.value, days: days.value || undefined }
    const [list, sum] = await Promise.all([
      processingApi.listRuns(params()),
      processingApi.summary(window),
    ])
    if (mine !== generation) return
    runs.value = list.data
    more.value = list.data.length === PAGE
    summary.value = sum.data
  } catch (err: unknown) {
    if (mine === generation) error.value = apiError(err, 'Failed to load processing runs')
  } finally {
    if (mine === generation) loading.value = false
  }
}

async function loadMore() {
  const last = runs.value[runs.value.length - 1]
  if (!last) return
  const mine = generation
  loadingMore.value = true
  try {
    const { data } = await processingApi.listRuns(params(last.id))
    if (mine !== generation) return
    runs.value = [...runs.value, ...data]
    more.value = data.length === PAGE
  } catch (err: unknown) {
    if (mine === generation) error.value = apiError(err, 'Failed to load more runs')
  } finally {
    loadingMore.value = false
  }
}

/** A tile shows its runs; clicking the one already shown goes back to all. */
function pick(next: { status?: string; problems?: boolean }) {
  const same = (next.status ?? '') === status.value && !!next.problems === problems.value
  status.value = same ? '' : next.status ?? ''
  problems.value = same ? false : !!next.problems
}

function clearFilters() {
  leagueId.value = undefined
  status.value = ''
  problems.value = false
}

function statusClass(s: string): string {
  switch (s) {
    case 'completed':
      return 'badge-success'
    case 'running':
      return 'badge-info'
    case 'failed':
      return 'badge-danger'
    default:
      return 'badge-secondary'
  }
}

async function handleTrigger() {
  if (confirm('Trigger packet processing now?')) {
    if (await processingStore.triggerProcessing()) await load()
  }
}

onMounted(load)
watch([days, leagueId, status, problems], load)
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Processing Runs</h1>
          <p class="text-muted">Game processing history and logs</p>
        </div>
        <div class="header-actions">
          <button class="btn btn-secondary" :disabled="loading" @click="load">
            {{ loading ? 'Refreshing...' : 'Refresh' }}
          </button>
          <button
            v-if="authStore.isAdmin"
            class="btn btn-primary"
            @click="handleTrigger"
            :disabled="processingStore.loading"
          >
            Trigger Processing
          </button>
        </div>
      </header>

      <FilterBar
        v-model:days="days"
        v-model:league-id="leagueId"
        :day-options="[1, 7, 30, 90, 0]"
        window-label="Started"
        :active="filtersActive"
        @clear="clearFilters"
      >
        <select v-model="status" class="form-select" aria-label="Status">
          <option value="">Any status</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>
        <label class="check">
          <input v-model="problems" type="checkbox" />
          With problems
        </label>
      </FilterBar>

      <div v-if="summary" class="tiles">
        <button class="card tile" :class="{ on: !status && !problems }" @click="pick({})">
          <span class="tile-n">{{ summary.total }}</span>
          <span class="text-muted">runs</span>
        </button>
        <button class="card tile" :class="{ on: status === 'completed' && !problems }"
                @click="pick({ status: 'completed' })">
          <span class="tile-n">{{ summary.completed }}</span>
          <span class="text-muted">completed</span>
        </button>
        <button class="card tile" :class="{ on: status === 'failed' && !problems, 'tile-bad': summary.failed > 0 }"
                @click="pick({ status: 'failed' })">
          <span class="tile-n">{{ summary.failed }}</span>
          <span class="text-muted">failed</span>
        </button>
        <button class="card tile" :class="{ on: status === 'running' && !problems }"
                @click="pick({ status: 'running' })">
          <span class="tile-n">{{ summary.running }}</span>
          <span class="text-muted">running</span>
        </button>
        <button class="card tile" :class="{ on: problems && !status, 'tile-bad': summary.problems > 0 }"
                title="Failed, or left packets unconsumed or quarantined"
                @click="pick({ problems: true })">
          <span class="tile-n">{{ summary.problems }}</span>
          <span class="text-muted">with problems</span>
        </button>
      </div>

      <div v-if="error" class="alert alert-error">
        {{ error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="load">Retry</button>
      </div>

      <div v-else-if="loading && runs.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading processing runs...</p>
      </div>

      <div v-else class="card">
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Started</th>
                <th>Duration</th>
                <th>League</th>
                <th>Packets</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="runs.length === 0">
                <td colspan="7" class="text-center text-muted" style="padding: 2rem;">
                  {{ filtersActive ? 'No runs match these filters' : 'No processing runs in this window' }}
                </td>
              </tr>
              <tr v-for="run in runs" :key="run.id">
                <td class="font-mono">#{{ run.id }}</td>
                <td class="text-muted">{{ run.started_at }}</td>
                <td class="font-mono">{{ run.duration || '-' }}</td>
                <td>
                  <template v-for="(name, i) in run.leagues" :key="name">
                    <template v-if="i">, </template>
                    <router-link v-if="run.league_ids?.[i]" :to="`/leagues/${run.league_ids[i]}`">{{ name }}</router-link>
                    <template v-else>{{ name }}</template>
                  </template>
                  <template v-if="run.leagues.length === 0">-</template>
                </td>
                <td>
                  {{ run.packets_processed }}
                  <span v-if="run.packets_unconsumed" class="badge badge-warning"
                        title="Handed to the game but not ingested; retried next run">
                    {{ run.packets_unconsumed }} unconsumed
                  </span>
                  <span v-if="run.packets_quarantined" class="badge badge-warning"
                        title="Files the game left that were not its own league's">
                    {{ run.packets_quarantined }} quarantined
                  </span>
                </td>
                <td>
                  <span class="badge" :class="statusClass(run.status)">
                    {{ run.status }}
                  </span>
                </td>
                <td class="actions">
                  <router-link :to="`/processing/${run.id}`" class="btn btn-sm btn-secondary">
                    View
                  </router-link>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="more && !error" class="more">
        <button class="btn btn-secondary" :disabled="loadingMore" @click="loadMore">
          {{ loadingMore ? 'Loading...' : 'Load more' }}
        </button>
      </div>
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
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  gap: 1rem;
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.check {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin: 0;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.tile {
  display: flex;
  flex-direction: column;
  padding: 0.9rem 1rem;
  text-align: left;
  font: inherit;
  color: inherit;
  cursor: pointer;
  border: 1px solid var(--color-border);
}

.tile.on {
  border-color: var(--color-primary);
}

.tile-n {
  font-size: 1.6rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.tile-bad .tile-n {
  color: var(--color-danger);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}

.more {
  display: flex;
  justify-content: center;
  margin-top: 1rem;
}

.mt-2 {
  margin-top: 0.5rem;
}
</style>
