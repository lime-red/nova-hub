// The games the hub serves. Mirrors GAMES in backend/services/games.py;
// add a row to both when adding a game.
export interface Game {
  letter: string // league ids (555B) and League.game_type
  code: string // short name, as in the delete confirmation "BRE_555"
  name: string
}

export const GAMES: Game[] = [
  { letter: 'B', code: 'BRE', name: 'Barren Realms Elite' },
  { letter: 'F', code: 'FE', name: "Falcon's Eye" }
]

export function gameForLetter(letter: string): Game | undefined {
  return GAMES.find((g) => g.letter === letter)
}

export function gameTypeName(letter: string): string {
  return gameForLetter(letter)?.name ?? letter
}
