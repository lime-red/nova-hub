<script setup lang="ts">
// Every reset of a league, newest first: the newest two, which are the one
// under way or the current game's and the one before, with the rest behind a
// count. A row opens into what it changed from the reset before it and, with
// `inline`, its steps and editor log too, polled while it runs: the live run
// and a past one look the same, in the same place. Without `inline` (the
// league page) a console reset's row offers a link to the reset page instead.
// Resets only seen in a Game Start packet (BRE) carry every setting but the
// start date, decoded from it.
import { computed, onMounted, ref, watch } from 'vue'
import ResetProgress from '@/components/ResetProgress.vue'
import { blankResetValue, resetSignal } from '@/games'
import {
  apiError,
  gameResetApi,
  type ResetField,
  type ResetSummary,
  type ResetValue
} from '@/services/api'

const props = withDefaults(
  defineProps<{
    leagueId: number
    fields: ResetField[]
    refreshKey: number
    inline?: boolean
    openId?: number | null
    gameType?: string
  }>(),
  { inline: false, openId: null, gameType: 'B' }
)
const signal = computed(() => resetSignal(props.gameType))
const Signal = computed(() => signal.value[0].toUpperCase() + signal.value.slice(1))
const emit = defineEmits<{ open: [resetId: number]; finished: [] }>()

const SHOWN = 2

const rows = ref<ResetSummary[]>([])
const loading = ref(true)
const error = ref('')
const expanded = ref<number | null>(null)
const showAll = ref(false)

const visible = computed(() => (showAll.value ? rows.value : rows.value.slice(0, SHOWN)))
const hidden = computed(() => Math.max(rows.value.length - SHOWN, 0))

// Reloads leave the table in place, so an open row's progress keeps running.
async function load() {
  try {
    rows.value = (await gameResetApi.history(props.leagueId)).data
    error.value = ''
    reveal(props.openId)
  } catch (e) {
    error.value = apiError(e, 'Could not load the reset history')
  } finally {
    loading.value = false
  }
}

function reveal(id: number | null) {
  if (id === null) return
  const i = rows.value.findIndex((r) => r.id === id)
  if (i < 0) return
  expanded.value = id
  if (i >= SHOWN) showAll.value = true
}

function display(f: ResetField, v: ResetValue | undefined): string {
  if (v === null || v === undefined || v === '') return blankResetValue(f.id, f.kind)
  if (f.kind === 'toggle') return v ? 'Enabled' : 'Disabled'
  if (f.kind === 'choice') return f.choices.find((c) => c.letter === v)?.name ?? String(v)
  if (f.kind === 'number') return Number(v).toLocaleString()
  return String(v)
}

// Against the applied reset before it; the first one ever has nothing to
// compare with, so it lists what it set.
function diff(row: ResetSummary) {
  const i = rows.value.indexOf(row)
  const before = rows.value.slice(i + 1).find((r) => r.status === 'applied')
  return props.fields
    .filter((f) => f.id in row.settings)
    .filter((f) => !before || !(f.id in before.settings) || before.settings[f.id] !== row.settings[f.id])
    .map((f) => ({
      label: f.label,
      from: before && f.id in before.settings ? display(f, before.settings[f.id]) : '',
      to: display(f, row.settings[f.id])
    }))
}

function when(iso: string | null): string {
  return iso ? iso.replace('T', ' ').slice(0, 16) : ''
}

function onFinished() {
  load()
  emit('finished')
}

watch(() => props.refreshKey, load)
watch(() => props.openId, reveal)
onMounted(load)
</script>

<template>
  <section class="card mb-4">
    <div class="card-header">
      <h3>Reset history</h3>
      <span v-if="rows.length" class="text-muted small">
        {{ rows.length }} {{ rows.length === 1 ? 'reset' : 'resets' }}
      </span>
    </div>
    <div class="card-body history">
      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <p v-else-if="loading" class="text-muted">Loading...</p>
      <p v-else-if="!rows.length" class="text-muted">
        No resets yet: none from the console, and no {{ signal }} in the packets the hub holds.
      </p>
      <template v-else>
        <table class="table">
          <thead>
            <tr>
              <th>Game start</th>
              <th>Ended</th>
              <th>By</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in visible" :key="r.id">
              <tr class="row" :class="{ open: expanded === r.id }" @click="expanded = expanded === r.id ? null : r.id">
                <td>{{ when(r.game_start) || `performed ${when(r.applied_at)}` }}</td>
                <td>
                  <span v-if="r.status !== 'applied'" class="text-muted">—</span>
                  <span v-else-if="r.ended">{{ when(r.ended) }}</span>
                  <span v-else class="badge badge-info">current</span>
                </td>
                <td>
                  <template v-if="r.source === 'observed'">
                    <span class="text-muted" :title="`Seen in a ${signal} packet, not performed from the console`">
                      seen in packets
                    </span>
                  </template>
                  <template v-else>{{ r.applied_by }}</template>
                </td>
                <td>
                  <span v-if="r.status === 'applying'" class="badge badge-info">under way</span>
                  <span v-else-if="r.status === 'failed'" class="badge badge-danger">failed</span>
                  <span v-else-if="r.confirmed === false" class="badge badge-danger">{{ Signal }} disagrees</span>
                  <span v-else-if="r.confirmed" class="badge badge-success">confirmed</span>
                  <span v-else-if="r.source === 'console'" class="badge badge-warning">awaiting {{ signal }}</span>
                  <span v-else class="badge badge-success">applied</span>
                </td>
                <td class="toggle"><span class="chevron" :class="{ open: expanded === r.id }" aria-hidden="true"></span></td>
              </tr>
              <tr v-if="expanded === r.id" class="detail">
                <td colspan="5">
                  <ResetProgress
                    v-if="inline && r.source === 'console'"
                    :league-id="leagueId"
                    :reset-id="r.id"
                    :game-type="gameType"
                    embedded
                    @finished="onFinished"
                  />
                  <p v-else-if="r.error" class="alert alert-error">{{ r.error }}</p>
                  <p v-if="r.notes" class="notes"><strong>Notes:</strong> {{ r.notes }}</p>
                  <h4>What it set</h4>
                  <table class="table diff">
                    <thead>
                      <tr><th>Setting</th><th>Before</th><th>This reset</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="d in diff(r)" :key="d.label">
                        <td>{{ d.label }}</td>
                        <td class="text-muted">{{ d.from }}</td>
                        <td><strong>{{ d.to }}</strong></td>
                      </tr>
                      <tr v-if="!diff(r).length">
                        <td colspan="3" class="text-muted">The same settings as the reset before.</td>
                      </tr>
                    </tbody>
                  </table>
                  <button
                    v-if="!inline && r.source === 'console'"
                    class="btn btn-secondary btn-sm"
                    @click.stop="emit('open', r.id)"
                  >
                    Progress and {{ gameType === 'I' ? "the game's" : 'editor' }} log
                  </button>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        <button v-if="hidden" class="btn btn-secondary btn-sm more" @click="showAll = !showAll">
          {{ showAll ? 'Hide' : 'Show' }} {{ hidden }} earlier {{ hidden === 1 ? 'reset' : 'resets' }}
        </button>
      </template>
    </div>
  </section>
</template>

<style scoped>
.card-header {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.small {
  font-size: 0.85rem;
}

.history {
  overflow-x: auto;
}

.row {
  cursor: pointer;
}

.row.open > td {
  background: var(--color-bg);
}

.toggle {
  width: 1.5rem;
}

/* Points right when shut, down when open. */
.chevron {
  display: inline-block;
  width: 0.5rem;
  height: 0.5rem;
  border-right: 2px solid var(--color-text-muted);
  border-bottom: 2px solid var(--color-text-muted);
  transform: rotate(-45deg);
  transition: transform 0.15s ease;
}

.chevron.open {
  transform: rotate(45deg);
}

.detail > td {
  background: var(--color-bg);
}

h4 {
  margin: 1rem 0 0.25rem;
  font-size: 0.95rem;
}

.diff td,
.diff th {
  font-size: 0.85rem;
}

.notes {
  margin: 0.75rem 0 0;
  white-space: pre-wrap;
  font-size: 0.875rem;
}

.more {
  margin-top: 0.75rem;
}
</style>
