// API token storage: sessionStorage only (web/CLAUDE.md). Never log the token.
const KEY = "cteforge.token";

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // Storage unavailable (private mode); the session just won't persist across reloads.
  }
}

export function clearToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
