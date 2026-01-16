<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useUsersStore, type User } from '@/stores/users'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/AppLayout.vue'

const usersStore = useUsersStore()
const authStore = useAuthStore()

const showCreateModal = ref(false)
const showEditModal = ref(false)
const showDeleteModal = ref(false)

const editingUser = ref<User | null>(null)

// Create form
const newUsername = ref('')
const newPassword = ref('')
const newIsAdmin = ref(false)

// Edit form
const editUsername = ref('')
const editPassword = ref('')
const editIsAdmin = ref(false)

onMounted(async () => {
  await usersStore.loadUsers()
})

function openCreateModal() {
  newUsername.value = ''
  newPassword.value = ''
  newIsAdmin.value = false
  usersStore.clearError()
  showCreateModal.value = true
}

function openEditModal(user: User) {
  editingUser.value = user
  editUsername.value = user.username
  editPassword.value = ''
  editIsAdmin.value = user.is_admin
  usersStore.clearError()
  showEditModal.value = true
}

function openDeleteModal(user: User) {
  editingUser.value = user
  showDeleteModal.value = true
}

async function handleCreate() {
  if (!newUsername.value || !newPassword.value) return

  const success = await usersStore.createUser({
    username: newUsername.value,
    password: newPassword.value,
    is_admin: newIsAdmin.value
  })

  if (success) {
    showCreateModal.value = false
  }
}

async function handleEdit() {
  if (!editingUser.value || !editUsername.value) return

  const data: { username?: string; password?: string; is_admin?: boolean } = {}

  if (editUsername.value !== editingUser.value.username) {
    data.username = editUsername.value
  }

  if (editPassword.value) {
    data.password = editPassword.value
  }

  if (editIsAdmin.value !== editingUser.value.is_admin) {
    data.is_admin = editIsAdmin.value
  }

  // Only update if there are changes
  if (Object.keys(data).length === 0) {
    showEditModal.value = false
    return
  }

  const success = await usersStore.updateUser(editingUser.value.id, data)

  if (success) {
    showEditModal.value = false
    editingUser.value = null
  }
}

async function handleDelete() {
  if (!editingUser.value) return

  const success = await usersStore.deleteUser(editingUser.value.id)

  if (success) {
    showDeleteModal.value = false
    editingUser.value = null
  }
}

function canDelete(user: User): boolean {
  // Can't delete yourself
  return user.id !== authStore.user?.id
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>User Management</h1>
          <p class="text-muted">Manage sysop user accounts</p>
        </div>
        <button class="btn btn-primary" @click="openCreateModal">
          Add User
        </button>
      </header>

      <!-- Loading -->
      <div v-if="usersStore.loading && usersStore.users.length === 0" class="loading-state">
        <div class="spinner"></div>
        <p>Loading users...</p>
      </div>

      <!-- Error -->
      <div v-else-if="usersStore.error && usersStore.users.length === 0" class="alert alert-error">
        {{ usersStore.error }}
        <button class="btn btn-sm btn-secondary mt-2" @click="usersStore.loadUsers">Retry</button>
      </div>

      <!-- Users Table -->
      <div v-else class="card">
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>Username</th>
                <th>Role</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="usersStore.users.length === 0">
                <td colspan="4" class="text-center text-muted" style="padding: 2rem;">
                  No users found
                </td>
              </tr>
              <tr
                v-for="user in usersStore.users"
                :key="user.id"
                :class="{ 'row-current': user.id === authStore.user?.id }"
              >
                <td>
                  <span class="username">{{ user.username }}</span>
                  <span v-if="user.id === authStore.user?.id" class="badge badge-info ml-2">You</span>
                </td>
                <td>
                  <span class="badge" :class="user.is_admin ? 'badge-warning' : 'badge-secondary'">
                    {{ user.is_admin ? 'Admin' : 'User' }}
                  </span>
                </td>
                <td class="text-muted">{{ user.created_at || '-' }}</td>
                <td class="actions">
                  <button class="btn btn-sm btn-secondary" @click="openEditModal(user)">
                    Edit
                  </button>
                  <button
                    v-if="canDelete(user)"
                    class="btn btn-sm btn-danger"
                    @click="openDeleteModal(user)"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Create Modal -->
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Add New User</h3>
            <button class="modal-close" @click="showCreateModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleCreate">
            <div class="modal-body">
              <div v-if="usersStore.error" class="alert alert-error mb-4">
                {{ usersStore.error }}
              </div>
              <div class="form-group">
                <label for="newUsername">Username</label>
                <input
                  id="newUsername"
                  v-model="newUsername"
                  type="text"
                  placeholder="Enter username"
                  required
                />
              </div>
              <div class="form-group">
                <label for="newPassword">Password</label>
                <input
                  id="newPassword"
                  v-model="newPassword"
                  type="password"
                  placeholder="Enter password"
                  required
                />
              </div>
              <div class="form-group">
                <label class="checkbox-label">
                  <input type="checkbox" v-model="newIsAdmin" />
                  <span>Admin privileges</span>
                </label>
                <small class="text-muted">Admins can manage users, clients, and leagues</small>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" @click="showCreateModal = false">
                Cancel
              </button>
              <button
                type="submit"
                class="btn btn-primary"
                :disabled="usersStore.loading || !newUsername || !newPassword"
              >
                Create User
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Edit Modal -->
      <div v-if="showEditModal" class="modal-overlay" @click.self="showEditModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Edit User</h3>
            <button class="modal-close" @click="showEditModal = false">&times;</button>
          </div>
          <form @submit.prevent="handleEdit">
            <div class="modal-body">
              <div v-if="usersStore.error" class="alert alert-error mb-4">
                {{ usersStore.error }}
              </div>
              <div class="form-group">
                <label for="editUsername">Username</label>
                <input
                  id="editUsername"
                  v-model="editUsername"
                  type="text"
                  placeholder="Enter username"
                  required
                />
              </div>
              <div class="form-group">
                <label for="editPassword">New Password</label>
                <input
                  id="editPassword"
                  v-model="editPassword"
                  type="password"
                  placeholder="Leave blank to keep current"
                />
                <small class="text-muted">Leave blank to keep current password</small>
              </div>
              <div class="form-group">
                <label class="checkbox-label">
                  <input
                    type="checkbox"
                    v-model="editIsAdmin"
                    :disabled="editingUser?.id === authStore.user?.id"
                  />
                  <span>Admin privileges</span>
                </label>
                <small v-if="editingUser?.id === authStore.user?.id" class="text-muted">
                  You cannot change your own admin status
                </small>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" @click="showEditModal = false">
                Cancel
              </button>
              <button
                type="submit"
                class="btn btn-primary"
                :disabled="usersStore.loading || !editUsername"
              >
                Save Changes
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- Delete Modal -->
      <div v-if="showDeleteModal" class="modal-overlay" @click.self="showDeleteModal = false">
        <div class="modal">
          <div class="modal-header">
            <h3>Delete User</h3>
            <button class="modal-close" @click="showDeleteModal = false">&times;</button>
          </div>
          <div class="modal-body">
            <p>Are you sure you want to delete <strong>{{ editingUser?.username }}</strong>?</p>
            <p class="text-muted mt-2">This action cannot be undone.</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" @click="showDeleteModal = false">
              Cancel
            </button>
            <button
              type="button"
              class="btn btn-danger"
              @click="handleDelete"
              :disabled="usersStore.loading"
            >
              Delete User
            </button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<style scoped>
.page {
  max-width: 1000px;
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

.username {
  font-weight: 500;
}

.row-current {
  background: rgba(59, 130, 246, 0.05);
}

.ml-2 {
  margin-left: 0.5rem;
}

.badge-warning {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
}

.actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
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

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.checkbox-label input[type="checkbox"] {
  width: auto;
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

.mb-4 {
  margin-bottom: 1rem;
}

.mt-2 {
  margin-top: 0.5rem;
}
</style>
