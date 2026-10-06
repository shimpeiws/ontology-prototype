// Minimal ambient declaration so tests can reach Node's own
// AbortController/AbortSignal classes without pulling in @types/node.
declare module "node:util" {
  export function transferableAbortController(): AbortController;
}
