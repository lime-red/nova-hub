<script setup lang="ts">
// An Immortal Barons league as the hub's own game, the League Coordinator,
// sees it: each board's version, rules and round trip; the freeze, with each
// board's quiet report as it comes in; the packets the game is holding and
// why; and its own strikes and trades still waiting for an answer. Freezing
// and thawing the league start here, and run as a reset does (ResetProgress).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import CopyTsvButton from '@/components/CopyTsvButton.vue'
import ResetProgress from '@/components/ResetProgress.vue'
import { apiError, gameResetApi, type IbGameState } from '@/services/api'

const props = defineProps<{ leagueId: number; fullId: string }>()
const emit = defineEmits<{ ordered: [] }>()

const state = ref<IbGameState | null>(null)
const error = ref('')
const asking = ref<'freeze' | 'thaw' | null>(null)
const message = ref('')
const typed = ref('')
const busy = ref(false)
const orderError = ref('')
const orderId = ref<number | null>(null)   // the order started here, shown until dismissed
const boardsEl = ref<HTMLElement | null>(null)
const heldEl = ref<HTMLElement | null>(null)
const flightEl = ref<HTMLElement | null>(null)
let timer: number | undefined

const POLL_MS = 30_000

async function load() {
  try {
    state.value = (await gameResetApi.ibGame(props.leagueId)).data
    error.value = ''
    if (state.value.under_way && orderId.value === null) orderId.value = state.value.under_way.id
  } catch (e) {
    error.value = apiError(e, "Could not read the hub's game")
  }
}

const matches = computed(() => typed.value.trim().toUpperCase() === props.fullId.toUpperCase())
const members = computed(() => (state.value?.boards ?? []).filter((b) => b.bbs_index !== null))

function hex(i: number | null): string {
  return i === null ? '-' : i.toString(16).toUpperCase().padStart(2, '0')
}

function at(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : ''
}

function ago(iso: string | null): string {
  if (!iso) return ''
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 90) return 'just now'
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`
  if (secs < 2 * 86400) return `${Math.round(secs / 3600)} h ago`
  return `${Math.round(secs / 86400)} days ago`
}

function roundTrip(days: number | null): string {
  if (days === null) return ''
  const mins = days * 1440
  if (mins < 90) return `${Math.max(1, Math.round(mins))} min`
  if (days < 2) return `${(days * 24).toFixed(1)} h`
  return `${days.toFixed(1)} days`
}

function held(h: Record<string, number>): string {
  return Object.entries(h).map(([r, n]) => `${n} ${r}`).join(', ')
}

const REASONS: Record<string, string> = {
  protocol: "A packet format this hub's build cannot read. Kept until the hub updates, or it expires.",
  rules: "States rules that are neither the league's nor, within a week of a change, the ones before.",
  signature: "Its sender's roster key did not verify it (or the next run will apply it).",
  unreadable: 'Not a packet at all.'
}

function ask(kind: 'freeze' | 'thaw') {
  asking.value = kind
  typed.value = ''
  orderError.value = ''
  if (kind === 'freeze' && !message.value)
    message.value = 'The league is paused for an update. Play resumes shortly.'
}

async function order() {
  if (!asking.value) return
  busy.value = true
  orderError.value = ''
  try {
    const { data } = asking.value === 'freeze'
      ? await gameResetApi.freeze(props.leagueId, { confirm: typed.value.trim(), message: message.value })
      : await gameResetApi.thaw(props.leagueId, { confirm: typed.value.trim() })
    orderId.value = data.id
    asking.value = null
    emit('ordered')
    await load()
  } catch (e) {
    orderError.value = apiError(e, `Could not start the ${asking.value}`)
  } finally {
    busy.value = false
  }
}

function onFinished() {
  load()
  emit('ordered')
}

onMounted(() => {
  load()
  timer = window.setInterval(load, POLL_MS)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="card mt-4">
    <div class="card-header">
      <h3>Hub's game</h3>
      <span v-if="state?.saved" class="text-muted small" :title="at(state.saved)">
        as of its last save, {{ ago(state.saved) }}
      </span>
    </div>
    <div class="card-body">
      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <p v-else-if="!state" class="text-muted">Loading...</p>
      <p v-else-if="!state.available" class="text-muted">
        The hub's game has no world yet: it is written on the game's first run.
      </p>
      <template v-else>
        <dl class="facts">
          <div><dt>Game day</dt><dd>{{ state.game_day }}<span v-if="state.started" class="text-muted">
            (started {{ state.started }})</span></dd></div>
          <div><dt>Rules</dt><dd class="font-mono">{{ state.ruleset || '-' }}</dd></div>
          <div><dt>Minimum version</dt><dd>{{ state.min_board_version || 'none' }}</dd></div>
          <div><dt>Lost forces</dt><dd>{{ state.lost_forces_days ? `${state.lost_forces_days} days` : 'never' }}
            <span class="text-muted">(trades {{ Math.max(3, state.lost_forces_days) }})</span></dd></div>
        </dl>

        <!-- The freeze -->
        <div class="freeze" :class="{ frozen: state.frozen }">
          <div class="freeze-state">
            <template v-if="state.frozen">
              <span class="badge badge-warning">Frozen</span>
              since {{ at(state.frozen_at) }}.
              Callers are told: <em>{{ state.freeze_message }}</em>
              <div class="small mt-2">
                <strong>{{ state.quiet }} of {{ members.length }}</strong> boards have reported quiet.
                A board reports once nothing but the freeze has reached it for a while, and again
                after it updates, so its version below is what it runs now. Thaw once every board
                is quiet and on the new version.
              </div>
            </template>
            <template v-else>
              <span class="badge badge-success">Playing</span>
              <span v-if="state.thawed_at" class="text-muted small">last thawed {{ at(state.thawed_at) }}</span>
              <div class="small mt-2 text-muted">
                Freeze the league before an update every board must make together, such as a new
                packet protocol: play stops everywhere and the packets in flight land, and each
                board reports when it has gone quiet.
              </div>
            </template>
          </div>
          <div class="actions">
            <button v-if="!state.frozen" class="btn btn-warning btn-sm" :disabled="!!state.under_way"
                    :title="state.under_way ? 'An order for this league is under way' : ''"
                    @click="ask('freeze')">Freeze league</button>
            <button v-else class="btn btn-primary btn-sm" :disabled="!!state.under_way"
                    :title="state.under_way ? 'An order for this league is under way' : ''"
                    @click="ask('thaw')">Thaw league</button>
          </div>
        </div>

        <div v-if="asking" class="order-form">
          <template v-if="asking === 'freeze'">
            <label for="freeze-message">What callers are shown while frozen</label>
            <input id="freeze-message" v-model="message" type="text" maxlength="200" />
          </template>
          <p v-else class="small">
            Play resumes on each board as the thaw reaches it, and every deadline moves on by the
            time spent frozen.
          </p>
          <label for="order-code">Type <code>{{ fullId }}</code> to confirm</label>
          <input id="order-code" v-model="typed" type="text" autocomplete="off" spellcheck="false"
                 :placeholder="fullId" @keyup.enter="matches && !busy && order()" />
          <div v-if="orderError" class="alert alert-error mt-2">{{ orderError }}</div>
          <div class="actions mt-2">
            <button class="btn btn-secondary btn-sm" :disabled="busy" @click="asking = null">Cancel</button>
            <button class="btn btn-sm" :class="asking === 'freeze' ? 'btn-warning' : 'btn-primary'"
                    :disabled="!matches || busy || (asking === 'freeze' && !message.trim())" @click="order">
              {{ busy ? 'Starting...' : asking === 'freeze' ? `Freeze ${fullId}` : `Thaw ${fullId}` }}
            </button>
          </div>
        </div>

        <div v-if="orderId !== null" class="mt-2">
          <ResetProgress :league-id="leagueId" :reset-id="orderId" game-type="I" embedded
                         @finished="onFinished" />
          <button class="btn btn-secondary btn-sm mt-2" @click="orderId = null">Hide</button>
        </div>

        <!-- Boards -->
        <div class="section-head">
          <h4>Boards</h4>
          <CopyTsvButton :table="boardsEl" />
        </div>
        <div ref="boardsEl" class="scroll">
          <table class="table">
            <thead>
              <tr>
                <th>Index</th><th>BBS</th><th>Board</th><th>Version</th><th>Rules</th>
                <th>Last packet taken</th><th>Round trip</th><th>Held here</th>
                <th v-if="state.frozen">Quiet since</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="b in state.boards" :key="b.name">
                <td class="font-mono">{{ hex(b.bbs_index) }}</td>
                <td>{{ b.bbs_name || '-' }}</td>
                <td>{{ b.name }}</td>
                <td>
                  <span class="font-mono">{{ b.version || '-' }}</span>
                  <span v-if="b.below_min" class="badge badge-danger">below minimum</span>
                </td>
                <td>
                  <span class="font-mono">{{ b.ruleset || '-' }}</span>
                  <span v-if="b.other_rules" class="badge badge-warning">other rules</span>
                </td>
                <td :title="at(b.last_packet)">{{ ago(b.last_packet) || 'never' }}</td>
                <td :title="b.round_trip_seen ? `Last probe home ${at(b.round_trip_seen)}` : ''">
                  {{ roundTrip(b.round_trip_days) || '-' }}
                  <span v-if="b.round_trip_seen" class="text-muted small">({{ ago(b.round_trip_seen) }})</span>
                </td>
                <td>
                  <span v-if="b.protocol_held" class="badge badge-danger"
                        title="Held for a protocol this hub's build cannot read, and not talked past since">
                    protocol</span>
                  {{ held(b.held) }}
                </td>
                <td v-if="state.frozen" :title="at(b.quiet_since)">
                  <span v-if="b.quiet_since" class="badge badge-success">{{ ago(b.quiet_since) }}</span>
                  <span v-else class="badge badge-warning">not yet</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Held packets -->
        <div class="section-head">
          <h4>Held packets <span class="text-muted small">({{ state.held.length }})</span></h4>
          <CopyTsvButton v-if="state.held.length" :table="heldEl" />
        </div>
        <p v-if="!state.held.length" class="text-muted small">The hub's game is holding nothing.</p>
        <div v-else ref="heldEl" class="scroll">
          <table class="table">
            <thead>
              <tr><th>File</th><th>From</th><th>Type</th><th>Why</th><th>Arrived</th><th>Deleted</th>
                <th>Pauses lost forces</th></tr>
            </thead>
            <tbody>
              <tr v-for="h in state.held" :key="h.file">
                <td class="font-mono">{{ h.file }}</td>
                <td>{{ h.from_board || '-' }} <span class="font-mono text-muted">{{ hex(h.bbs_index) }}</span></td>
                <td>{{ h.type || '-' }}</td>
                <td :title="REASONS[h.reason]">
                  {{ h.reason }}
                  <span v-if="h.reason === 'protocol'" class="text-muted small">(protocol {{ h.protocol }})</span>
                  <span v-if="h.reason === 'rules'" class="font-mono text-muted small">{{ h.ruleset }}</span>
                </td>
                <td :title="at(h.arrived)">{{ ago(h.arrived) }}</td>
                <td>{{ at(h.expires) }}</td>
                <td>{{ h.pauses_lost_forces ? 'yes' : 'no' }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- In flight -->
        <div class="section-head">
          <h4>Waiting for an answer <span class="text-muted small">({{ state.in_flight.length }})</span></h4>
          <CopyTsvButton v-if="state.in_flight.length" :table="flightEl" />
        </div>
        <p v-if="!state.in_flight.length" class="text-muted small">
          None of the hub game's own strikes or trades is out.
        </p>
        <div v-else ref="flightEl" class="scroll">
          <table class="table">
            <thead>
              <tr><th>ID</th><th>Kind</th><th>To</th><th>Launched</th><th>Waits</th><th>Days left</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in state.in_flight" :key="f.id">
                <td class="font-mono">{{ f.id }}</td>
                <td>{{ f.kind }}</td>
                <td>{{ f.target_empire || '-' }} on {{ f.target_board }}</td>
                <td :title="at(f.launched)">{{ ago(f.launched) }}</td>
                <td>{{ f.wait_days ? `${f.wait_days} days` : 'for ever' }}</td>
                <td>
                  <span v-if="f.held" class="badge badge-info"
                        :title="`Paused: the target's packets are held here. ${f.held_days} days held so far.`">
                    paused</span>
                  <template v-else>{{ f.days_left ?? '-' }}</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 2rem;
  margin: 0 0 1rem;
}
.facts dt {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}
.facts dd {
  margin: 0;
}
.freeze {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 6px);
}
.freeze.frozen {
  border-color: var(--color-warning);
}
.order-form {
  margin-top: 0.75rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 6px);
}
.order-form label {
  display: block;
  margin: 0.5rem 0 0.25rem;
  font-weight: 600;
}
.order-form input {
  width: 100%;
  max-width: 32rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
}
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 1.25rem 0 0.25rem;
}
.section-head h4 {
  margin: 0;
}
.scroll {
  overflow-x: auto;
}
.small {
  font-size: 0.85rem;
}
.badge + .badge,
.font-mono + .badge {
  margin-left: 0.375rem;
}
</style>
