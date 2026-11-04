/**
 * A page filter kept in the URL's query string (#125).
 *
 * The ref is the page's own state, so it updates at once. The URL follows it,
 * which means Back keeps a page's filters, a filtered view can be linked, and
 * a link like /processing?league=3 opens already filtered. Moving through
 * history updates the ref too.
 *
 * A value equal to its default is left out of the URL, so an unfiltered page
 * keeps a bare address.
 */
import { ref, watch, type Ref } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'

type Codec<T> = { parse: (raw: string) => T | undefined; format: (value: T) => string }

export const asNumber: Codec<number> = {
  parse: (raw) => (/^-?\d+$/.test(raw) ? Number(raw) : undefined),
  format: String,
}
export const asString: Codec<string> = { parse: (raw) => raw, format: (v) => v }
export const asBool: Codec<boolean> = {
  parse: (raw) => (raw === '1' ? true : raw === '0' ? false : undefined),
  format: (v) => (v ? '1' : '0'),
}

// Several filters can change in one tick (Clear resets them all). They go to
// the router as one replace, because each replace starts from the route's
// query as it stood and separate ones would undo each other.
let pending: Record<string, string | undefined> | null = null

function first(query: LocationQuery, key: string): string | undefined {
  const raw = query[key]
  const value = Array.isArray(raw) ? raw[0] : raw
  return value ?? undefined
}

export function useQueryRef<T>(key: string, fallback: T, codec: Codec<NonNullable<T>>): Ref<T> {
  const route = useRoute()
  const router = useRouter()

  const read = (): T => {
    const raw = first(route.query, key)
    const parsed = raw === undefined ? undefined : codec.parse(raw)
    return (parsed ?? fallback) as T
  }

  const value = ref(read()) as Ref<T>

  watch(value, (next) => {
    const encoded = next === fallback || next === undefined || next === null || next === ''
      ? undefined
      : codec.format(next as NonNullable<T>)
    if (first(route.query, key) === encoded) return
    if (!pending) {
      pending = {}
      queueMicrotask(() => {
        const query: Record<string, string> = {}
        for (const [k, v] of Object.entries(route.query)) {
          const one = Array.isArray(v) ? v[0] : v
          if (one != null) query[k] = one
        }
        for (const [k, v] of Object.entries(pending ?? {})) {
          if (v === undefined) delete query[k]
          else query[k] = v
        }
        pending = null
        router.replace({ query })
      })
    }
    pending[key] = encoded
  })

  // Back and Forward, or a link to the same page with other filters.
  watch(() => first(route.query, key), () => {
    const next = read()
    if (next !== value.value) value.value = next
  })

  return value
}
