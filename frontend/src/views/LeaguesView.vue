<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useLeaguesStore } from '@/stores/leagues'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'

const leaguesStore = useLeaguesStore()
const authStore = useAuthStore()

const showCreateModal = ref(false)
const newLeagueId = ref('')
const newGameType = ref('B')
const newName = ref('')
const newDescription = ref('')

onMounted(async () => {
  await leaguesStore.loadLeagues()
})

function openCreateModal() {
  newLeagueId.value = ''
  newGameType.value = 'B'
  newName.value = ''
  newDescription.value = ''
  leaguesStore.clearError()
  showCreateModal.value = true
}

async function handleCreate() {
  if (!newLeagueId.value || !newName.value) return

  const success = await leaguesStore.createLeague({
    league_id: newLeagueId.value,
    game_type: newGameType.value,
    name: newName.value,
    description: newDescription.value || undefined
  })
  if (success) {
    showCreateModal.value = false
  }
}

function gameTypeName(type: string): string {
  return type === 'B' ? 'BRE' : type === 'F' ? "Falcon's Eye" : type
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
          Add League
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

      <!-- Create Modal -->
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Add New League</h3>
            <button class="modal-close" @click="showCreateModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleCreate">
            <div class="modal-body">
              <div v-if="leaguesStore.error" class="alert alert-error mb-4">
                {{ leaguesStore.error }}
              </div>
              <div class="form-row">
                <div class="form-group">
                  <label for="leagueId">League Number</label>
                  <input
                    id="leagueId"
                    v-model="newLeagueId"
                    type="text"
                    placeholder="e.g., 555"
                    pattern="[0-9]{3}"
                    maxlength="3"
                    required
                  />
                  <small class="text-muted">3-digit number</small>
                </div>
                <div class="form-group">
                  <label for="gameType">Game Type</label>
                  <select id="gameType" v-model="newGameType" required>
                    <option value="B">BRE (Barren Realms Elite)</option>
                    <option value="F">Falcon's Eye</option>
                  </select>
                </div>
              </div>
              <div class="form-group">
                <label for="name">League Name</label>
                <input
                  id="name"
                  v-model="newName"
                  type="text"
                  placeholder="e.g., My BRE League"
                  required
                />
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
              <button
                type="submit"
                class="btn btn-primary"
                :disabled="leaguesStore.loading || !newLeagueId || !newName"
              >
                Create League
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
