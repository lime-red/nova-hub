<script setup lang="ts">
// The last stop before a reset: what changes, what's below the usual minimum,
// what happens to every player, and the league code typed by hand.
import { computed, ref } from 'vue'
import {
  apiError,
  gameResetApi,
  type GameResetForm,
  type ResetDetail,
  type ResetField,
  type ResetValue
} from '@/services/api'
import { blankResetValue, resetSignal } from '@/games'

const props = defineProps<{ form: GameResetForm }>()
const emit = defineEmits<{ close: []; performed: [reset: ResetDetail] }>()

const typed = ref('')
const busy = ref(false)
const error = ref('')

const draft = computed(() => props.form.draft!)

// What the game runs with now, as best the hub knows: the last reset from the
// console; failing that the defaults, with what it has decoded from packets.
const OBSERVED: Record<string, 'protection_turns' | 'indiv_attacks' | 'lost_attack_days'> = {
  protection_turns: 'protection_turns',
  indiv_attacks: 'indiv_attacks',
  lost_attack_days: 'lost_attack_days'
}
const baseline = computed<Record<string, ResetValue>>(() => {
  if (props.form.last_applied) return props.form.last_applied.settings
  const seen: Record<string, ResetValue> = {}
  for (const [fid, key] of Object.entries(OBSERVED)) {
    const v = props.form.observed?.[key]
    if (v !== null && v !== undefined) seen[fid] = v
  }
  return { ...props.form.defaults, ...seen }
})

function display(f: ResetField, v: ResetValue | undefined): string {
  if (v === null || v === undefined || v === '') return blankResetValue(f.id, f.kind)
  if (f.kind === 'toggle') return v ? 'Enabled' : 'Disabled'
  if (f.kind === 'choice') return f.choices.find((c) => c.letter === v)?.name ?? String(v)
  if (f.kind === 'number') return Number(v).toLocaleString()
  return String(v)
}

const changes = computed(() =>
  props.form.fields
    .filter((f) => f.id in draft.value.settings)
    // A start date belongs to its own game, so only a chosen one is news.
    .filter((f) => f.id !== 'start_date' || draft.value.settings.start_date)
    .filter((f) => draft.value.settings[f.id] !== baseline.value[f.id])
    .map((f) => ({
      label: f.label,
      from: display(f, baseline.value[f.id]),
      to: display(f, draft.value.settings[f.id])
    }))
)

const labelOf = (id: string) => props.form.fields.find((f) => f.id === id)?.label ?? id

const matches = computed(() => typed.value.trim().toUpperCase() === props.form.full_id.toUpperCase())

async function perform() {
  busy.value = true
  error.value = ''
  try {
    const { data } = await gameResetApi.perform(props.form.league_id, {
      version: draft.value.version,
      confirm: typed.value.trim()
    })
    emit('performed', data)
  } catch (e) {
    error.value = apiError(e, 'Could not start the reset')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="!busy && emit('close')">
    <div class="modal" role="dialog" aria-labelledby="perform-title">
      <div class="modal-header">
        <h3 id="perform-title">Reset {{ form.full_id }}?</h3>
        <button class="modal-close" :disabled="busy" @click="emit('close')">&times;</button>
      </div>
      <div class="modal-body">
        <div class="alert alert-error mb-4">
          <strong>Every player on every board in {{ form.full_id }} is wiped.</strong>
          The hub resets its own game now, and each member's game resets on its next
          run after it downloads the {{ resetSignal(form.game_type) }}. This can't be undone once members
          have run it.
        </div>

        <h4>What changes</h4>
        <p class="text-muted small">
          Against
          <template v-if="form.last_applied">the last reset from the console.</template>
          <template v-else>
            the game's defaults: no reset has been performed from the console yet<template
              v-if="form.observed"
            >, so only Turns of Protection, Individual Attacks and Lost Attacks are
              known from the league's packets</template>.
          </template>
        </p>
        <table v-if="changes.length" class="table changes">
          <thead>
            <tr><th>Setting</th><th>Now</th><th>After the reset</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in changes" :key="c.label">
              <td>{{ c.label }}</td>
              <td class="text-muted">{{ c.from }}</td>
              <td><strong>{{ c.to }}</strong></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="small">Nothing: the same settings as now, with a new game.</p>

        <template v-if="draft.warnings.length">
          <h4>Below the usual minimum</h4>
          <ul class="warnings">
            <li v-for="w in draft.warnings" :key="w.field">
              <strong>{{ labelOf(w.field) }}:</strong> {{ w.message }}
            </li>
          </ul>
        </template>

        <label for="confirm-code" class="confirm-label">
          Type <code>{{ form.full_id }}</code> to confirm
        </label>
        <input
          id="confirm-code"
          v-model="typed"
          type="text"
          autocomplete="off"
          spellcheck="false"
          :placeholder="form.full_id"
          @keyup.enter="matches && !busy && perform()"
        />
        <div v-if="error" class="alert alert-error mt-2">{{ error }}</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" :disabled="busy" @click="emit('close')">Cancel</button>
        <button class="btn btn-danger" :disabled="!matches || busy" @click="perform">
          {{ busy ? 'Starting...' : `Reset ${form.full_id}` }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 1rem;
}

.modal {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 640px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.5rem;
  border-bottom: 1px solid var(--color-border);
}

.modal-header h3 {
  margin: 0;
}

.modal-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: var(--color-text-muted);
  line-height: 1;
}

.modal-body {
  padding: 1.5rem;
  overflow-y: auto;
}

.modal-body h4 {
  margin: 1rem 0 0.25rem;
  font-size: 0.95rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid var(--color-border);
}

.small {
  font-size: 0.85rem;
}

.changes td,
.changes th {
  font-size: 0.85rem;
}

.warnings {
  margin: 0.25rem 0 0;
  padding-left: 1.25rem;
  font-size: 0.85rem;
  color: var(--color-warning);
}

.confirm-label {
  display: block;
  margin: 1.25rem 0 0.375rem;
  font-weight: 600;
}
</style>
