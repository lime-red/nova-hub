import { ref } from 'vue'

// navigator.clipboard only exists in a secure context (https or localhost). A
// hub served over plain http gets the older execCommand copy, which browsers
// still allow from a click; if that is refused too, the text beside the button
// is selected so it can be copied by hand.

export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // fall through to the older route
    }
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
  }
}

export function selectText(el: Element | null | undefined) {
  if (!el) return
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    el.focus()
    el.select()
    return
  }
  const range = document.createRange()
  range.selectNodeContents(el)
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
}

/**
 * Copy buttons that say what happened. Pass the click event, and when the
 * copy is refused the element just before the button (the input or text being
 * copied) is selected instead.
 */
export function useCopy() {
  const state = ref<{ key: string; ok: boolean } | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined

  async function copy(key: string, text: string, event?: Event) {
    // currentTarget is gone once the handler awaits
    const beside = (event?.currentTarget as HTMLElement | null)?.previousElementSibling
    const ok = await copyText(text)
    if (!ok) selectText(beside)
    state.value = { key, ok }
    clearTimeout(timer)
    timer = setTimeout(() => (state.value = null), ok ? 1500 : 8000)
  }

  function label(key: string): string {
    if (state.value?.key !== key) return 'Copy'
    return state.value.ok ? 'Copied' : 'Selected: copy it by hand'
  }

  return { copy, label }
}
