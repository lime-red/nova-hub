<script setup lang="ts">
/**
 * Where each attack got to, and the rest of the InterBBS traffic with it:
 * Terrorist, Bombing and Special Ops, trade deals, spy reports, messages and
 * Gooies, each followed from its send to its result where it has one.
 *
 * A Missing In Transit is the attacker's game giving up on a result that never
 * came back. This page shows how far each attack actually travelled: to the
 * hub, on to the target, back to the hub as a result, and home again. The last
 * step reached is where it stopped.
 *
 * Like Movements, this is for reading rather than alarming: a board that has not
 * polled today is not broken. The one verdict it does give is MIT, because the
 * game's rule is mechanical -- no result by the league's "Days for Lost Attacks"
 * and the attack is written off, and a result arriving after that is discarded.
 *
 * Unit counts are not in the listing. They are hidden game state, and nothing
 * here needs them to tell one attack from another. An admin can reveal one
 * attack's forces by clicking for them -- never by default, so an admin who also
 * plays does not see them by accident. Others are not offered the button, and
 * the server refuses them anyway.
 */
import { computed, onMounted, ref, watch } from 'vue'
import AppLayout from '@/components/AppLayout.vue'
import {
  attacksApi, leaguesApi, trafficApi,
  type AttackForces, type AttackJourney, type TrafficDetails, type TrafficJourney,
} from '@/services/api'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const loading = ref(false)
const error = ref<string | null>(null)
const journeys = ref<AttackJourney[]>([])
const traffic = ref<TrafficJourney[]>([])
const leagues = ref<Array<{ id: number; name: string; full_id: string }>>([])
// "015B Medusa Test League 015": two leagues can share a number and even a
// name, one BRE and one FE, so the name alone doesn't say which.
const leagueLabels = computed(() => new Map(leagues.value.map((l) => [l.id, `${l.full_id} ${l.name}`])))
function leagueLabel(id: number, name?: string | null): string {
  return leagueLabels.value.get(id) ?? name ?? '-'
}
const expanded = ref<Set<string>>(new Set())

const days = ref(14)
const leagueId = ref<number | undefined>(undefined)
const planet = ref<number | undefined>(undefined)
const onlyMit = ref(false)
const idSearch = ref('')
const launchedOn = ref('')   // YYYY-MM-DD, matched client-side
const kind = ref('')         // '' everything, 'Attack', or a traffic kind

const STEPS: Array<{ key: keyof AttackJourney; label: string }> = [
  { key: 'attack_at_hub', label: 'Attack reached the hub' },
  { key: 'attack_delivered', label: 'Attack taken by the target board' },
  { key: 'result_at_hub', label: 'Result reached the hub' },
  { key: 'result_delivered', label: 'Result taken by the attacker board' },
]

async function load() {
  loading.value = true
  error.value = null
  try {
    const id = idSearch.value.trim()
    const filters = { days: days.value, league_id: leagueId.value, planet: planet.value || undefined }
    // An attack ID or the MIT filter is about attacks alone.
    const attacksOnly = !!id || onlyMit.value
    const [a, t] = await Promise.all([
      attacksApi.list({
        ...filters,
        attack_id: /^[0-9a-fA-F]{1,16}$/.test(id) ? id : undefined,
        mit: onlyMit.value ? 'any' : undefined,
      }),
      attacksOnly ? Promise.resolve({ data: [] as TrafficJourney[] }) : trafficApi.list(filters),
    ])
    journeys.value = a.data
    traffic.value = t.data
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Could not load attacks'
  } finally {
    loading.value = false
  }
}

interface Row {
  id: string
  at: string                  // hub clock, for ordering
  attack?: AttackJourney
  traffic?: TrafficJourney
}

const rows = computed<Row[]>(() => {
  const out: Row[] = [
    ...journeys.value.map((j) => ({ id: j.attack_id, at: j.attack_at_hub || j.launched || '', attack: j })),
    ...traffic.value.map((t) => ({ id: t.key, at: t.events[0]?.at_hub || '', traffic: t })),
  ]
  return out.sort((x, y) => (x.at < y.at ? 1 : x.at > y.at ? -1 : 0))
})

const kinds = computed(() => [...new Set(traffic.value.map((t) => t.kind))].sort())

/** A player's MIT report gives a date, not an ID; this is how to get from one to the other. */
const shown = computed(() =>
  rows.value.filter((r) => {
    if (kind.value === 'Attack' && !r.attack) return false
    if (kind.value && kind.value !== 'Attack' && r.traffic?.kind !== kind.value) return false
    if (launchedOn.value) {
      const day = r.attack ? r.attack.launched : r.at
      if (!day?.startsWith(launchedOn.value)) return false
    }
    return true
  })
)

const counts = computed(() => {
  const c = { total: journeys.value.length, delivered: 0, waiting: 0, late: 0, overdue: 0, possible: 0 }
  for (const j of journeys.value) {
    if (j.stage === 'result delivered') c.delivered++
    else if (j.stage !== 'relay not held') c.waiting++
    if (j.mit === 'late') c.late++
    if (j.mit === 'overdue') c.overdue++
    if (j.mit === 'possible') c.possible++
  }
  return c
})

function toggle(id: string) {
  const next = new Set(expanded.value)
  next.has(id) ? next.delete(id) : next.add(id)
  expanded.value = next
}

// Admin reveal: fetched only on click, forgotten when hidden.
const UNITS = ['troopers', 'jets', 'tanks', 'bombers'] as const
const forces = ref<Record<string, AttackForces>>({})
const forcesLoading = ref<string | null>(null)
const forcesError = ref<Record<string, string>>({})

async function reveal(id: string) {
  forcesLoading.value = id
  const errs = { ...forcesError.value }
  delete errs[id]
  try {
    const { data } = await attacksApi.forces(id)
    forces.value = { ...forces.value, [id]: data }
  } catch (e: any) {
    errs[id] = e?.response?.data?.detail || 'Could not reveal forces'
  } finally {
    forcesError.value = errs
    forcesLoading.value = null
  }
}

function hide(id: string) {
  const next = { ...forces.value }
  delete next[id]
  forces.value = next
}

// Traffic: the same reveal, per event, and the same journey dots.
const details = ref<Record<string, TrafficDetails>>({})
const detailsLoading = ref<string | null>(null)
const detailsError = ref<Record<string, string>>({})

async function revealEvent(key: string) {
  detailsLoading.value = key
  const errs = { ...detailsError.value }
  delete errs[key]
  try {
    const { data } = await trafficApi.details(key)
    details.value = { ...details.value, [key]: data }
  } catch (e: any) {
    errs[key] = e?.response?.data?.detail || 'Could not reveal this'
  } finally {
    detailsError.value = errs
    detailsLoading.value = null
  }
}

function hideEvent(key: string) {
  const next = { ...details.value }
  delete next[key]
  details.value = next
}

type TrafficStep = { key: 'sent_at_hub' | 'sent_delivered' | 'result_at_hub' | 'result_delivered'; label: string }

function trafficSteps(t: TrafficJourney): TrafficStep[] {
  const steps: TrafficStep[] = [
    { key: 'sent_at_hub', label: `${t.kind} reached the hub` },
    { key: 'sent_delivered', label: `${t.kind} taken by the target board` },
  ]
  if (t.expects_result) {
    steps.push({ key: 'result_at_hub', label: 'Result reached the hub' },
               { key: 'result_delivered', label: 'Result taken by the sending board' })
  }
  return steps
}

function trafficStepTitle(t: TrafficJourney, step: TrafficStep): string {
  const at = t[step.key]
  return at ? `${step.label}: ${when(at)}` : `${step.label}: not yet`
}

// A realm: its planet and letter (an IB realm has no letter), and its name as
// the hub knew it when it read the packet.
function realm(planet: number, letter: string | null, name: string | null | undefined): string {
  const where = `${planet}${letter ?? ''}`
  return name ? `${where} ${name}` : where
}

function trafficRoute(t: {
  from_planet: number; to_planet: number; from_letter: string | null; to_letter: string | null
  from_name: string | null; to_name: string | null
  recipients?: string | null; recipient_names?: (string | null)[] | null
}): string {
  const from = realm(t.from_planet, t.from_letter, t.from_name)
  const r = t.recipients ?? null
  if (r === 'all planets') return `${from} → all planets`
  const letters = r === null ? [] : r.split('')
  const named = t.recipient_names ?? null
  if (named !== null || r !== null) {
    const who = (named ?? letters).map((n, i) => n ?? letters[i] ?? '?')
    if (who.length === 1 && named === null) return `${from} → ${t.to_planet}${who[0]}`
    return `${from} → ${t.to_planet} (${who.join(', ')})`
  }
  return `${from} → ${realm(t.to_planet, t.to_letter, t.to_name)}`
}

function trafficStageClass(t: TrafficJourney): string {
  return ['result delivered', 'delivered', 'destroyed'].includes(t.stage) ? 'badge-success' : 'badge-warning'
}

function label(k: string): string {
  return k.replace(/_/g, ' ')
}

function when(stamp: string | null): string {
  if (!stamp) return '-'
  return stamp.replace('T', ' ').slice(0, 19)
}

// The attacker's board clock relative to the hub's, as measured from its packets.
function clockOffset(minutes: number | null): string {
  if (minutes === null) return 'clock not yet measured'
  if (minutes === 0) return 'on hub time'
  const sign = minutes > 0 ? '+' : '-'
  const m = Math.abs(minutes)
  return `${sign}${Math.floor(m / 60)}h${m % 60 ? String(m % 60).padStart(2, '0') : ''} from hub`
}

function reached(j: AttackJourney, key: keyof AttackJourney): boolean {
  return !!j[key]
}

function stepTitle(j: AttackJourney, step: { key: keyof AttackJourney; label: string }): string {
  const at = j[step.key] as string | null
  return at ? `${step.label}: ${when(at)}` : `${step.label}: not yet`
}

function stageClass(j: AttackJourney): string {
  if (j.mit === 'possible') return 'badge-warning'
  if (j.mit) return 'badge-danger'
  if (j.stage === 'relay not held') return 'badge-info'
  return j.stage === 'result delivered' ? 'badge-success' : 'badge-warning'
}

function mitText(j: AttackJourney): string {
  if (j.mit === 'late') return 'MIT: result arrived too late'
  if (j.mit === 'overdue') return 'MIT: past the window'
  if (j.mit === 'possible') return 'Possible MIT: result arrived as the attacker rolled over'
  return ''
}

function clearFilters() {
  kind.value = ''
  leagueId.value = undefined
  planet.value = undefined
  onlyMit.value = false
  idSearch.value = ''
  launchedOn.value = ''
}

const filtersActive = computed(
  () => !!(kind.value || leagueId.value || planet.value || onlyMit.value || idSearch.value || launchedOn.value)
)

onMounted(async () => {
  try {
    const { data } = await leaguesApi.list()
    leagues.value = data
  } catch {
    // The league filter is a convenience; the page works without it.
  }
  await load()
})

let idTimer: ReturnType<typeof setTimeout> | undefined
watch(idSearch, () => {
  clearTimeout(idTimer)
  idTimer = setTimeout(load, 300)
})
watch([days, leagueId, planet, onlyMit], load)
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Attacks &amp; Traffic</h1>
          <p class="text-muted">
            BRE attacks, ops, trade deals, spy reports, messages and Gooies, each followed
            through the hub to its result &mdash; who to whom, not what
          </p>
        </div>
        <div class="window-picker">
          <label for="days">Launched</label>
          <select id="days" v-model.number="days" class="form-select">
            <option :value="1">Last 24 hours</option>
            <option :value="7">Last 7 days</option>
            <option :value="14">Last 14 days</option>
            <option :value="30">Last 30 days</option>
            <option :value="90">Last 90 days</option>
          </select>
          <!-- Reloads with the filters as they are; F5 starts the page afresh. -->
          <button class="btn btn-secondary" :disabled="loading" @click="load">
            {{ loading ? 'Refreshing...' : 'Refresh' }}
          </button>
        </div>
      </header>

      <div v-if="error" class="alert alert-error">
        {{ error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="load">Retry</button>
      </div>

      <div v-else-if="loading && journeys.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading attacks...</p>
      </div>

      <template v-else>
        <div class="tiles">
          <div class="card tile">
            <span class="tile-n">{{ counts.total }}</span>
            <span class="text-muted">attacks</span>
          </div>
          <div class="card tile">
            <span class="tile-n">{{ traffic.length }}</span>
            <span class="text-muted">other traffic</span>
          </div>
          <div class="card tile">
            <span class="tile-n">{{ counts.delivered }}</span>
            <span class="text-muted">result home</span>
          </div>
          <div class="card tile">
            <span class="tile-n">{{ counts.waiting }}</span>
            <span class="text-muted">still travelling</span>
          </div>
          <button class="card tile tile-mit" :class="{ on: onlyMit }" @click="onlyMit = !onlyMit"
                  title="Show only attacks the attacker's game will have written off">
            <span class="tile-n">{{ counts.late + counts.overdue }}</span>
            <span class="text-muted">Missing In Transit<template v-if="counts.possible"> (+{{ counts.possible }} possible)</template></span>
          </button>
        </div>

        <div class="card">
          <div class="card-header filter-bar">
            <h2>
              {{ shown.length }} journey<span v-if="shown.length !== 1">s</span>
            </h2>
            <div class="filters">
              <select v-model="kind" class="form-select">
                <option value="">Everything</option>
                <option value="Attack">Attacks</option>
                <option v-for="k in kinds" :key="k" :value="k">{{ k }}</option>
              </select>
              <select v-model="leagueId" class="form-select">
                <option :value="undefined">All leagues</option>
                <option v-for="l in leagues" :key="l.id" :value="l.id">{{ leagueLabel(l.id, l.name) }}</option>
              </select>
              <input v-model.number="planet" type="number" min="1" class="form-input narrow"
                     placeholder="Planet" title="Attacker's or target's planet" />
              <input v-model="launchedOn" type="date" class="form-input"
                     title="The Date: on the player's MIT report" />
              <input v-model="idSearch" class="form-input id-input font-mono"
                     placeholder="Attack ID" title="Any leading part of the ID" />
              <button v-if="filtersActive" class="btn btn-sm btn-secondary" @click="clearFilters">
                Clear
              </button>
            </div>
          </div>
          <div class="card-body" style="padding: 0;">
            <div v-if="shown.length === 0" class="empty">
              <p class="text-muted">
                Nothing matches. Traffic is read from BRE packets as they reach the hub;
                packets stored before this page existed appear once
                <code>backfill_attacks.py --redo</code> has been run.
              </p>
            </div>
            <table v-else class="table">
              <thead>
                <tr>
                  <th title="Attacks: launched, on the attacker's clock. Traffic: reached the hub.">When</th>
                  <th>League</th>
                  <th>From &rarr; to</th>
                  <th>Type</th>
                  <th>Journey</th>
                  <th>Where it is</th>
                  <th>ID</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="row in shown" :key="row.id">
                <template v-for="j in row.attack ? [row.attack] : []" :key="j.attack_id">
                  <tr class="clickable" :class="{ mit: j.mit }" @click="toggle(j.attack_id)">
                    <td class="font-mono" title="On the attacker board's clock -- the Date: on its MIT report">
                      {{ when(j.launched) }}
                    </td>
                    <td>{{ leagueLabel(j.league_id, j.league_name) }}</td>
                    <td class="font-mono">
                      {{ realm(j.from_planet, j.attacker, j.attacker_name) }} &rarr; {{ realm(j.to_planet, j.target, j.target_name) }}
                    </td>
                    <td>Attack<span v-if="j.attack_type" class="text-muted"> &middot; {{ j.attack_type }}</span></td>
                    <td>
                      <span class="journey">
                        <template v-for="(s, i) in STEPS" :key="s.key">
                          <span v-if="i === 2" class="turn" title="The target's game resolves it here"></span>
                          <span class="dot" :class="{ on: reached(j, s.key) }" :title="stepTitle(j, s)"></span>
                        </template>
                      </span>
                    </td>
                    <td>
                      <span class="badge" :class="stageClass(j)"
                            :title="j.stage === 'relay not held' ? `The hub no longer holds the packets it wrote to planet ${j.unheld_relay_to} around then (older hub versions overwrote them), so this journey's next hop cannot be shown. It is not evidence of a loss.` : ''">{{ j.stage }}</span>
                      <div v-if="j.mit" class="mit-text">{{ mitText(j) }}</div>
                    </td>
                    <td class="font-mono text-muted">{{ j.attack_id }}</td>
                  </tr>
                  <tr v-if="expanded.has(j.attack_id)" class="detail">
                    <td colspan="7">
                      <div class="detail-grid">
                        <div>
                          <h3>Journey</h3>
                          <dl>
                            <dt>Launched</dt><dd class="font-mono">{{ when(j.launched) }} <span class="text-muted">(attacker's clock)</span></dd>
                            <template v-for="s in STEPS" :key="`st-${s.key}`">
                              <dt>{{ s.label }}</dt>
                              <dd class="font-mono">{{ when(j[s.key] as string | null) }}</dd>
                            </template>
                            <dt>Resolved</dt><dd class="font-mono">{{ when(j.resolved) }} <span class="text-muted">(defender's clock)</span></dd>
                            <dt>MIT window</dt>
                            <dd>
                              {{ j.lost_attack_days }} day<span v-if="j.lost_attack_days !== 1">s</span>
                              <span class="text-muted"> &mdash; written off from {{ when(j.mit_due_local).slice(0, 10) }} on the attacker's clock</span>
                            </dd>
                            <dt>Due</dt>
                            <dd class="font-mono">{{ when(j.mit_due) }} <span class="text-muted">(earliest, hub clock; attacker's board {{ clockOffset(j.attacker_clock_minutes) }})</span></dd>
                            <template v-if="j.rollover_after">
                              <dt>Attacker rolled over</dt>
                              <dd class="font-mono">
                                {{ when(j.rollover_after) }} &ndash; {{ when(j.rollover_before) }}
                                <span class="text-muted">(hub clock; between its uploads either side of the day's skipped sequence number &mdash; a result in before this counts)</span>
                              </dd>
                            </template>
                          </dl>
                        </div>
                        <div>
                          <h3>Packets that carried it</h3>
                          <table class="table compact">
                            <thead>
                              <tr><th></th><th>Packet</th><th>Boards</th><th>At hub</th><th>Taken</th></tr>
                            </thead>
                            <tbody>
                              <tr v-for="h in j.hops" :key="`${h.packet_id}-${h.is_result}`">
                                <td>
                                  <span class="badge" :class="h.is_result ? 'badge-info' : 'badge-secondary'">
                                    {{ h.is_result ? 'result' : 'attack' }}
                                  </span>
                                </td>
                                <td class="font-mono">{{ h.filename }}</td>
                                <td class="font-mono">{{ h.source_bbs }} &rarr; {{ h.dest_bbs }}</td>
                                <td class="font-mono">{{ when(h.at_hub) }}</td>
                                <td class="font-mono">{{ h.taken ? when(h.taken) : 'not yet' }}</td>
                              </tr>
                            </tbody>
                          </table>
                          <div v-if="authStore.isAdmin" class="reveal">
                            <template v-if="forces[j.attack_id]">
                              <h3>
                                Forces
                                <button class="btn btn-sm btn-secondary" @click="hide(j.attack_id)">Hide</button>
                              </h3>
                              <table class="table compact forces">
                                <thead>
                                  <tr>
                                    <th></th>
                                    <th v-for="u in UNITS" :key="u">{{ u }}</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  <tr>
                                    <td class="text-muted">sent</td>
                                    <td v-for="u in UNITS" :key="u" class="font-mono">{{ forces[j.attack_id].sent[u] }}</td>
                                  </tr>
                                  <template v-if="forces[j.attack_id].lost">
                                    <tr>
                                      <td class="text-muted">lost</td>
                                      <td v-for="u in UNITS" :key="u" class="font-mono">{{ forces[j.attack_id].lost![u] }}</td>
                                    </tr>
                                    <tr>
                                      <td class="text-muted">returned</td>
                                      <td v-for="u in UNITS" :key="u" class="font-mono">{{ forces[j.attack_id].returned![u] }}</td>
                                    </tr>
                                  </template>
                                </tbody>
                              </table>
                              <p class="text-muted small">
                                <template v-if="forces[j.attack_id].resolved">
                                  <strong>{{ forces[j.attack_id].success ? 'SUCCESS' : 'FAILURE' }}</strong><template v-if="forces[j.attack_id].regions_captured">,
                                  captured {{ forces[j.attack_id].regions_captured }} region<span v-if="forces[j.attack_id].regions_captured !== 1">s</span></template>.
                                  Attacker lost {{ forces[j.attack_id].loss_percent }}% of each unit type;
                                  destroyed {{ forces[j.attack_id].defenders_destroyed }} defending trooper<span v-if="forces[j.attack_id].defenders_destroyed !== 1">s</span>.
                                </template>
                                <template v-else>No result seen yet, so no outcome.</template>
                                <template v-if="forces[j.attack_id].carriers">
                                  {{ forces[j.attack_id].carriers }} carrier<span v-if="forces[j.attack_id].carriers !== 1">s</span> flew the jets; carriers always come home.
                                </template>
                              </p>
                            </template>
                            <template v-else>
                              <button class="btn btn-sm btn-secondary" :disabled="forcesLoading === j.attack_id"
                                      @click="reveal(j.attack_id)">
                                {{ forcesLoading === j.attack_id ? 'Revealing…' : 'Reveal forces (admin)' }}
                              </button>
                              <span class="text-muted small"> hidden game state &mdash; each reveal is logged</span>
                              <div v-if="forcesError[j.attack_id]" class="text-danger small">{{ forcesError[j.attack_id] }}</div>
                            </template>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
                <template v-for="t in row.traffic ? [row.traffic] : []" :key="t.key">
                  <tr class="clickable" @click="toggle(t.key)">
                    <td class="font-mono" title="Reached the hub">{{ when(row.at) }}</td>
                    <td>{{ leagueLabel(t.league_id, t.league_name) }}</td>
                    <td class="font-mono">{{ trafficRoute(t) }}</td>
                    <td>{{ t.kind }}</td>
                    <td>
                      <span class="journey">
                        <template v-for="(s, i) in trafficSteps(t)" :key="s.key">
                          <span v-if="i === 2" class="turn" title="The target's game answers it here"></span>
                          <span class="dot" :class="{ on: !!t[s.key] }" :title="trafficStepTitle(t, s)"></span>
                        </template>
                      </span>
                    </td>
                    <td><span class="badge" :class="trafficStageClass(t)">{{ t.stage }}</span></td>
                    <td class="font-mono text-muted">{{ t.key }}</td>
                  </tr>
                  <tr v-if="expanded.has(t.key)" class="detail">
                    <td colspan="7">
                      <h3>Journey</h3>
                      <table class="table compact">
                        <thead>
                          <tr><th>Event</th><th>From &rarr; to</th><th>Packets</th><th>At hub</th><th>Taken</th><th></th></tr>
                        </thead>
                        <tbody>
                          <template v-for="e in t.events" :key="e.key">
                            <tr>
                              <td>
                                <span class="badge" :class="e.role === 'result' ? 'badge-info' : 'badge-secondary'">{{ e.kind }}</span>
                                <div v-if="e.stamp" class="text-muted small">resolved {{ when(e.stamp) }} (far side's clock)</div>
                              </td>
                              <td class="font-mono">{{ trafficRoute(e) }}</td>
                              <td class="font-mono small">
                                <div v-for="h in e.hops" :key="h.packet_id">{{ h.filename }} ({{ h.source_bbs }}&rarr;{{ h.dest_bbs }})</div>
                              </td>
                              <td class="font-mono">{{ when(e.at_hub) }}</td>
                              <td class="font-mono">{{ e.delivered ? when(e.delivered) : 'not yet' }}</td>
                              <td>
                                <template v-if="e.kind === 'Message'">
                                  <span class="text-muted small">contents never shown</span>
                                </template>
                                <template v-else-if="authStore.isAdmin && e.revealable && !details[e.key]">
                                  <button class="btn btn-sm btn-secondary" :disabled="detailsLoading === e.key"
                                          @click="revealEvent(e.key)">
                                    {{ detailsLoading === e.key ? 'Revealing…' : 'Reveal (admin)' }}
                                  </button>
                                  <div v-if="detailsError[e.key]" class="text-danger small">{{ detailsError[e.key] }}</div>
                                </template>
                                <button v-else-if="details[e.key]" class="btn btn-sm btn-secondary" @click="hideEvent(e.key)">Hide</button>
                              </td>
                            </tr>
                            <tr v-if="details[e.key]">
                              <td colspan="6">
                                <dl>
                                  <template v-for="(v, k) in details[e.key].details" :key="k">
                                    <dt>{{ label(String(k)) }}</dt>
                                    <dd v-if="typeof v === 'object'" class="font-mono">
                                      <span v-for="(n, g) in v" :key="g">{{ n }} {{ g }}&nbsp; </span>
                                    </dd>
                                    <dd v-else class="font-mono">{{ v }}</dd>
                                  </template>
                                </dl>
                              </td>
                            </tr>
                          </template>
                        </tbody>
                      </table>
                      <p v-if="authStore.isAdmin" class="text-muted small">
                        Reveals show hidden game state and each is logged. Gooie, Bombing and Special Op
                        contents are not decoded yet.
                      </p>
                    </td>
                  </tr>
                </template>
                </template>
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

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
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
}

.tile-n {
  font-size: 1.6rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.tile-mit {
  cursor: pointer;
  border: 1px solid var(--color-border);
}

.tile-mit .tile-n {
  color: var(--color-danger);
}

.tile-mit.on {
  border-color: var(--color-danger);
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

.narrow {
  width: 6rem;
}

.id-input {
  width: 11rem;
}

.empty {
  padding: 2rem;
  text-align: center;
}

.clickable {
  cursor: pointer;
}

.clickable:hover {
  background: var(--color-background-mute, rgba(127, 127, 127, 0.12));
}

tr.mit td:first-child {
  box-shadow: inset 3px 0 0 var(--color-danger);
}

.journey {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
  border: 2px solid var(--color-border);
  background: transparent;
}

.dot.on {
  background: var(--color-primary);
  border-color: var(--color-primary);
}

.turn {
  width: 0.9rem;
  height: 2px;
  background: var(--color-border);
}

.mit-text {
  font-size: 0.8rem;
  color: var(--color-danger);
  margin-top: 0.2rem;
}

.detail > td {
  background: var(--color-background-mute, rgba(127, 127, 127, 0.06));
  padding: 1rem 1.25rem;
}

.detail-grid {
  display: grid;
  grid-template-columns: minmax(18rem, 1fr) 2fr;
  gap: 1.5rem;
}

.detail h3 {
  font-size: 0.9rem;
  margin-bottom: 0.5rem;
}

.detail dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.25rem 0.75rem;
  font-size: 0.85rem;
}

.detail dt {
  color: var(--color-text-muted);
}

.reveal {
  margin-top: 1rem;
}

.reveal h3 {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.table.forces {
  width: auto;
}

.table.compact td,
.table.compact th {
  padding: 0.3rem 0.4rem;
  font-size: 0.85rem;
}
</style>
