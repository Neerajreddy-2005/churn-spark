// Lets a page that runs its own re-authentication + 2FA step (Account > Change password) tell the admin MfaGate
// not to take over the screen while that flow is in progress. Releasing the hold makes the gate re-check, so an
// abandoned flow still ends with the gate asking for a code if the session dropped to aal1.
let held = false
const listeners = new Set<() => void>()

export const isMfaHeld = () => held
export function holdMfaGate() { held = true }
export function releaseMfaGate() {
  if (!held) return
  held = false
  listeners.forEach((l) => l())
}
export function onMfaRelease(l: () => void) {
  listeners.add(l)
  return () => { listeners.delete(l) }
}
