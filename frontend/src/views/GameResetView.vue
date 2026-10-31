<script setup lang="ts">
// A league's game reset. Every setting the game's reset editor offers, in the
// editor's order, with the game's own help always visible. The hub keeps one
// draft per league; PERFORM RESET hands the saved draft to the hub, which types
// it into the game's own editor. The server checks everything again: an
// out-of-range value would throw that driver out of step. Immortal Barons has
// no editor: its settings are written into the Coordinator's config.json.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import ResetHistory from '@/components/ResetHistory.vue'
import ResetPerformDialog from '@/components/ResetPerformDialog.vue'
import {
  apiError,
  gameResetApi,
  type GameResetForm,
  type ResetDetail,
  type ResetField,
  type ResetValue
} from '@/services/api'
import { blankResetValue, gameTypeName } from '@/games'

const route = useRoute()
const leagueId = computed(() => Number(route.params.id))

const form = ref<GameResetForm | null>(null)
const values = ref<Record<string, ResetValue>>({})
const notes = ref('')
const saved = ref('') // what was last loaded or saved, to tell unsaved changes
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')
const serverErrors = ref<Record<string, string>>({})
const performing = ref(false) // the confirmation dialog is open
const openReset = ref<number | null>(null) // the history row opened, with its progress
const historyKey = ref(0)

const GROUPS = [
  { id: 'General', title: 'General' },
  { id: 'GamePlay', title: 'GamePlay' },
  { id: 'InterBBS', title: 'InterBBS' }
]

// Which settings cards are open. They start open, unless the page opens on a
// reset (under way, or from the league page): then the reset is the point.
const opened = ref<Record<string, boolean>>({})
const historyEl = ref<HTMLElement | null>(null)

function setAll(open: boolean) {
  opened.value = Object.fromEntries(GROUPS.map((g) => g.id).map((id) => [id, open]))
}

const allOpen = computed(() => Object.values(opened.value).every(Boolean))

// What the hub decoded from the league's last Configupdate before the full
// decoder (16 Oct 2026): only these three. Newer ones carry `settings`.
const OBSERVED: Record<string, 'protection_turns' | 'indiv_attacks' | 'lost_attack_days'> = {
  protection_turns: 'protection_turns',
  indiv_attacks: 'indiv_attacks',
  lost_attack_days: 'lost_attack_days'
}

function snapshot(): string {
  return JSON.stringify({ v: values.value, n: notes.value })
}

const dirty = computed(() => form.value !== null && snapshot() !== saved.value)

function load(data: GameResetForm) {
  const first = form.value === null
  form.value = data
  if (data.under_way) openReset.value = data.under_way.id
  // From the league page's history: /leagues/:id/reset?reset=<id>
  else if (openReset.value === null && route.query.reset) openReset.value = Number(route.query.reset)
  if (first) setAll(openReset.value === null)
  values.value = { ...data.defaults, ...(data.draft?.settings ?? {}) }
  notes.value = data.draft?.notes ?? ''
  saved.value = snapshot()
  serverErrors.value = {}
}

// `quiet` refreshes in place, without the loading screen that would take the
// progress card with it.
async function fetchForm(quiet = false) {
  if (!quiet) loading.value = true
  error.value = ''
  try {
    const { data } = await gameResetApi.form(leagueId.value)
    load(data)
  } catch (e) {
    error.value = apiError(e, 'Could not load the reset form')
  } finally {
    loading.value = false
  }
}

function shown(f: ResetField): boolean {
  return !f.depends_on || values.value[f.depends_on[0]] === f.depends_on[1]
}

function fieldsIn(group: string): ResetField[] {
  return (form.value?.fields ?? []).filter((f) => f.group === group && shown(f))
}

// The same limits the server applies, so most mistakes show as you type.
function problem(f: ResetField): string {
  const v = values.value[f.id]
  if (f.kind === 'number') {
    if (typeof v !== 'number' || !Number.isInteger(v)) return 'Enter a whole number.'
    if (f.min !== null && v < f.min) return `At least ${f.min}.`
    if (f.max !== null && v > f.max) return `At most ${f.max}: the game's own limit.`
  }
  if (f.kind === 'date' && f.default !== null && !v) return 'Enter a date.'
  return serverErrors.value[f.id] ?? ''
}

function changedIn(group: string): number {
  return fieldsIn(group).filter(changedFromDefault).length
}

function problemsIn(group: string): number {
  return fieldsIn(group).filter((f) => problem(f)).length
}

const problems = computed(() =>
  (form.value?.fields ?? []).filter((f) => shown(f) && problem(f)).length
)

// Below our usual minimum: allowed, since a league may mean it (a late
// free-for-all where nobody can buy anything), but worth a second look.
function advice(f: ResetField): string {
  const v = values.value[f.id]
  if (f.advised_min === null || typeof v !== 'number' || v >= f.advised_min) return ''
  return `Below the usual minimum of ${f.advised_min}: ${f.advised_reason}`
}

const advisories = computed(() =>
  (form.value?.fields ?? []).filter((f) => shown(f) && !problem(f) && advice(f)).length
)

function display(f: ResetField, v: ResetValue): string {
  if (v === null || v === undefined || v === '') return blankResetValue(f.id, f.kind)
  if (f.kind === 'toggle') return v ? 'Enabled' : 'Disabled'
  if (f.kind === 'choice') return f.choices.find((c) => c.letter === v)?.name ?? String(v)
  if (f.kind === 'number') return Number(v).toLocaleString()
  return String(v)
}

function changedFromDefault(f: ResetField): boolean {
  return values.value[f.id] !== form.value?.defaults[f.id]
}

// What the current game runs with: the last reset from the console, or failing
// that what the hub has decoded from the league's packets.
function current(f: ResetField): string | null {
  const applied = form.value?.last_applied?.settings
  if (applied && f.id in applied) return display(f, applied[f.id])
  const observed = form.value?.observed
  const key = OBSERVED[f.id]
  const seen = observed?.settings && f.id in observed.settings
    ? observed.settings[f.id]
    : key ? observed?.[key] : null
  return seen === null || seen === undefined ? null : display(f, seen)
}

function setNumber(f: ResetField, raw: string) {
  values.value[f.id] = raw.trim() === '' ? null : Number(raw)
  delete serverErrors.value[f.id]
}

function setText(f: ResetField, raw: string) {
  values.value[f.id] = raw.trim()
  delete serverErrors.value[f.id]
}

function setDate(f: ResetField, raw: string) {
  values.value[f.id] = raw || (f.default === null ? null : '')
  delete serverErrors.value[f.id]
}

function useDefaults() {
  if (!form.value) return
  values.value = { ...form.value.defaults }
  serverErrors.value = {}
}

function copyLast() {
  if (!form.value?.last_applied) return
  // Dates belong to that game, not the next one.
  const rest = { ...form.value.last_applied.settings }
  delete rest.start_date
  delete rest.join_date
  values.value = { ...form.value.defaults, ...rest }
  serverErrors.value = {}
}

async function save() {
  if (!form.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  serverErrors.value = {}
  try {
    const { data } = await gameResetApi.saveDraft(leagueId.value, {
      settings: values.value,
      notes: notes.value.trim() || null,
      version: form.value.draft?.version ?? null
    })
    load(data)
    message.value = 'Draft saved.'
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 422 && Array.isArray(detail)) {
      for (const d of detail) {
        const id = d?.loc?.[d.loc.length - 1]
        if (typeof id === 'string') serverErrors.value[id] = d.msg
      }
      error.value = 'Some settings need fixing; each is marked above.'
      for (const g of GROUPS) if (problemsIn(g.id)) opened.value[g.id] = true
    } else {
      error.value = apiError(e, 'Could not save the draft')
    }
  } finally {
    saving.value = false
  }
}

async function discard() {
  if (!form.value?.draft) return
  if (!window.confirm('Discard this draft? The settings in it will be lost.')) return
  try {
    const { data } = await gameResetApi.discardDraft(leagueId.value)
    load(data)
    message.value = 'Draft discarded.'
  } catch (e) {
    error.value = apiError(e, 'Could not discard the draft')
  }
}

// The help files are hard-wrapped at 80 columns; keep only their paragraphs.
function paragraphs(text: string): string[] {
  return text.split(/\n\s*\n/).map((p) => p.replace(/\s*\n\s*/g, ' '))
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString()
}

// Why PERFORM RESET can't be pressed, or '' when it can.
const performBlocked = computed(() => {
  if (!form.value) return 'Loading'
  if (form.value.under_way) return 'A reset of this league is under way.'
  if (!form.value.draft) return 'Save a draft first: a reset performs the saved draft.'
  if (dirty.value) return 'Save or undo your changes first: a reset performs the saved draft.'
  if (problems.value) return 'Fix the settings marked below first.'
  return ''
})

// The new reset opens at the top of the history, where its steps then run;
// the settings fold away, having done their part.
function onPerformed(reset: ResetDetail) {
  performing.value = false
  message.value = ''
  openReset.value = reset.id
  setAll(false)
  fetchForm(true)
  historyKey.value++
  historyEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onFinished() {
  fetchForm(true)
}

function warnOnUnload(e: BeforeUnloadEvent) {
  if (dirty.value) {
    e.preventDefault()
    e.returnValue = ''
  }
}

onBeforeRouteLeave(() =>
  !dirty.value || window.confirm('You have unsaved changes to this draft. Leave anyway?')
)

onMounted(() => {
  window.addEventListener('beforeunload', warnOnUnload)
  fetchForm()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', warnOnUnload))
</script>

<template>
  <AppLayout>
    <div class="page">
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <p>Loading the reset form...</p>
      </div>

      <div v-else-if="!form" class="alert alert-error">
        {{ error }}
        <router-link to="/leagues" class="btn btn-sm btn-secondary mt-2">Back to Leagues</router-link>
      </div>

      <template v-else>
        <header class="page-header">
          <div>
            <div class="breadcrumb">
              <router-link to="/leagues">Leagues</router-link>
              <span>/</span>
              <router-link :to="`/leagues/${leagueId}`">{{ form.full_id }}</router-link>
              <span>/</span>
              <span>Game reset</span>
            </div>
            <h1>Game reset: {{ form.full_id }}</h1>
            <p v-if="form.game_type === 'I'" class="text-muted">
              {{ gameTypeName(form.game_type) }}. Every league rule in the Coordinator's
              config.json, with the help from the game's own config editor.
            </p>
            <p v-else class="text-muted">
              {{ gameTypeName(form.game_type) }}. Every setting the game's reset editor
              offers, with the game's own help.
            </p>
          </div>
        </header>

        <div v-if="form.game_type === 'I'" class="alert alert-warning mb-4">
          A reset wipes every player on every board in the league and starts a new
          game with these rules. Save the draft, then PERFORM RESET: the hub backs up
          its game, writes the rules into its config.json, orders the league reset,
          checks the rules the game signed for the members, and puts the game back as
          it was if anything doesn't match.
        </div>
        <div v-else class="alert alert-warning mb-4">
          A reset wipes every player on every board in the league and starts a new
          game with these settings. Save the draft, then PERFORM RESET: the hub backs
          up its game, types the settings into the game's own reset editor, checks
          both pages, and puts the game back as it was if anything doesn't match.
        </div>

        <div ref="historyEl">
          <ResetHistory
            :league-id="leagueId"
            :fields="form.fields"
            :refresh-key="historyKey"
            :open-id="openReset"
            :game-type="form.game_type"
            inline
            @finished="onFinished"
          />
        </div>

        <div class="settings-bar mb-4">
          <h2>Settings for the next game</h2>
          <div class="actions">
            <button class="btn btn-secondary btn-sm" @click="useDefaults">
              Game defaults
            </button>
            <button
              class="btn btn-secondary btn-sm"
              :disabled="!form.last_applied"
              :title="form.last_applied ? '' : 'No reset has been performed from the console yet'"
              @click="copyLast"
            >
              Copy from last reset
            </button>
            <button class="btn btn-secondary btn-sm" @click="setAll(!allOpen)">
              {{ allOpen ? 'Collapse all' : 'Expand all' }}
            </button>
          </div>
        </div>

        <section v-for="g in GROUPS" :key="g.id" class="card mb-4">
          <button class="card-header fold" :aria-expanded="opened[g.id]" @click="opened[g.id] = !opened[g.id]">
            <span class="chevron" :class="{ open: opened[g.id] }" aria-hidden="true"></span>
            <h3>{{ g.title }}</h3>
            <span class="text-muted small">{{ fieldsIn(g.id).length }} settings</span>
            <span v-if="changedIn(g.id)" class="badge badge-info">
              {{ changedIn(g.id) }} changed from default
            </span>
            <span v-if="problemsIn(g.id)" class="badge badge-danger">{{ problemsIn(g.id) }} to fix</span>
          </button>
          <div v-if="opened[g.id]" class="card-body fields">
            <div v-if="g.id === 'General'" class="our-note timezones">
              <strong>Dates and timezones</strong>
              <ul>
                <li>
                  The hub runs on {{ form.hub_timezone.name }} (UTC{{ form.hub_timezone.utc_offset }});
                  its date is {{ form.hub_timezone.today }}.
                </li>
                <li>
                  The dates you choose are applied locally on each BBS. The hub does not enforce
                  any dates or timezones itself.
                </li>
                <li>
                  BBSes with a positive timezone offset can start play earlier, but the offset
                  applies consistently throughout the round, so there is no benefit or detriment.
                </li>
                <li>
                  For structured leagues, set a Game Start Date at least one day from now, so
                  everyone gets the same chance to play at their own timezone offset.
                </li>
                <li>
                  For casual leagues, ignore all of the above: leave the dates at their defaults
                  and go have fun.
                </li>
              </ul>
            </div>
            <div
              v-for="f in fieldsIn(g.id)"
              :key="f.id"
              class="field"
              :class="{ changed: changedFromDefault(f), invalid: !!problem(f), advised: !problem(f) && !!advice(f) }"
            >
              <div class="field-input">
                <label :for="`f-${f.id}`">
                  {{ f.label }}
                </label>

                <input
                  v-if="f.kind === 'number'"
                  :id="`f-${f.id}`"
                  type="number"
                  :min="f.min ?? undefined"
                  :max="f.max ?? undefined"
                  step="1"
                  :value="values[f.id] ?? ''"
                  @input="setNumber(f, ($event.target as HTMLInputElement).value)"
                />

                <select
                  v-else-if="f.kind === 'choice'"
                  :id="`f-${f.id}`"
                  v-model="values[f.id]"
                >
                  <option v-for="c in f.choices" :key="c.letter" :value="c.letter">
                    {{ c.name }}
                  </option>
                </select>

                <label v-else-if="f.kind === 'toggle'" class="toggle">
                  <input :id="`f-${f.id}`" v-model="values[f.id]" type="checkbox" />
                  <span>{{ values[f.id] ? 'Enabled' : 'Disabled' }}</span>
                </label>

                <input
                  v-else-if="f.kind === 'text'"
                  :id="`f-${f.id}`"
                  type="text"
                  class="text-input"
                  placeholder="none"
                  :value="values[f.id] ?? ''"
                  @input="setText(f, ($event.target as HTMLInputElement).value)"
                />

                <input
                  v-else
                  :id="`f-${f.id}`"
                  type="date"
                  class="date-input"
                  :value="values[f.id] ?? ''"
                  @input="setDate(f, ($event.target as HTMLInputElement).value)"
                />

                <div class="meta">
                  <span>Default: {{ display(f, f.default) }}</span>
                  <span v-if="f.kind === 'number'">Range {{ f.min }}–{{ f.max?.toLocaleString() }}</span>
                  <span v-if="current(f) !== null">Current game: {{ current(f) }}</span>
                </div>
                <p v-if="problem(f)" class="field-error">{{ problem(f) }}</p>
                <p v-else-if="advice(f)" class="field-advice">{{ advice(f) }}</p>
              </div>

              <div class="field-help">
                <h4 v-if="f.help_title">{{ f.help_title }}</h4>
                <p v-for="(para, i) in paragraphs(f.help)" :key="i" class="help-text">{{ para }}</p>
                <p v-if="f.note" class="our-note"><strong>Note:</strong> {{ f.note }}</p>
                <p v-if="f.advised_min" class="our-note">
                  <strong>Usually at least {{ f.advised_min }}:</strong> {{ f.advised_reason }}
                  The game takes 0, and so does this form, for a league that means it.
                </p>
                <p v-if="f.admin_note" class="our-note admin-note">
                  <strong>Admins only:</strong> {{ f.admin_note }}
                </p>
              </div>
            </div>
          </div>
        </section>

        <section class="card mb-4">
          <div class="card-header">
            <h3>Notes</h3>
          </div>
          <div class="card-body">
            <textarea
              v-model="notes"
              rows="3"
              maxlength="4000"
              placeholder="Why these settings, who agreed them, anything to remember"
            ></textarea>
          </div>
        </section>

        <div class="toolbar card mb-4">
          <div v-if="error" class="alert alert-error toolbar-alert">{{ error }}</div>
          <div v-else-if="message && !dirty" class="alert alert-success toolbar-alert">
            {{ message }}
          </div>
          <div class="card-body toolbar-body">
            <div class="draft-state">
              <template v-if="form.draft">
                <strong>Draft, version {{ form.draft.version }}</strong>
                <span class="text-muted">
                  last saved by {{ form.draft.updated_by }} at
                  {{ formatTime(form.draft.updated_at) }}
                </span>
              </template>
              <strong v-else>No draft yet: these are the game's defaults.</strong>
              <span v-if="dirty" class="badge badge-warning">Unsaved changes</span>
              <span v-if="problems" class="badge badge-danger">
                {{ problems }} to fix
              </span>
              <span v-if="advisories" class="badge badge-warning">
                {{ advisories }} below the usual minimum
              </span>
            </div>
            <div class="actions">
              <button v-if="form.draft" class="btn btn-secondary btn-sm" @click="discard">
                Discard draft
              </button>
              <button
                class="btn btn-primary btn-sm"
                :disabled="saving || !dirty || problems > 0"
                @click="save"
              >
                {{ saving ? 'Saving...' : 'Save draft' }}
              </button>
              <button
                class="btn btn-danger btn-sm perform"
                :disabled="!!performBlocked"
                :title="performBlocked"
                @click="performing = true"
              >
                PERFORM RESET
              </button>
            </div>
          </div>
        </div>

        <ResetPerformDialog
          v-if="performing && form.draft"
          :form="form"
          @close="performing = false"
          @performed="onPerformed"
        />
      </template>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin: 0.25rem 0;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.breadcrumb a {
  color: var(--color-primary);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.settings-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-top: 1.5rem;
}

.settings-bar h2 {
  margin: 0;
  font-size: 1.25rem;
}

.fold {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
  width: 100%;
  border: none;
  background: none;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.fold h3 {
  margin: 0;
}

.fold[aria-expanded='true'] {
  border-bottom: 1px solid var(--color-border);
}

/* Points right when folded, down when open. */
.chevron {
  width: 0.5rem;
  height: 0.5rem;
  margin: 0 0.25rem;
  border-right: 2px solid var(--color-text-muted);
  border-bottom: 2px solid var(--color-text-muted);
  transform: rotate(-45deg);
  transition: transform 0.15s ease;
  flex: none;
  align-self: center;
}

.chevron.open {
  transform: rotate(45deg);
}

.small {
  font-size: 0.85rem;
}

/* The draft's state and what to do with it, held at the foot of the screen. */
.toolbar {
  position: sticky;
  bottom: 0;
  z-index: 5;
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.08);
}

.toolbar-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.toolbar-alert {
  margin: 1rem 1rem 0;
}

.draft-state {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.perform {
  font-weight: 700;
  letter-spacing: 0.03em;
}

.fields {
  display: flex;
  flex-direction: column;
  padding: 0;
}

.field {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 2fr;
  gap: 1.5rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
  border-left: 3px solid transparent;
}

.field:last-child {
  border-bottom: none;
}

.field.changed {
  border-left-color: var(--color-primary);
}

.field.invalid {
  border-left-color: var(--color-danger);
}

.field.advised {
  border-left-color: var(--color-warning);
}

.field-input label {
  display: block;
  font-weight: 600;
  margin-bottom: 0.375rem;
}


.date-input,
.text-input {
  display: block;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.toggle {
  display: flex !important;
  align-items: center;
  gap: 0.5rem;
  font-weight: 400 !important;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
  margin-top: 0.375rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.field-advice {
  margin: 0.375rem 0 0;
  font-size: 0.85rem;
  color: var(--color-warning);
}

.field-error {
  margin: 0.375rem 0 0;
  font-size: 0.85rem;
  color: var(--color-danger);
}

.field-help h4 {
  margin: 0 0 0.375rem;
  font-size: 0.9rem;
}

.help-text {
  margin: 0 0 0.5rem;
  font-size: 0.875rem;
  line-height: 1.5;
}

.our-note {
  margin: 0.5rem 0 0;
  padding: 0.375rem 0.625rem;
  font-size: 0.825rem;
  background: var(--color-bg);
  border-radius: var(--radius-sm);
}

.timezones {
  margin: 1rem 1.25rem 0;
}

.timezones ul {
  margin: 0.25rem 0 0;
  padding-left: 1.25rem;
}

.admin-note {
  border-left: 3px solid var(--color-warning);
}

textarea {
  resize: vertical;
}

@media (max-width: 760px) {
  .field {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }
}
</style>
