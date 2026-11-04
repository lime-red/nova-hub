<script setup lang="ts">
/**
 * The filter row that Processing Runs, Movements and Attacks share (#125).
 *
 * Window first, then league, then whatever the page adds in the slot, in the
 * same place on every page. The page owns the values (normally URL-backed
 * with useQueryRef) and passes them in with v-model.
 */
import { useLeagues } from '@/composables/useLeagues'

const days = defineModel<number>('days', { required: true })
const leagueId = defineModel<number | undefined>('leagueId')

withDefaults(defineProps<{
  /** The windows offered, in days. 0 means all time. */
  dayOptions?: number[]
  /** What the window is measured on, e.g. "Started" or "Launched". */
  windowLabel?: string
  /** Show Clear: the page knows which of its filters are set. */
  active?: boolean
}>(), {
  dayOptions: () => [1, 7, 30, 90],
  windowLabel: 'Window',
  active: false,
})

const emit = defineEmits<{ clear: [] }>()

const { leagues, leagueLabel } = useLeagues()

function windowName(d: number): string {
  if (d === 0) return 'All time'
  if (d === 1) return 'Last 24 hours'
  return `Last ${d} days`
}
</script>

<template>
  <div class="card filter-row">
    <label class="filter-field">
      <span class="text-muted">{{ windowLabel }}</span>
      <select v-model.number="days" class="form-select">
        <option v-for="d in dayOptions" :key="d" :value="d">{{ windowName(d) }}</option>
      </select>
    </label>
    <select v-model="leagueId" class="form-select" aria-label="League">
      <option :value="undefined">All leagues</option>
      <option v-for="l in leagues" :key="l.id" :value="l.id">{{ leagueLabel(l.id, l.name) }}</option>
    </select>
    <slot />
    <button v-if="active" type="button" class="btn btn-sm btn-secondary" @click="emit('clear')">
      Clear
    </button>
  </div>
</template>

<style scoped>
.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
}

.filter-field {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
}

/* The global form rule makes every select and input full width; here they
   sit side by side and size to their content. */
.filter-row :deep(select),
.filter-row :deep(input:not([type="checkbox"])) {
  width: auto;
  max-width: 100%;
}

.filter-row :deep(input.narrow) {
  width: 6rem;
}

.filter-row :deep(input.id-input) {
  width: 11rem;
}
</style>
