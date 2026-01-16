<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useLeaguesStore } from '@/stores/leagues'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'

const route = useRoute()
const router = useRouter()
const leaguesStore = useLeaguesStore()
const authStore = useAuthStore()

const leagueId = computed(() => Number(route.params.id))

const showAddMemberModal = ref(false)
const showDeleteModal = ref(false)
const selectedClientId = ref<number | null>(null)
const newBbsIndex = ref('')
const newFidonet = ref('')

onMounted(async () => {
  await leaguesStore.loadLeague(leagueId.value)
})

function gameTypeName(type: string): string {
  return type === 'B' ? 'BRE' : type === 'F' ? "Falcon's Eye" : type
}

function formatBbsIndex(index: number): string {
  return index.toString().padStart(2, '0')
}

function openAddMemberModal() {
  selectedClientId.value = null
  newBbsIndex.value = ''
  newFidonet.value = ''
  leaguesStore.clearError()
  showAddMemberModal.value = true
}

async function handleAddMember() {
  if (!selectedClientId.value || !newBbsIndex.value || !newFidonet.value) return

  const success = await leaguesStore.addMember(leagueId.value, {
    client_id: selectedClientId.value,
    bbs_index: parseInt(newBbsIndex.value),
    fidonet_address: newFidonet.value
  })
  if (success) {
    showAddMemberModal.value = false
  }
}

async function handleRemoveMember(clientId: number) {
  if (confirm('Remove this member from the league?')) {
    await leaguesStore.removeMember(leagueId.value, clientId)
  }
}

async function handleToggleActive() {
  if (leaguesStore.currentLeague) {
    await leaguesStore.updateLeague(leagueId.value, {
      is_active: !leaguesStore.currentLeague.is_active
    })
  }
}

async function handleDelete() {
  const success = await leaguesStore.deleteLeague(leagueId.value)
  if (success) {
    router.push('/leagues')
  }
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <!-- Loading -->
      <div v-if="leaguesStore.loading && !leaguesStore.currentLeague" class="loading-state">
        <div class="spinner"></div>
        <p>Loading league...</p>
      </div>

      <!-- Error -->
      <div v-else-if="leaguesStore.error && !leaguesStore.currentLeague" class="alert alert-error">
        {{ leaguesStore.error }}
        <router-link to="/leagues" class="btn btn-sm btn-secondary mt-2">Back to Leagues</router-link>
      </div>

      <!-- League Details -->
      <template v-else-if="leaguesStore.currentLeague">
        <header class="page-header">
          <div>
            <div class="breadcrumb">
              <router-link to="/leagues">Leagues</router-link>
              <span>/</span>
              <span>{{ leaguesStore.currentLeague.full_id }}</span>
            </div>
            <h1>{{ leaguesStore.currentLeague.name }}</h1>
            <p class="text-muted">
              {{ gameTypeName(leaguesStore.currentLeague.game_type) }} League {{ leaguesStore.currentLeague.league_id }}
            </p>
          </div>
          <div v-if="authStore.isAdmin" class="actions">
            <button
              class="btn"
              :class="leaguesStore.currentLeague.is_active ? 'btn-secondary' : 'btn-primary'"
              @click="handleToggleActive"
            >
              {{ leaguesStore.currentLeague.is_active ? 'Disable' : 'Enable' }}
            </button>
            <button class="btn btn-danger" @click="showDeleteModal = true">Delete</button>
          </div>
        </header>

        <div class="content-grid">
          <!-- Info Card -->
          <div class="card">
            <div class="card-header">
              <h3>League Information</h3>
            </div>
            <div class="card-body">
              <dl class="info-list">
                <div class="info-item">
                  <dt>Full ID</dt>
                  <dd class="font-mono">{{ leaguesStore.currentLeague.full_id }}</dd>
                </div>
                <div class="info-item">
                  <dt>Status</dt>
                  <dd>
                    <span
                      class="badge"
                      :class="leaguesStore.currentLeague.is_active ? 'badge-success' : 'badge-danger'"
                    >
                      {{ leaguesStore.currentLeague.is_active ? 'Active' : 'Inactive' }}
                    </span>
                  </dd>
                </div>
                <div class="info-item">
                  <dt>Game Type</dt>
                  <dd>{{ gameTypeName(leaguesStore.currentLeague.game_type) }}</dd>
                </div>
                <div v-if="leaguesStore.currentLeague.description" class="info-item">
                  <dt>Description</dt>
                  <dd>{{ leaguesStore.currentLeague.description }}</dd>
                </div>
              </dl>
            </div>
          </div>

          <!-- Stats Card -->
          <div class="card">
            <div class="card-header">
              <h3>Statistics</h3>
            </div>
            <div class="card-body">
              <div class="stats-grid">
                <div class="stat">
                  <div class="stat-value">{{ leaguesStore.currentLeague.members.length }}</div>
                  <div class="stat-label">Members</div>
                </div>
                <div class="stat">
                  <div class="stat-value">{{ leaguesStore.currentLeague.stats.total_packets }}</div>
                  <div class="stat-label">Total Packets</div>
                </div>
                <div class="stat">
                  <div class="stat-value">{{ leaguesStore.currentLeague.stats.processed_packets }}</div>
                  <div class="stat-label">Processed</div>
                </div>
                <div class="stat">
                  <div class="stat-value">{{ leaguesStore.currentLeague.stats.processing_runs }}</div>
                  <div class="stat-label">Processing Runs</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Members -->
        <div class="card mt-4">
          <div class="card-header">
            <h3>League Members</h3>
            <button
              v-if="authStore.isAdmin"
              class="btn btn-primary btn-sm"
              @click="openAddMemberModal"
            >
              Add Member
            </button>
          </div>
          <div class="card-body" style="padding: 0;">
            <table class="table">
              <thead>
                <tr>
                  <th>BBS Index</th>
                  <th>BBS Name</th>
                  <th>Fidonet Address</th>
                  <th>Status</th>
                  <th>Joined</th>
                  <th v-if="authStore.isAdmin"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="leaguesStore.currentLeague.members.length === 0">
                  <td :colspan="authStore.isAdmin ? 6 : 5" class="text-center text-muted" style="padding: 2rem;">
                    No members in this league
                  </td>
                </tr>
                <tr v-for="member in leaguesStore.currentLeague.members" :key="member.membership_id">
                  <td class="font-mono">{{ formatBbsIndex(member.bbs_index) }}</td>
                  <td>
                    <router-link :to="`/clients/${member.client_id}`">{{ member.bbs_name }}</router-link>
                  </td>
                  <td class="font-mono">{{ member.fidonet_address || '-' }}</td>
                  <td>
                    <span class="badge" :class="member.is_active ? 'badge-success' : 'badge-danger'">
                      {{ member.is_active ? 'Active' : 'Inactive' }}
                    </span>
                  </td>
                  <td class="text-muted">{{ member.joined_at || '-' }}</td>
                  <td v-if="authStore.isAdmin" class="actions">
                    <button
                      class="btn btn-sm btn-danger"
                      @click="handleRemoveMember(member.client_id)"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>

      <!-- Add Member Modal -->
      <div v-if="showAddMemberModal" class="modal-overlay" @click.self="showAddMemberModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Add League Member</h3>
            <button class="modal-close" @click="showAddMemberModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleAddMember">
            <div class="modal-body">
              <div v-if="leaguesStore.error" class="alert alert-error mb-4">
                {{ leaguesStore.error }}
              </div>
              <template v-if="leaguesStore.currentLeague?.available_clients?.length">
                <div class="form-group">
                  <label for="client">Select Client</label>
                  <select id="client" v-model="selectedClientId" required>
                    <option :value="null" disabled>Choose a client...</option>
                    <option
                      v-for="client in leaguesStore.currentLeague?.available_clients"
                      :key="client.id"
                      :value="client.id"
                    >
                      {{ client.bbs_name }} ({{ client.client_id }})
                    </option>
                  </select>
                </div>
              <div class="form-group">
                <label for="bbsIndex">BBS Index</label>
                <input
                  id="bbsIndex"
                  v-model="newBbsIndex"
                  type="number"
                  min="1"
                  max="255"
                  placeholder="1-255"
                  required
                />
                <small class="text-muted">Unique identifier for this BBS in the league (1-255)</small>
              </div>
              <div class="form-group">
                <label for="fidonet">Fidonet Address</label>
                <input
                  id="fidonet"
                  v-model="newFidonet"
                  type="text"
                  placeholder="e.g., 13:10/100"
                  pattern="\d+:\d+/\d+"
                  required
                />
                <small class="text-muted">Format: zone:net/node (e.g., 13:10/100)</small>
              </div>
              </template>
              <p v-else class="text-muted">All active clients are already members of this league.</p>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" @click="showAddMemberModal = false">
                Cancel
              </button>
              <button
                v-if="leaguesStore.currentLeague?.available_clients?.length"
                type="submit"
                class="btn btn-primary"
                :disabled="leaguesStore.loading || !selectedClientId || !newBbsIndex || !newFidonet"
              >
                Add Member
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Delete Modal -->
      <div v-if="showDeleteModal" class="modal-overlay" @click.self="showDeleteModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Delete League</h3>
            <button class="modal-close" @click="showDeleteModal = false">&times;</button>
          </div>
          <div class="modal-body">
            <p>Are you sure you want to delete <strong>{{ leaguesStore.currentLeague?.name }}</strong>?</p>
            <p class="text-muted mt-2">This will also remove all member associations. This action cannot be undone.</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showDeleteModal = false">
              Cancel
            </button>
            <button type="button" class="btn btn-danger" @click="handleDelete" :disabled="leaguesStore.loading">
              Delete League
            </button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
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
  margin-bottom: 0.25rem;
}

.breadcrumb a {
  color: var(--color-primary);
}

.actions {
  display: flex;
  gap: 0.5rem;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4rem;
  gap: 1rem;
}

.content-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.info-list {
  margin: 0;
}

.info-item {
  display: flex;
  justify-content: space-between;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border);
}

.info-item:last-child {
  border-bottom: none;
}

.info-item dt {
  color: var(--color-text-muted);
}

.info-item dd {
  margin: 0;
  font-weight: 500;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

.stat {
  text-align: center;
  padding: 1rem;
  background: var(--color-bg);
  border-radius: var(--radius-md);
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-primary);
}

.stat-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
}

.mt-4 {
  margin-top: 1rem;
}

.mb-4 {
  margin-bottom: 1rem;
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

table .actions {
  justify-content: flex-end;
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
  max-width: 480px;
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
