// Helper to determine if we should use secure protocols (https/wss)
const isSecure = import.meta.env.VITE_IS_SECURE === 'true';

// Backend URL without protocol (e.g., "localhost:8000" or "api.example.com")
const backendHost = import.meta.env.VITE_BACKEND_URL || 'localhost:8000';

// Where the hosted service lives. Canonical addresses and link-preview images
// must be absolute, and are built from this rather than from the origin the
// page happens to be served on — so a preview deployment advertises the real
// site instead of naming itself.
export const SITE_URL = 'https://talleyrand.app';

// The public repository, so a visitor can check the open-source claim, star it,
// or self-host without going looking for it.
export const REPO_URL = 'https://github.com/Sage-Future/Talleyrand';

// Set by ./start-local.sh, alongside the backend's AGENT_BACKEND: model calls
// run on the operator's signed-in agent CLI, so no API key is needed.
// ponytail: a build-time flag the script keeps in step with the backend; ask
// the backend instead if the two ever get started separately.
export const AGENT_BACKEND = import.meta.env.VITE_AGENT_BACKEND || null;

export const BACKEND_CONFIG = {
  // Full HTTP URL for REST API calls
  BACKEND_HTTP_URL: `${isSecure ? 'https' : 'http'}://${backendHost}/backend`,
  // Full WebSocket URL
  BACKEND_WS_URL: `${isSecure ? 'wss' : 'ws'}://${backendHost}/backend`,
};
