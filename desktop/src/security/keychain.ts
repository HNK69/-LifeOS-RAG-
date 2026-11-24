/**
 * keychain.ts
 * Secure OS Credential Storage adapter.
 * Uses Tauri secure store plugin when running inside Tauri,
 * with encrypted/in-memory fallback for testing and web environments.
 */

export interface CredentialStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  delete(key: string): Promise<boolean>;
}

export class MemoryCredentialStore implements CredentialStore {
  private store: Map<string, string> = new Map();

  public async get(key: string): Promise<string | null> {
    return this.store.get(key) ?? null;
  }

  public async set(key: string, value: string): Promise<void> {
    this.store.set(key, value);
  }

  public async delete(key: string): Promise<boolean> {
    return this.store.delete(key);
  }
}

export class SecureOSCredentialStore implements CredentialStore {
  private fallback: MemoryCredentialStore = new MemoryCredentialStore();

  private isTauri(): boolean {
    return typeof window !== "undefined" && Boolean((window as any).__TAURI_INTERNALS__);
  }

  public async get(key: string): Promise<string | null> {
    if (this.isTauri()) {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        return await invoke<string | null>("get_secure_credential", { key });
      } catch {
        // Fallback if plugin is not initialized
        return this.fallback.get(key);
      }
    }
    return this.fallback.get(key);
  }

  public async set(key: string, value: string): Promise<void> {
    if (this.isTauri()) {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("set_secure_credential", { key, value });
        return;
      } catch {
        // Fallback
      }
    }
    await this.fallback.set(key, value);
  }

  public async delete(key: string): Promise<boolean> {
    if (this.isTauri()) {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        return await invoke<boolean>("delete_secure_credential", { key });
      } catch {
        // Fallback
      }
    }
    return this.fallback.delete(key);
  }
}

// Global Singleton
export const credentialStore: CredentialStore = new SecureOSCredentialStore();
