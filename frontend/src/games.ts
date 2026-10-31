// The games the hub serves. Mirrors GAMES in backend/services/games.py;
// add a row to both when adding a game.
export interface Game {
  letter: string // league ids (555B) and League.game_type
  code: string // short name, as in the delete confirmation "BRE_555"
  name: string
}

export const GAMES: Game[] = [
  { letter: 'B', code: 'BRE', name: 'Barren Realms Elite' },
  { letter: 'F', code: 'FE', name: "Falcon's Eye" },
  { letter: 'I', code: 'IB', name: 'Immortal Barons' }
]

export function gameForLetter(letter: string): Game | undefined {
  return GAMES.find((g) => g.letter === letter)
}

export function gameTypeName(letter: string): string {
  return gameForLetter(letter)?.name ?? letter
}

// What tells a league's members to reset: BRE and FE send a Game Start, an
// Immortal Barons Coordinator a signed reset order.
export function resetSignal(letter: string): string {
  return letter === 'I' ? 'reset order' : 'Game Start'
}

// How a reset setting left blank reads: for a date, the game's own "none".
export function blankResetValue(id: string, kind: string): string {
  if (kind === 'date') return id === 'join_date' ? 'no cut-off' : 'day of reset'
  return kind === 'text' ? 'none' : '—'
}
