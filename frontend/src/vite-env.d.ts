/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Analytics measurement ID. Absent on a deployment that collects nothing. */
  readonly VITE_GA_MEASUREMENT_ID?: string;
  /** "claude_code" in a local session (./start-local.sh); absent otherwise. */
  readonly VITE_AGENT_BACKEND?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
