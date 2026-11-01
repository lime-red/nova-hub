<script setup lang="ts">
// Copies a table as tab-separated text, header row first, for pasting into a
// spreadsheet or a message. A cell's text is what it shows; a hidden sub-row
// (an expanded detail) is left out with the rest of what isn't rendered.
import { ref } from 'vue'

const props = defineProps<{ table: HTMLElement | null }>()
const copied = ref(false)

function cell(td: Element): string {
  return (td as HTMLElement).innerText.replace(/\s*\n\s*/g, ' ').replace(/\t/g, ' ').trim()
}

function tsv(root: HTMLElement): string {
  const table = root.tagName === 'TABLE' ? root : root.querySelector('table')
  if (!table) return ''
  return Array.from(table.querySelectorAll('tr'))
    .filter(tr => (tr as HTMLElement).offsetParent !== null)
    .map(tr => Array.from(tr.querySelectorAll('th, td')).map(cell).join('\t'))
    .join('\n')
}

async function copy() {
  if (!props.table) return
  const text = tsv(props.table)
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // Not a secure context (plain http on the LAN): the old way still works.
    const area = document.createElement('textarea')
    area.value = text
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    area.remove()
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 1500)
}
</script>

<template>
  <button type="button" class="btn btn-secondary btn-sm" title="Copy the table as tab-separated text"
          @click="copy">
    {{ copied ? 'Copied' : 'Copy as TSV' }}
  </button>
</template>
