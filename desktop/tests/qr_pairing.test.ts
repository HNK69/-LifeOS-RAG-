import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LifeOSWebClient } from "../src/api/client";
import {
  LifeOSEventPayload,
  PairingInitiateResponse,
  PeerDevice,
  PeerSyncStatus,
} from "../src/api/types";
import {
  QrCodeSvg,
  QrPairingModal,
  buildQrPairingPayload,
  computeInitialTtlSeconds,
  computeRemainingSeconds,
  detectPairedPeerFromPoll,
  encodeQrMatrix,
} from "../src/components/QrPairingModal";
import { SyncView } from "../src/views/SyncView";
import { App } from "../src/App";

describe("QR Pairing & Peer Revocation (Milestone C5)", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const sampleChallenge: PairingInitiateResponse = {
    challenge_id: "chal_0123456789abcdef01234567",
    peer_device_id: "*",
    challenge_code: "482913",
    expires_at: "2026-11-26T15:05:00Z",
    created_at: "2026-11-26T15:00:00Z",
    host: "192.168.1.42",
    port: 8000,
    desktop_device_id: "desktop-uuid-1234-5678",
    signing_public_key: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    exchange_public_key: "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    pairing_token: "pair_ephemeral_token_xyz",
  };

  it("encodes valid ISO 18004 QR matrices with finder patterns across small and ~400-byte pairing payloads", () => {
    const smallMat = encodeQrMatrix("LIFEOS");
    expect(smallMat.length).toBe(21); // Version 1 (21x21)
    // Top-left finder pattern 7x7 outer ring & 3x3 core
    for (let i = 0; i < 7; i++) {
      expect(smallMat[0][i]).toBe(true);
      expect(smallMat[6][i]).toBe(true);
      expect(smallMat[i][0]).toBe(true);
      expect(smallMat[i][6]).toBe(true);
    }
    expect(smallMat[3][3]).toBe(true);
    // White separator
    expect(smallMat[7][0]).toBe(false);
    expect(smallMat[0][7]).toBe(false);

    // ~400 byte QR pairing payload (exercises Version >= 12 with multi-block RS + alignment + version info)
    const payloadJson = JSON.stringify(buildQrPairingPayload(sampleChallenge));
    const matA = encodeQrMatrix(payloadJson);
    const matB = encodeQrMatrix(payloadJson);
    expect(matA.length).toBeGreaterThanOrEqual(57);
    expect(matA).toEqual(matB); // Deterministic

    // Oversize payload (> Version 20) throws
    expect(() => encodeQrMatrix("X".repeat(1200))).toThrow(/exceeds Version 20/i);
  });

  it("renders deterministic QrCodeSvg markup with quiet zone", () => {
    const payloadJson = JSON.stringify(buildQrPairingPayload(sampleChallenge));
    const svg1 = renderToStaticMarkup(React.createElement(QrCodeSvg, { value: payloadJson, sizePx: 200 }));
    const svg2 = renderToStaticMarkup(React.createElement(QrCodeSvg, { value: payloadJson, sizePx: 200 }));

    expect(svg1).toBe(svg2);
    expect(svg1).toContain('data-testid="qr-code-svg"');
    expect(svg1).toContain("<path");
    expect(svg1).toContain('width="200"');
  });

  it("renders QrPairingModal with 6-digit code, TTL countdown, and expired state", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });

    const activeHtml = renderToStaticMarkup(
      React.createElement(QrPairingModal, {
        client,
        isOpen: true,
        onClose: () => {},
        initialChallenge: sampleChallenge,
        initialRemainingSeconds: 240,
      })
    );
    expect(activeHtml).toContain("Pair Mobile Companion (QR)");
    expect(activeHtml).toContain("482913");
    expect(activeHtml).toContain("Challenge Expires In");
    expect(activeHtml).toContain("240s");
    expect(activeHtml).toContain("192.168.1.42:8000");

    const expiredHtml = renderToStaticMarkup(
      React.createElement(QrPairingModal, {
        client,
        isOpen: true,
        onClose: () => {},
        initialChallenge: sampleChallenge,
        initialRemainingSeconds: 0,
      })
    );
    expect(expiredHtml).toContain("Challenge Expired");
    expect(expiredHtml).toContain("0s");

    // computeRemainingSeconds clamps expired timestamps to 0
    const nowMs = Date.parse("2026-11-26T15:06:00Z");
    expect(computeRemainingSeconds(sampleChallenge.expires_at, nowMs)).toBe(0);
    expect(computeRemainingSeconds(sampleChallenge.expires_at, Date.parse("2026-11-26T15:04:00Z"))).toBe(60);
  });

  it("renders Pair Mobile Device (QR) and Revoke Trust buttons in SyncView", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const peers: PeerSyncStatus[] = [
      {
        peer_device_id: "mob-1",
        device_name: "Pixel 9",
        device_type: "mobile",
        trust_status: "paired",
        last_synced_at: null,
        unresolved_conflicts: 0,
      },
      {
        peer_device_id: "mob-revoked",
        device_name: "Old iPhone",
        device_type: "mobile",
        trust_status: "revoked",
        last_synced_at: null,
        unresolved_conflicts: 0,
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(SyncView, {
        client,
        activeWorkspaceId: "default",
        initialPeers: peers,
      })
    );

    expect(html).toContain("Pair Mobile Device (QR)");
    expect(html).toContain('data-testid="revoke-peer-mob-1"');
    expect(html).toContain("Revoke Trust");
    expect(html).not.toContain('data-testid="revoke-peer-mob-revoked"');
  });

  it("calls initiatePairing, verifyPairing, and revokePeer SDK endpoints cleanly", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => sampleChallenge })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ success: true, message: "Paired", device_id: "mob-1" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ revoked: true, device_id: "mob-1", status: "revoked" }),
      });

    globalThis.fetch = mockFetch;
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000", authToken: "tok" });

    const initRes = await client.initiatePairing();
    expect(initRes.challenge_id).toBe(sampleChallenge.challenge_id);
    expect(mockFetch.mock.calls[0][0]).toBe("http://127.0.0.1:8000/api/device/pairing/initiate");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({
      peer_device_id: "*",
      ttl_seconds: 300,
    });

    const verRes = await client.verifyPairing({
      challenge_id: sampleChallenge.challenge_id,
      challenge_code: sampleChallenge.challenge_code,
      peer_device_id: "mob-1",
      peer_device_name: "Pixel 9",
      peer_device_type: "mobile",
      peer_signing_public_key: "sig",
      peer_exchange_public_key: "ex",
      signature: "signed",
    });
    expect(verRes.success).toBe(true);
    expect(mockFetch.mock.calls[1][0]).toBe("http://127.0.0.1:8000/api/device/pairing/verify");

    const revRes = await client.revokePeer("mob-1");
    expect(revRes.revoked).toBe(true);
    expect(mockFetch.mock.calls[2][0]).toBe("http://127.0.0.1:8000/api/device/peers/mob-1");
    expect(mockFetch.mock.calls[2][1].method).toBe("DELETE");
  });

  it("handles SSE event shape for device.pairing_completed", () => {
    const evt: LifeOSEventPayload = {
      event_id: "evt_1",
      event_type: "device.pairing_completed",
      timestamp: "2026-11-26T15:01:00Z",
      producer: "device",
      payload: {
        challenge_id: sampleChallenge.challenge_id,
        peer_device_id: "mob-1",
        device_name: "Pixel 9",
        device_type: "mobile",
      },
    };
    expect(evt.payload.challenge_id).toBe(sampleChallenge.challenge_id);
  });

  it("renders fallback error UI in QrCodeSvg when payload exceeds Version 20 capacity", () => {
    const oversized = "Z".repeat(1200);
    const html = renderToStaticMarkup(React.createElement(QrCodeSvg, { value: oversized, sizePx: 200 }));
    expect(html).toContain('data-testid="qr-encode-error"');
    expect(html).toContain("Unable to render QR code");
    expect(html).not.toContain('data-testid="qr-code-svg"');
  });

  it("computes dynamic initialTtl denominator for TTL progress bar instead of hardcoded 300s", () => {
    const shortChallenge: PairingInitiateResponse = {
      ...sampleChallenge,
      created_at: "2026-11-26T15:00:00Z",
      expires_at: "2026-11-26T15:02:00Z", // 120s TTL
    };
    expect(computeInitialTtlSeconds(shortChallenge)).toBe(120);

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const html = renderToStaticMarkup(
      React.createElement(QrPairingModal, {
        client,
        isOpen: true,
        onClose: () => {},
        initialChallenge: shortChallenge,
        initialRemainingSeconds: 60,
      })
    );
    expect(html).toContain("width:50%");
  });

  it("detects both newly paired and re-paired devices in polling fallback", () => {
    const baseline = new Map<string, string>([
      ["mob-existing", "2026-11-26T14:00:00Z|2026-11-26T14:00:00Z"],
    ]);

    // Unchanged existing peer is not flagged as newly paired
    const unchanged: PeerDevice[] = [
      {
        device_id: "mob-existing",
        device_name: "Pixel 9",
        device_type: "mobile",
        trust_status: "paired",
        paired_at: "2026-11-26T14:00:00Z",
        last_seen_at: "2026-11-26T14:00:00Z",
      },
    ];
    expect(detectPairedPeerFromPoll(unchanged, baseline, "2026-11-26T15:00:00Z")).toBeUndefined();

    // Re-paired existing peer with updated paired_at / last_seen_at IS detected
    const repaired: PeerDevice[] = [
      {
        device_id: "mob-existing",
        device_name: "Pixel 9",
        device_type: "mobile",
        trust_status: "paired",
        paired_at: "2026-11-26T15:00:30Z",
        last_seen_at: "2026-11-26T15:00:30Z",
      },
    ];
    const detected = detectPairedPeerFromPoll(repaired, baseline, "2026-11-26T15:00:00Z");
    expect(detected?.device_id).toBe("mob-existing");
  });

  it("renders SyncView inside App shell with events prop wired", () => {
    const appHtml = renderToStaticMarkup(
      React.createElement(App, { initialTab: "sync" })
    );
    expect(appHtml).toContain("Cross-Device Sync");
    expect(appHtml).toContain("Pair Mobile Device (QR)");
  });
});
