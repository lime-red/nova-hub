<script setup lang="ts">
/**
 * What moved between nodes.
 *
 * The page is deliberately a reading instrument rather than a verdict. What a
 * healthy week looks like depends on how many boards are playing and how busy
 * their players are, which the hub cannot know and the sysop does -- so this
 * shows the traffic and gets out of the way.
 *
 * The summary comes first because the shape is what you read at a glance: which
 * item types are flowing, which boards are talking to which, and whether the
 * daily rhythm changed. The table underneath is for when the shape prompts a
 * question.
 */
import { computed, onMounted, ref, watch } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import FilterBar from '@/components/FilterBar.vue'
import { asNumber, asString, useQueryRef } from '@/composables/useQueryRef'
import { useLeagues } from '@/composables/useLeagues'
import { movementsApi, type Movement, type MovementSummary } from '@/services/api'

const loading = ref(false)
const error = ref<string | null>(null)
const movements = ref<Movement[]>([])
const summary = ref<MovementSummary | null>(null)
const { leagueLabel } = useLeagues()

// In the URL, so Back keeps them and a filtered view can be linked (#125).
const days = useQueryRef<number>('days', 7, asNumber)
const leagueId = useQueryRef<number | undefined>('league', undefined, asNumber)
const direction = useQueryRef<'in' | 'out' | undefined>('direction', undefined, {
  parse: (raw) => (raw === 'in' || raw === 'out' ? raw : undefined),
  format: (v) => v,
})
const itemType = useQueryRef<string | undefined>('type', undefined, asString)
const node = useQueryRef<number | undefined>('node', undefined, asNumber)

/** Item types actually seen in the window, so the filter offers only real ones. */
const knownTypes = computed(() => {
  const seen = new Set<string>()
  summary.value?.by_type.forEach((t) => seen.add(t.item_type))
  return Array.from(seen).sort()
})

/** Busiest pair first, so the bars have a sensible scale. */
const busiestPair = computed(() =>
  Math.max(1, ...(summary.value?.by_pair.map((p) => p.count) ?? [1]))
)
const busiestDay = computed(() =>
  Math.max(1, ...(summary.value?.by_day.map((d) => d.count) ?? [1]))
)

async function load() {
  loading.value = true
  error.value = null
  try {
    const filters = {
      days: days.value,
      league_id: leagueId.value,
      direction: direction.value,
      item_type: itemType.value,
      node: typeof node.value === 'number' ? node.value : undefined,
      limit: 500,
    }
    const [list, sum] = await Promise.all([
      movementsApi.list(filters),
      movementsApi.summary({ days: days.value, league_id: leagueId.value }),
    ])
    movements.value = list.data
    summary.value = sum.data
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Could not load movements'
  } finally {
    loading.value = false
  }
}

function clearFilters() {
  direction.value = undefined
  itemType.value = undefined
  node.value = undefined
  leagueId.value = undefined
}

const filtersActive = computed(
  () => !!(direction.value || itemType.value || typeof node.value === 'number' || leagueId.value)
)

function shortTime(stamp: string | null): string {
  if (!stamp) return '-'
  return stamp.replace('T', ' ').slice(0, 19)
}

onMounted(load)

watch([days, leagueId, direction, itemType, node], load)
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Movements</h1>
          <p class="text-muted">
            What the games exchanged between nodes, from each run's detailed output
          </p>
        </div>
      </header>

      <FilterBar
        v-model:days="days"
        v-model:league-id="leagueId"
        :active="filtersActive"
        @clear="clearFilters"
      >
        <select v-model="direction" class="form-select" aria-label="Direction">
          <option :value="undefined">Both directions</option>
          <option value="in">Incoming</option>
          <option value="out">Outgoing</option>
        </select>
        <select v-model="itemType" class="form-select" aria-label="Item type">
          <option :value="undefined">All types</option>
          <option v-for="t in knownTypes" :key="t" :value="t">{{ t }}</option>
        </select>
        <input
          v-model.number="node"
          type="number"
          min="1"
          class="form-input narrow"
          placeholder="Node"
          title="Matches either end of the transfer"
        />
      </FilterBar>

      <div v-if="error" class="alert alert-error">
        {{ error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="load">Retry</button>
      </div>

      <div v-else-if="loading && !summary" class="loading-state">
        <div class="spinner"></div>
        <p>Loading movements...</p>
      </div>

      <template v-else-if="summary">
        <!-- Nothing at all: say why, rather than showing empty tables. -->
        <div v-if="summary.total === 0" class="card empty-state">
          <div class="card-body">
            <h2>No movements recorded in this window</h2>
            <p class="text-muted">
              Movements are read from each processing run's detailed output. A new hub,
              a quiet league, or a window shorter than the gap between runs will all
              look like this. Runs processed before this feature was added have no
              movement records.
            </p>
          </div>
        </div>

        <template v-else>
          <div class="summary-grid">
            <div class="card">
              <div class="card-header"><h2>By item type</h2></div>
              <div class="card-body">
                <table class="table compact">
                  <tbody>
                    <tr
                      v-for="t in summary.by_type"
                      :key="`${t.direction}-${t.item_type}`"
                      class="clickable"
                      @click="direction = t.direction as 'in' | 'out'; itemType = t.item_type"
                    >
                      <td>
                        <span class="badge" :class="t.direction === 'in' ? 'badge-info' : 'badge-secondary'">
                          {{ t.direction }}
                        </span>
                      </td>
                      <td>{{ t.item_type }}</td>
                      <td class="font-mono text-right">{{ t.count }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="card">
              <div class="card-header"><h2>Between nodes</h2></div>
              <div class="card-body">
                <div v-for="p in summary.by_pair" :key="`${p.src_node}-${p.dst_node}`" class="bar-row">
                  <span class="font-mono pair">{{ p.src_node ?? '?' }} &rarr; {{ p.dst_node ?? '?' }}</span>
                  <span class="bar">
                    <span class="bar-fill" :style="{ width: `${(p.count / busiestPair) * 100}%` }"></span>
                  </span>
                  <span class="font-mono count">{{ p.count }}</span>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-header"><h2>Per day</h2></div>
              <div class="card-body">
                <div v-for="d in summary.by_day" :key="d.day" class="bar-row">
                  <span class="font-mono pair">{{ d.day }}</span>
                  <span class="bar">
                    <span class="bar-fill" :style="{ width: `${(d.count / busiestDay) * 100}%` }"></span>
                  </span>
                  <span class="font-mono count">{{ d.count }}</span>
                </div>
              </div>
            </div>
          </div>

          <!--
            Not a warning. A board can be idle for good reasons; this only saves
            reading down the table to work out who is missing.
          -->
          <div v-if="summary.quiet_nodes.length" class="card quiet">
            <div class="card-body">
              <strong>Nothing exchanged with
                node<span v-if="summary.quiet_nodes.length > 1">s</span>
                {{ summary.quiet_nodes.join(', ') }}</strong>
              in this window, though there has been traffic before.
              That may be perfectly normal.
            </div>
          </div>

          <div class="card">
            <div class="card-header filter-bar">
              <h2>
                {{ movements.length }} movement<span v-if="movements.length !== 1">s</span>
                <span class="text-muted" v-if="movements.length === 500"> (showing the most recent 500)</span>
              </h2>
            </div>
            <div class="card-body" style="padding: 0;">
              <table class="table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>League</th>
                    <th>Direction</th>
                    <th>Type</th>
                    <th>Route</th>
                    <th class="text-right">Size</th>
                    <th>Run</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="movements.length === 0">
                    <td colspan="7" class="text-center text-muted" style="padding: 2rem;">
                      Nothing matches these filters
                    </td>
                  </tr>
                  <tr v-for="m in movements" :key="m.id">
                    <td class="text-muted font-mono">{{ shortTime(m.occurred_at) }}</td>
                    <td>{{ leagueLabel(m.league_id, m.league_name) }}</td>
                    <td>
                      <span class="badge" :class="m.direction === 'in' ? 'badge-info' : 'badge-secondary'">
                        {{ m.direction }}
                      </span>
                    </td>
                    <td>{{ m.item_type }}</td>
                    <td class="font-mono">{{ m.src_node ?? '?' }} &rarr; {{ m.dst_node ?? '?' }}</td>
                    <td class="font-mono text-right">{{ m.size_after ?? '-' }}</td>
                    <td>
                      <router-link :to="`/processing/${m.processing_run_id}`" class="run-link">
                        #{{ m.processing_run_id }}
                      </router-link>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
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
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  gap: 1rem;
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.window-picker {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.empty-state .card-body {
  padding: 2.5rem;
  text-align: center;
}

.empty-state h2 {
  margin-bottom: 0.5rem;
}

.empty-state p {
  max-width: 34rem;
  margin: 0 auto;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.summary-grid .card-body {
  padding: 0.5rem 1rem 1rem;
}

.table.compact td {
  padding: 0.3rem 0.4rem;
}

.clickable {
  cursor: pointer;
}

.clickable:hover {
  background: var(--color-background-mute, rgba(127, 127, 127, 0.12));
}

.bar-row {
  display: grid;
  grid-template-columns: 6.5rem 1fr 3rem;
  align-items: center;
  gap: 0.6rem;
  padding: 0.18rem 0;
  font-size: 0.85rem;
}

.bar {
  background: var(--color-background-mute, rgba(127, 127, 127, 0.15));
  border-radius: 3px;
  height: 0.7rem;
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 100%;
  background: var(--color-primary, #3b82f6);
}

.count,
.text-right {
  text-align: right;
}

.quiet {
  margin-bottom: 1rem;
}

.quiet .card-body {
  padding: 0.85rem 1rem;
}

.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.filters {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.node-input {
  width: 6rem;
}

.run-link {
  font-family: var(--font-mono, monospace);
}
</style>
