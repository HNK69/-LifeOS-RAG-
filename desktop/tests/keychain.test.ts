import { describe, it, expect } from "vitest";
import { MemoryCredentialStore, SecureOSCredentialStore } from "../src/security/keychain";

describe("Credential Stores", () => {
  it("MemoryCredentialStore sets, gets, and deletes credentials", async () => {
    const store = new MemoryCredentialStore();
    expect(await store.get("test_key")).toBeNull();

    await store.set("test_key", "secret_value_123");
    expect(await store.get("test_key")).toBe("secret_value_123");

    const deleted = await store.delete("test_key");
    expect(deleted).toBe(true);
    expect(await store.get("test_key")).toBeNull();
  });

  it("SecureOSCredentialStore gracefully falls back to memory store in non-Tauri environment", async () => {
    const store = new SecureOSCredentialStore();
    expect(await store.get("session_token")).toBeNull();

    await store.set("session_token", "ephemeral-token-abc");
    expect(await store.get("session_token")).toBe("ephemeral-token-abc");

    await store.delete("session_token");
    expect(await store.get("session_token")).toBeNull();
  });
});
