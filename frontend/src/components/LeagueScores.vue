<script setup lang="ts">
// The league's current standings: the newest copy of each score file the
// hub's game wrote, with when it was made and the run that made it (#127).
import { computed, onMounted, ref, watch } from 'vue'
import { leaguesApi, type LeagueScoreFile } from '@/services/api'

const props = defineProps<{ leagueId: number }>()

const files = ref<LeagueScoreFile[] | null>(null)
const failed = ref(false)
const selected = ref<string | null>(null)

const shown = computed(() =>
  files.value?.find((f) => f.filename === selected.value) ?? files.value?.[0] ?? null)

async function load() {
  const id = props.leagueId
  files.value = null
  failed.value = false
  try {
    const { data } = await leaguesApi.scores(id)
    if (id !== props.leagueId) return  // another league's page since
    files.value = data
    if (!data.some((f) => f.filename === selected.value)) selected.value = null
  } catch {
    if (id === props.leagueId) failed.value = true
  }
}

onMounted(load)
watch(() => props.leagueId, load)
</script>

<template>
  <div class="card mt-4">
    <div class="card-header scores-header">
      <h3>Scores</h3>
      <span v-if="shown" class="text-muted">
        {{ shown.label }}, as of {{ new Date(shown.generated_at).toLocaleString() }} ·
        <router-link :to="`/processing/${shown.processing_run_id}`">
          run #{{ shown.processing_run_id }}
        </router-link>
      </span>
    </div>
    <div class="card-body">
      <p v-if="failed" class="text-muted">The scores could not be loaded.</p>
      <p v-else-if="files === null" class="text-muted">Loading scores...</p>
      <p v-else-if="files.length === 0" class="text-muted">
        No score files yet: the hub's game writes them when it runs.
      </p>
      <template v-else>
        <div class="score-tabs">
          <button
            v-for="f in files"
            :key="f.filename"
            type="button"
            class="btn btn-sm"
            :class="f.filename === shown?.filename ? 'btn-primary' : 'btn-secondary'"
            :title="f.filename"
            @click="selected = f.filename"
          >
            {{ f.label }}
          </button>
        </div>
        <div
          v-if="shown?.file_data_html"
          class="score-output ansi-output"
          v-html="shown.file_data_html"
        ></div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.scores-header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
}

.score-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.score-output {
  border-radius: var(--radius-md);
  padding: 1rem;
  overflow-x: auto;
  white-space: pre;
  font-family: monospace;
  font-size: 0.875rem;
  line-height: 1.3;
  max-height: 600px;
  overflow-y: auto;
}

.ansi-output {
  background: #1a1a2e;
  color: #e0e0e0;
}
</style>
