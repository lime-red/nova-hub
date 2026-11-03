<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiError, imagesApi, type GameImageInfo, type ImagesList } from '@/services/api'
import { gameTypeName } from '@/games'
import AppLayout from '@/components/AppLayout.vue'

// The golden images leagues' games are made from. Admin only: a BRE or FE
// image carries the game's registration keys.

const data = ref<ImagesList | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const busy = ref(false)

const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const fetchName = ref('')

// Check results by image id: null while checking, then the problems found.
const checks = ref<Record<number, string[] | null>>({})

async function load() {
  loading.value = true
  error.value = null
  try {
    data.value = (await imagesApi.list()).data
  } catch (e: any) {
    error.value = apiError(e, 'Could not load the images.')
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function take(what: string, call: () => Promise<{ data: { image: GameImageInfo; created: boolean } }>) {
  busy.value = true
  error.value = null
  notice.value = null
  try {
    const { image, created } = (await call()).data
    notice.value = created
      ? `${image.dirname} is in: every file matched its manifest.`
      : `The hub already had ${image.dirname}, exactly as ${what} has it.`
    await load()
    return true
  } catch (e: any) {
    error.value = apiError(e, `Could not take in ${what}.`)
    return false
  } finally {
    busy.value = false
  }
}

function pickFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function upload() {
  if (!file.value) return
  const f = file.value
  if (await take(f.name, () => imagesApi.upload(f))) {
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function fetchImage() {
  const name = fetchName.value.trim()
  if (name && await take(name, () => imagesApi.fetch(name))) fetchName.value = ''
}

async function importWaiting(name: string) {
  await take(name, () => imagesApi.importWaiting(name))
}

async function check(image: GameImageInfo) {
  checks.value[image.id] = null
  try {
    checks.value[image.id] = (await imagesApi.check(image.id)).data.problems
  } catch (e: any) {
    checks.value[image.id] = [apiError(e, 'Could not check it.')]
  }
}

async function remove(image: GameImageInfo) {
  if (!confirm(`Delete ${image.dirname}? Its files go too.`)) return
  error.value = null
  notice.value = null
  try {
    await imagesApi.remove(image.id)
    notice.value = `${image.dirname} is deleted.`
    await load()
  } catch (e: any) {
    error.value = apiError(e, `Could not delete ${image.dirname}.`)
  }
}

const canFetch = computed(() => !!data.value?.fetch_from && !data.value.fetch_problems.length)

function size(bytes: number): string {
  return bytes >= 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
}
</script>

<template>
  <AppLayout>
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Game Images</h1>
          <p class="text-muted">
            The clean installs leagues' games are made from, one per game version.
            make_golden.py builds them from your own copy of a game.
          </p>
        </div>
      </header>

      <div v-if="error" class="alert alert-error">{{ error }}</div>
      <div v-if="notice" class="alert alert-success">{{ notice }}</div>

      <div class="card">
        <div class="card-header"><h2>Add an image</h2></div>
        <div class="card-body add">
          <div class="add-way">
            <label class="form-label" for="image-file">Upload</label>
            <div class="row">
              <input id="image-file" ref="fileInput" type="file" accept=".tar.gz,.gz" @change="pickFile" />
              <button class="btn btn-primary btn-sm" :disabled="!file || busy" @click="upload">
                {{ busy ? 'Checking…' : 'Upload' }}
              </button>
            </div>
          </div>

          <div v-if="data?.fetch_from" class="add-way">
            <label class="form-label" for="fetch-name">Fetch from <span class="font-mono">{{ data.fetch_from }}</span></label>
            <div class="row">
              <input
                id="fetch-name"
                v-model="fetchName"
                type="text"
                class="font-mono"
                placeholder="bre-sj1-r1.tar.gz"
                :disabled="!canFetch"
                @keyup.enter="fetchImage"
              />
              <button class="btn btn-primary btn-sm" :disabled="!canFetch || !fetchName.trim() || busy" @click="fetchImage">
                Fetch
              </button>
            </div>
            <ul v-if="data.fetch_problems.length" class="problems">
              <li v-for="p in data.fetch_problems" :key="p">{{ p }}</li>
            </ul>
          </div>

          <p v-if="data" class="form-hint">
            Or copy the archive into <span class="font-mono">{{ data.folder }}</span> on the hub;
            it is listed below to import.
            Every file is checked against the image's manifest before the hub keeps it.
          </p>
        </div>
      </div>

      <div v-if="data?.waiting.length" class="card">
        <div class="card-header"><h2>Waiting to be imported</h2></div>
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <tbody>
              <tr v-for="w in data.waiting" :key="w.name">
                <td class="font-mono">{{ w.name }}</td>
                <td class="text-muted">{{ size(w.size) }}</td>
                <td class="actions">
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="importWaiting(w.name)">Import</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h2>Images</h2></div>
        <div class="card-body" style="padding: 0;">
          <table class="table">
            <thead>
              <tr>
                <th>Image</th>
                <th>Game</th>
                <th>Made from</th>
                <th>Added</th>
                <th>Leagues</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!loading && data && data.images.length === 0">
                <td colspan="6" class="text-center text-muted" style="padding: 2rem;">No images yet</td>
              </tr>
              <tr v-for="image in data?.images ?? []" :key="image.id">
                <td>
                  <div class="font-mono">{{ image.dirname }}</div>
                  <div v-if="image.id in checks" class="small">
                    <span v-if="checks[image.id] === null" class="text-muted">Checking…</span>
                    <span v-else-if="!checks[image.id]!.length" class="badge badge-success">Matches its manifest</span>
                    <ul v-else class="problems">
                      <li v-for="p in checks[image.id]" :key="p">{{ p }}</li>
                    </ul>
                  </div>
                </td>
                <td>{{ gameTypeName(image.game_type) }} <span class="text-muted">{{ image.version }}</span></td>
                <td class="small">
                  {{ image.made_from ?? '-' }}
                  <span v-if="image.recipe" class="text-muted">(recipe {{ image.recipe }})</span>
                  <div v-if="image.source" class="text-muted">{{ image.source }}</div>
                </td>
                <td class="nowrap text-muted small">
                  {{ image.created_at ? new Date(image.created_at).toLocaleDateString() : '-' }}
                </td>
                <td>{{ image.leagues.join(', ') || '-' }}</td>
                <td class="actions">
                  <button class="btn btn-secondary btn-sm" @click="check(image)">Check</button>
                  <button
                    class="btn btn-danger btn-sm"
                    :disabled="image.leagues.length > 0"
                    :title="image.leagues.length ? 'A league\'s game was made from it' : ''"
                    @click="remove(image)"
                  >Delete</button>
                </td>
              </tr>
            </tbody>
          </table>
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
  margin-bottom: 1.5rem;
}

.page-header h1 {
  margin-bottom: 0.25rem;
}

.card {
  margin-bottom: 1.5rem;
}

.card-header h2 {
  font-size: 1rem;
  margin: 0;
}

.add {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
}

.row input[type="text"] {
  max-width: 24rem;
}

.form-label {
  display: block;
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.form-hint {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.problems {
  margin: 0.25rem 0 0;
  padding-left: 1.25rem;
  color: var(--color-danger);
  font-size: 0.8125rem;
}

.actions {
  text-align: right;
  white-space: nowrap;
}

.actions .btn + .btn {
  margin-left: 0.5rem;
}

.nowrap {
  white-space: nowrap;
}

.small {
  font-size: 0.8125rem;
}
</style>
