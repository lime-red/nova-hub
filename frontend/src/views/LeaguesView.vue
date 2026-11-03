<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useLeaguesStore } from '@/stores/leagues'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'
import { GAMES, gameTypeName } from '@/games'
import { apiError, leaguesApi, type NewLeagueForm } from '@/services/api'

const leaguesStore = useLeaguesStore()
const authStore = useAuthStore()
const router = useRouter()

// New League: Set Up makes the league (disabled) and the hub's game for it,
// from a golden image. Its settings come with its first reset, on Game Reset.
const showCreateModal = ref(false)
const form = ref<NewLeagueForm | null>(null)
const newLeagueId = ref('')
const newGameType = ref('B')
const newName = ref('')
const newDescription = ref('')
const newHubAddress = ref('')
const newImageId = ref<number | null>(null)
const setupError = ref<string | null>(null)
const settingUp = ref(false)

onMounted(async () => {
  await leaguesStore.loadLeagues()
})

const imagesForGame = computed(() =>
  (form.value?.images ?? []).filter((i) => i.game_type === newGameType.value))

watch(newGameType, () => {
  newImageId.value = imagesForGame.value[0]?.id ?? null
})

const numberTaken = computed(() =>
  /^\d{3}$/.test(newLeagueId.value)
  && !!form.value?.used_ids.includes(newLeagueId.value + newGameType.value))

const defaultAddress = computed(() => {
  const n = /^\d{3}$/.test(newLeagueId.value) ? Number(newLeagueId.value) : 'n'
  return `${n}:${n}/${form.value?.hub_index ?? 1}`
})

const canSetUp = computed(() =>
  !!form.value && !form.value.problems.length && /^\d{3}$/.test(newLeagueId.value)
  && newLeagueId.value !== '000' && !numberTaken.value && !!newName.value.trim()
  && newImageId.value !== null)

async function openCreateModal() {
  newLeagueId.value = ''
  newGameType.value = 'B'
  newName.value = ''
  newDescription.value = ''
  newHubAddress.value = ''
  setupError.value = null
  form.value = null
  showCreateModal.value = true
  try {
    form.value = (await leaguesApi.newLeagueForm()).data
    newImageId.value = imagesForGame.value[0]?.id ?? null
  } catch (e: unknown) {
    setupError.value = apiError(e, 'Could not load the form.')
  }
}

async function handleCreate() {
  if (!canSetUp.value) return
  settingUp.value = true
  setupError.value = null
  try {
    const { data } = await leaguesApi.setUp({
      league_id: newLeagueId.value,
      game_type: newGameType.value,
      name: newName.value.trim(),
      description: newDescription.value.trim() || undefined,
      hub_fidonet_address: newHubAddress.value.trim() || undefined,
      image_id: newImageId.value ?? undefined,
    })
    showCreateModal.value = false
    router.push(`/leagues/${data.league_id}`)
  } catch (e: unknown) {
    setupError.value = apiError(e, 'Could not set the league up.')
  } finally {
    settingUp.value = false
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Leagues</h1>
          <p class="text-muted">Manage inter-BBS game leagues</p>
        </div>
        <button v-if="authStore.isAdmin" class="btn btn-primary" @click="openCreateModal">
          New League
        </button>
      </header>

      <!-- Loading -->
      <div v-if="leaguesStore.loading && leaguesStore.leagues.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading leagues...</p>
      </div>

      <!-- Error -->
      <div v-else-if="leaguesStore.error" class="alert alert-error">
        {{ leaguesStore.error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="leaguesStore.loadLeagues">Retry</button>
      </div>

      <!-- Leagues Grid -->
      <div v-else class="leagues-grid">
        <div v-if="leaguesStore.leagues.length === 0" class="empty-state card">
          <p>No leagues configured</p>
          <button v-if="authStore.isAdmin" class="btn btn-primary mt-2" @click="openCreateModal">
            Create your first league
          </button>
        </div>

        <router-link
          v-for="league in leaguesStore.leagues"
          :key="league.id"
          :to="`/leagues/${league.id}`"
          class="league-card card"
        >
          <div class="league-header">
            <span class="league-id font-mono">{{ league.full_id }}</span>
            <span class="badge" :class="league.is_active ? 'badge-success' : 'badge-danger'">
              {{ league.is_active ? 'Active' : 'Inactive' }}
            </span>
          </div>
          <h3 class="league-name">{{ league.name }}</h3>
          <p class="league-game text-muted">{{ gameTypeName(league.game_type) }}</p>
          <div class="league-footer">
            <span class="member-count">{{ league.member_count }} member{{ league.member_count !== 1 ? 's' : '' }}</span>
          </div>
        </router-link>
      </div>

      <!-- New League -->
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>New League</h3>
            <button class="modal-close" @click="showCreateModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleCreate">
            <div class="modal-body">
              <p class="text-muted mb-4">
                Set Up makes the league, disabled, and the hub's game for it from a golden
                image. Nothing runs in the game until you send its first reset from Game
                Reset, with the settings you choose.
              </p>
              <div v-if="setupError" class="alert alert-error mb-4">{{ setupError }}</div>
              <div v-if="form?.problems.length" class="alert alert-error mb-4">
                <div v-for="p in form.problems" :key="p">{{ p }}</div>
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label for="leagueId">League Number</label>
                  <input
                    id="leagueId"
                    v-model="newLeagueId"
                    type="text"
                    placeholder="e.g., 016"
                    pattern="[0-9]{3}"
                    maxlength="3"
                    required
                  />
                  <small v-if="numberTaken" class="text-danger">
                    {{ newLeagueId }}{{ newGameType }} already exists
                  </small>
                  <small v-else class="text-muted">3 digits, unused by this game</small>
                </div>
                <div class="form-group">
                  <label for="gameType">Game</label>
                  <select id="gameType" v-model="newGameType" required>
                    <option v-for="g in GAMES" :key="g.letter" :value="g.letter">
                      {{ g.code }} ({{ g.name }})
                    </option>
                  </select>
                </div>
              </div>
              <div class="form-group">
                <label for="name">League Name</label>
                <input
                  id="name"
                  v-model="newName"
                  type="text"
                  placeholder="e.g., Starship Junkyard BRE"
                  required
                />
              </div>
              <div class="form-group">
                <label for="image">Golden Image</label>
                <select id="image" v-model="newImageId" :disabled="!imagesForGame.length">
                  <option v-for="i in imagesForGame" :key="i.id" :value="i.id">
                    {{ i.dirname }}
                  </option>
                </select>
                <small v-if="form && !imagesForGame.length" class="text-danger">
                  No {{ gameTypeName(newGameType) }} image:
                  <router-link to="/admin/images">add one on Game Images</router-link> first
                </small>
                <small v-else class="text-muted">Newest first</small>
              </div>
              <div class="form-group">
                <label for="hubAddress">Hub's FTN Address in the League (optional)</label>
                <input
                  id="hubAddress"
                  v-model="newHubAddress"
                  type="text"
                  class="font-mono"
                  :placeholder="defaultAddress"
                />
                <small class="text-muted">Blank: {{ defaultAddress }}</small>
              </div>
              <div class="form-group">
                <label for="description">Description (optional)</label>
                <textarea
                  id="description"
                  v-model="newDescription"
                  placeholder="Brief description of the league"
                  rows="2"
                ></textarea>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" @click="showCreateModal = false">
                Cancel
              </button>
              <button type="submit" class="btn btn-primary" :disabled="!canSetUp || settingUp">
                {{ settingUp ? 'Starting…' : 'Set Up' }}
              </button>
            </div>
          </form>
        </div>
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
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.empty-state {
  grid-column: 1 / -1;
  padding: 3rem;
  text-align: center;
}

.leagues-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}

.league-card {
  padding: 1.25rem;
  text-decoration: none;
  color: inherit;
  transition: box-shadow 0.15s, transform 0.15s;
}

.league-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.league-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.league-id {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-primary);
}

.league-name {
  margin: 0 0 0.25rem;
  font-size: 1.125rem;
}

.league-game {
  margin: 0;
  font-size: 0.875rem;
}

.league-footer {
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border);
}

.member-count {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.form-group {
  margin-bottom: 1rem;
}

.form-group label {
  display: block;
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.form-group small {
  display: block;
  margin-top: 0.25rem;
}

.mt-2 {
  margin-top: 0.5rem;
}

.mb-4 {
  margin-bottom: 1rem;
}

/* Modal styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 500px;
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

.modal-close:hover {
  color: var(--color-text);
}

.modal-body {
  padding: 1.5rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid var(--color-border);
}
</style>
