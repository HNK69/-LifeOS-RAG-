import React, { useCallback, useEffect, useMemo, useState } from "react";
import { LifeOSWebClient } from "../api/client";
import {
  LifeOSEventPayload,
  PairingInitiateResponse,
  PeerDevice,
  QrPairingPayload,
} from "../api/types";

// ============================================================================
// Pure-TypeScript ISO/IEC 18004 QR Code Encoder (Versions 1..20, EC Level L)
// Zero external dependencies; deterministic SVG output.
// ============================================================================

interface QrVersionSpec {
  version: number;
  dataCodewords: number;
  ecPerBlock: number;
  g1Blocks: number;
  g1DataCw: number;
  g2Blocks: number;
  g2DataCw: number;
  alignment: number[];
}

const QR_VERSIONS_L: QrVersionSpec[] = [
  { version: 1, dataCodewords: 19, ecPerBlock: 7, g1Blocks: 1, g1DataCw: 19, g2Blocks: 0, g2DataCw: 0, alignment: [] },
  { version: 2, dataCodewords: 34, ecPerBlock: 10, g1Blocks: 1, g1DataCw: 34, g2Blocks: 0, g2DataCw: 0, alignment: [6, 18] },
  { version: 3, dataCodewords: 55, ecPerBlock: 15, g1Blocks: 1, g1DataCw: 55, g2Blocks: 0, g2DataCw: 0, alignment: [6, 22] },
  { version: 4, dataCodewords: 80, ecPerBlock: 20, g1Blocks: 1, g1DataCw: 80, g2Blocks: 0, g2DataCw: 0, alignment: [6, 26] },
  { version: 5, dataCodewords: 108, ecPerBlock: 26, g1Blocks: 1, g1DataCw: 108, g2Blocks: 0, g2DataCw: 0, alignment: [6, 30] },
  { version: 6, dataCodewords: 136, ecPerBlock: 18, g1Blocks: 2, g1DataCw: 68, g2Blocks: 0, g2DataCw: 0, alignment: [6, 34] },
  { version: 7, dataCodewords: 156, ecPerBlock: 20, g1Blocks: 2, g1DataCw: 78, g2Blocks: 0, g2DataCw: 0, alignment: [6, 22, 38] },
  { version: 8, dataCodewords: 194, ecPerBlock: 24, g1Blocks: 2, g1DataCw: 97, g2Blocks: 0, g2DataCw: 0, alignment: [6, 24, 42] },
  { version: 9, dataCodewords: 232, ecPerBlock: 30, g1Blocks: 2, g1DataCw: 116, g2Blocks: 0, g2DataCw: 0, alignment: [6, 26, 46] },
  { version: 10, dataCodewords: 274, ecPerBlock: 18, g1Blocks: 2, g1DataCw: 68, g2Blocks: 2, g2DataCw: 69, alignment: [6, 28, 50] },
  { version: 11, dataCodewords: 324, ecPerBlock: 20, g1Blocks: 4, g1DataCw: 81, g2Blocks: 0, g2DataCw: 0, alignment: [6, 30, 54] },
  { version: 12, dataCodewords: 370, ecPerBlock: 24, g1Blocks: 2, g1DataCw: 92, g2Blocks: 2, g2DataCw: 93, alignment: [6, 32, 58] },
  { version: 13, dataCodewords: 428, ecPerBlock: 26, g1Blocks: 4, g1DataCw: 107, g2Blocks: 0, g2DataCw: 0, alignment: [6, 34, 62] },
  { version: 14, dataCodewords: 461, ecPerBlock: 30, g1Blocks: 3, g1DataCw: 115, g2Blocks: 1, g2DataCw: 116, alignment: [6, 26, 46, 66] },
  { version: 15, dataCodewords: 523, ecPerBlock: 22, g1Blocks: 5, g1DataCw: 87, g2Blocks: 1, g2DataCw: 88, alignment: [6, 26, 48, 70] },
  { version: 16, dataCodewords: 589, ecPerBlock: 24, g1Blocks: 5, g1DataCw: 98, g2Blocks: 1, g2DataCw: 99, alignment: [6, 26, 50, 74] },
  { version: 17, dataCodewords: 647, ecPerBlock: 28, g1Blocks: 1, g1DataCw: 107, g2Blocks: 5, g2DataCw: 108, alignment: [6, 30, 54, 78] },
  { version: 18, dataCodewords: 721, ecPerBlock: 30, g1Blocks: 5, g1DataCw: 120, g2Blocks: 1, g2DataCw: 121, alignment: [6, 30, 56, 82] },
  { version: 19, dataCodewords: 795, ecPerBlock: 28, g1Blocks: 3, g1DataCw: 113, g2Blocks: 4, g2DataCw: 114, alignment: [6, 30, 58, 86] },
  { version: 20, dataCodewords: 861, ecPerBlock: 28, g1Blocks: 3, g1DataCw: 107, g2Blocks: 5, g2DataCw: 108, alignment: [6, 34, 62, 90] },
];

const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) {
      x ^= 0x11d;
    }
  }
  for (let i = 255; i < 512; i++) {
    GF_EXP[i] = GF_EXP[i - 255];
  }
})();

function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

function rsGeneratorPoly(ecCount: number): Uint8Array {
  let poly = new Uint8Array([1]);
  for (let i = 0; i < ecCount; i++) {
    const next = new Uint8Array(poly.length + 1);
    const factor = GF_EXP[i];
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= poly[j];
      next[j + 1] ^= gfMul(poly[j], factor);
    }
    poly = next;
  }
  return poly;
}

function rsEncode(data: Uint8Array, ecCount: number): Uint8Array {
  const gen = rsGeneratorPoly(ecCount);
  const res = new Uint8Array(data.length + ecCount);
  res.set(data, 0);
  for (let i = 0; i < data.length; i++) {
    const coef = res[i];
    if (coef !== 0) {
      for (let j = 0; j < gen.length; j++) {
        res[i + j] ^= gfMul(gen[j], coef);
      }
    }
  }
  return res.slice(data.length);
}

function selectVersion(byteLen: number): QrVersionSpec {
  for (const spec of QR_VERSIONS_L) {
    const headerBits = 4 + (spec.version >= 10 ? 16 : 8);
    const totalBitsNeeded = headerBits + byteLen * 8;
    if (totalBitsNeeded <= spec.dataCodewords * 8) {
      return spec;
    }
  }
  throw new Error(`QR payload (${byteLen} bytes) exceeds Version 20 capacity.`);
}

function encodeDataCodewords(bytes: Uint8Array, spec: QrVersionSpec): Uint8Array {
  const bits: number[] = [];
  const pushBits = (val: number, count: number) => {
    for (let i = count - 1; i >= 0; i--) {
      bits.push((val >> i) & 1);
    }
  };

  // Byte mode indicator: 0100
  pushBits(0b0100, 4);
  pushBits(bytes.length, spec.version >= 10 ? 16 : 8);
  for (let i = 0; i < bytes.length; i++) {
    pushBits(bytes[i], 8);
  }

  const maxBits = spec.dataCodewords * 8;
  const terminatorLen = Math.min(4, maxBits - bits.length);
  pushBits(0, terminatorLen);

  while (bits.length % 8 !== 0) {
    bits.push(0);
  }

  const codewords = new Uint8Array(spec.dataCodewords);
  let byteIdx = 0;
  for (let i = 0; i < bits.length; i += 8) {
    let b = 0;
    for (let j = 0; j < 8; j++) {
      b = (b << 1) | bits[i + j];
    }
    codewords[byteIdx++] = b;
  }

  const pads = [0xec, 0x11];
  let padIdx = 0;
  while (byteIdx < spec.dataCodewords) {
    codewords[byteIdx++] = pads[padIdx % 2];
    padIdx++;
  }
  return codewords;
}

function interleaveBlocks(dataCw: Uint8Array, spec: QrVersionSpec): Uint8Array {
  const dataBlocks: Uint8Array[] = [];
  const ecBlocks: Uint8Array[] = [];
  let offset = 0;

  for (let i = 0; i < spec.g1Blocks; i++) {
    const blk = dataCw.slice(offset, offset + spec.g1DataCw);
    offset += spec.g1DataCw;
    dataBlocks.push(blk);
    ecBlocks.push(rsEncode(blk, spec.ecPerBlock));
  }
  for (let i = 0; i < spec.g2Blocks; i++) {
    const blk = dataCw.slice(offset, offset + spec.g2DataCw);
    offset += spec.g2DataCw;
    dataBlocks.push(blk);
    ecBlocks.push(rsEncode(blk, spec.ecPerBlock));
  }

  const totalOut = spec.dataCodewords + (spec.g1Blocks + spec.g2Blocks) * spec.ecPerBlock;
  const out = new Uint8Array(totalOut);
  let outIdx = 0;

  const maxDataLen = Math.max(spec.g1DataCw, spec.g2DataCw);
  for (let c = 0; c < maxDataLen; c++) {
    for (const blk of dataBlocks) {
      if (c < blk.length) {
        out[outIdx++] = blk[c];
      }
    }
  }
  for (let c = 0; c < spec.ecPerBlock; c++) {
    for (const ec of ecBlocks) {
      out[outIdx++] = ec[c];
    }
  }
  return out;
}

function computeFormatInfoBits(mask: number): number {
  const ecL = 0b01;
  const data = (ecL << 3) | mask;
  let d = data << 10;
  const gen = 0x537;
  for (let i = 14; i >= 10; i--) {
    if ((d >> i) & 1) {
      d ^= gen << (i - 10);
    }
  }
  return ((data << 10) | d) ^ 0x5412;
}

function computeVersionInfoBits(version: number): number {
  let d = version << 12;
  const gen = 0x1f25;
  for (let i = 17; i >= 12; i--) {
    if ((d >> i) & 1) {
      d ^= gen << (i - 12);
    }
  }
  return (version << 12) | d;
}

function evaluateMaskPenalty(matrix: boolean[][]): number {
  const n = matrix.length;
  let penalty = 0;

  // Rule 1: 5+ consecutive same-color modules in row or col
  for (let r = 0; r < n; r++) {
    let runLen = 1;
    for (let c = 1; c < n; c++) {
      if (matrix[r][c] === matrix[r][c - 1]) {
        runLen++;
      } else {
        if (runLen >= 5) penalty += 3 + (runLen - 5);
        runLen = 1;
      }
    }
    if (runLen >= 5) penalty += 3 + (runLen - 5);
  }
  for (let c = 0; c < n; c++) {
    let runLen = 1;
    for (let r = 1; r < n; r++) {
      if (matrix[r][c] === matrix[r - 1][c]) {
        runLen++;
      } else {
        if (runLen >= 5) penalty += 3 + (runLen - 5);
        runLen = 1;
      }
    }
    if (runLen >= 5) penalty += 3 + (runLen - 5);
  }

  // Rule 2: 2x2 blocks of same color
  for (let r = 0; r < n - 1; r++) {
    for (let c = 0; c < n - 1; c++) {
      const v = matrix[r][c];
      if (matrix[r][c + 1] === v && matrix[r + 1][c] === v && matrix[r + 1][c + 1] === v) {
        penalty += 3;
      }
    }
  }

  // Rule 4: Dark module ratio deviation from 50%
  let darkCount = 0;
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      if (matrix[r][c]) darkCount++;
    }
  }
  const pct = (darkCount * 100) / (n * n);
  const k = Math.floor(Math.abs(pct - 50) / 5);
  penalty += k * 10;

  return penalty;
}

function maskCondition(mask: number, r: number, c: number): boolean {
  switch (mask) {
    case 0:
      return (r + c) % 2 === 0;
    case 1:
      return r % 2 === 0;
    case 2:
      return c % 3 === 0;
    case 3:
      return (r + c) % 3 === 0;
    case 4:
      return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
    case 5:
      return ((r * c) % 2) + ((r * c) % 3) === 0;
    case 6:
      return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
    case 7:
      return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
    default:
      return false;
  }
}

export function encodeQrMatrix(text: string): boolean[][] {
  const bytes = new TextEncoder().encode(text);
  const spec = selectVersion(bytes.length);
  const size = 17 + 4 * spec.version;
  const dataCw = encodeDataCodewords(bytes, spec);
  const fullCw = interleaveBlocks(dataCw, spec);

  const isFunc: boolean[][] = Array.from({ length: size }, () => Array(size).fill(false));
  const base: boolean[][] = Array.from({ length: size }, () => Array(size).fill(false));

  const setModule = (r: number, c: number, dark: boolean, func = true) => {
    base[r][c] = dark;
    if (func) isFunc[r][c] = true;
  };

  // Finder patterns + separators
  const drawFinder = (top: number, left: number) => {
    for (let dr = -1; dr <= 7; dr++) {
      for (let dc = -1; dc <= 7; dc++) {
        const r = top + dr;
        const c = left + dc;
        if (r < 0 || r >= size || c < 0 || c >= size) continue;
        const isBorder = dr === -1 || dr === 7 || dc === -1 || dc === 7;
        const isOuter = dr === 0 || dr === 6 || dc === 0 || dc === 6;
        const isInner = dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4;
        setModule(r, c, !isBorder && (isOuter || isInner), true);
      }
    }
  };
  drawFinder(0, 0);
  drawFinder(0, size - 7);
  drawFinder(size - 7, 0);

  // Alignment patterns
  const drawAlignment = (centerR: number, centerC: number) => {
    for (let dr = -2; dr <= 2; dr++) {
      for (let dc = -2; dc <= 2; dc++) {
        const dark = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
        setModule(centerR + dr, centerC + dc, dark, true);
      }
    }
  };
  for (const r of spec.alignment) {
    for (const c of spec.alignment) {
      if (isFunc[r][c]) continue;
      drawAlignment(r, c);
    }
  }

  // Timing patterns
  for (let i = 8; i < size - 8; i++) {
    const dark = i % 2 === 0;
    if (!isFunc[6][i]) setModule(6, i, dark, true);
    if (!isFunc[i][6]) setModule(i, 6, dark, true);
  }

  // Dark module
  setModule(size - 8, 8, true, true);

  // Reserve format info cells
  for (let i = 0; i <= 8; i++) {
    if (!isFunc[8][i]) isFunc[8][i] = true;
    if (!isFunc[i][8]) isFunc[i][8] = true;
  }
  for (let i = 0; i < 8; i++) {
    isFunc[8][size - 1 - i] = true;
    isFunc[size - 1 - i][8] = true;
  }

  // Version info (v >= 7)
  if (spec.version >= 7) {
    const verBits = computeVersionInfoBits(spec.version);
    for (let i = 0; i < 18; i++) {
      const bit = ((verBits >> i) & 1) === 1;
      const a = Math.floor(i / 3);
      const b = size - 11 + (i % 3);
      setModule(a, b, bit, true);
      setModule(b, a, bit, true);
    }
  }

  // Place data bits in zig-zag order
  let bitIdx = 0;
  const totalBits = fullCw.length * 8;
  let upward = true;
  for (let col = size - 1; col >= 1; col -= 2) {
    if (col === 6) col--;
    for (let step = 0; step < size; step++) {
      const r = upward ? size - 1 - step : step;
      for (let dx = 0; dx < 2; dx++) {
        const c = col - dx;
        if (!isFunc[r][c]) {
          let bit = false;
          if (bitIdx < totalBits) {
            bit = ((fullCw[bitIdx >> 3] >> (7 - (bitIdx & 7))) & 1) === 1;
            bitIdx++;
          }
          base[r][c] = bit;
        }
      }
    }
    upward = !upward;
  }

  // Evaluate all 8 masks deterministically and pick lowest penalty
  let bestMask = 0;
  let bestPenalty = Infinity;
  let bestMatrix = base;

  for (let m = 0; m < 8; m++) {
    const candidate = base.map((row, r) =>
      row.map((val, c) => (isFunc[r][c] ? val : maskCondition(m, r, c) ? !val : val))
    );
    const fmt = computeFormatInfoBits(m);
    for (let i = 0; i <= 5; i++) {
      candidate[8][i] = ((fmt >> (14 - i)) & 1) === 1;
      candidate[i][8] = ((fmt >> i) & 1) === 1;
    }
    candidate[8][7] = ((fmt >> 8) & 1) === 1;
    candidate[8][8] = ((fmt >> 7) & 1) === 1;
    candidate[7][8] = ((fmt >> 6) & 1) === 1;
    for (let i = 0; i < 7; i++) {
      candidate[size - 1 - i][8] = ((fmt >> (14 - i)) & 1) === 1;
    }
    for (let i = 0; i < 8; i++) {
      candidate[8][size - 8 + i] = ((fmt >> (7 - i)) & 1) === 1;
    }

    const penalty = evaluateMaskPenalty(candidate);
    if (penalty < bestPenalty) {
      bestPenalty = penalty;
      bestMask = m;
      bestMatrix = candidate;
    }
  }

  void bestMask;
  return bestMatrix;
}

export interface QrCodeSvgProps {
  value: string;
  sizePx?: number;
  label?: string;
}

export const QrCodeSvg: React.FC<QrCodeSvgProps> = ({
  value,
  sizePx = 220,
  label = "LifeOS Mobile Pairing QR Code",
}) => {
  const qrResult = useMemo(() => {
    try {
      const mat = encodeQrMatrix(value);
      const n = mat.length;
      const quiet = 4;
      const cmds: string[] = [];
      for (let r = 0; r < n; r++) {
        for (let c = 0; c < n; c++) {
          if (mat[r][c]) {
            cmds.push(`M${c + quiet},${r + quiet}h1v1h-1z`);
          }
        }
      }
      return { ok: true as const, size: n + quiet * 2, pathD: cmds.join("") };
    } catch (err: any) {
      return {
        ok: false as const,
        error: err?.message || "QR payload exceeds supported Version 20 capacity.",
      };
    }
  }, [value]);

  if (!qrResult.ok) {
    return (
      <div
        role="alert"
        data-testid="qr-encode-error"
        style={{
          width: `${sizePx}px`,
          padding: "16px",
          backgroundColor: "#fef2f2",
          border: "1px solid #fecaca",
          borderRadius: "8px",
          color: "#991b1b",
          fontSize: "12px",
          textAlign: "center",
          boxSizing: "border-box",
        }}
      >
        Unable to render QR code: {qrResult.error}
      </div>
    );
  }

  return (
    <svg
      data-testid="qr-code-svg"
      role="img"
      aria-label={label}
      width={sizePx}
      height={sizePx}
      viewBox={`0 0 ${qrResult.size} ${qrResult.size}`}
      shapeRendering="crispEdges"
      style={{
        backgroundColor: "#ffffff",
        border: "1px solid #cbd5e1",
        borderRadius: "8px",
        display: "block",
      }}
    >
      <rect width={qrResult.size} height={qrResult.size} fill="#ffffff" />
      <path d={qrResult.pathD} fill="#0f172a" />
    </svg>
  );
};

// ============================================================================
// QR Pairing Modal Component
// ============================================================================

export function buildQrPairingPayload(challenge: PairingInitiateResponse): QrPairingPayload {
  return {
    version: 1,
    challenge_id: challenge.challenge_id,
    challenge_code: challenge.challenge_code,
    expires_at: challenge.expires_at,
    host: challenge.host || "127.0.0.1",
    port: challenge.port || 8000,
    desktop_device_id: challenge.desktop_device_id,
    signing_public_key: challenge.signing_public_key,
    exchange_public_key: challenge.exchange_public_key,
    pairing_token: challenge.pairing_token ?? null,
  };
}

export function computeRemainingSeconds(expiresAt: string, nowMs: number = Date.now()): number {
  const expMs = Date.parse(expiresAt);
  if (Number.isNaN(expMs)) return 0;
  return Math.max(0, Math.floor((expMs - nowMs) / 1000));
}

export function computeInitialTtlSeconds(
  challenge: PairingInitiateResponse | null,
  fallbackTtl: number = 300
): number {
  if (!challenge) return fallbackTtl > 0 ? fallbackTtl : 300;
  const expMs = Date.parse(challenge.expires_at);
  const createdMs = challenge.created_at ? Date.parse(challenge.created_at) : NaN;
  if (!Number.isNaN(expMs) && !Number.isNaN(createdMs) && expMs > createdMs) {
    return Math.max(1, Math.round((expMs - createdMs) / 1000));
  }
  return fallbackTtl > 0 ? fallbackTtl : 300;
}

export function detectPairedPeerFromPoll(
  peers: PeerDevice[],
  baselinePeerTimestamps: Map<string, string>,
  challengeCreatedAt?: string
): PeerDevice | undefined {
  const challengeCreatedMs = challengeCreatedAt ? Date.parse(challengeCreatedAt) : NaN;
  return peers.find((p) => {
    if (p.trust_status !== "paired") return false;
    if (!baselinePeerTimestamps.has(p.device_id)) return true;
    const prevStamp = baselinePeerTimestamps.get(p.device_id);
    const currStamp = `${p.paired_at || ""}|${p.last_seen_at || ""}`;
    if (currStamp !== prevStamp) return true;
    if (!Number.isNaN(challengeCreatedMs) && p.paired_at) {
      const pairedMs = Date.parse(p.paired_at);
      if (!Number.isNaN(pairedMs) && pairedMs >= challengeCreatedMs) {
        return true;
      }
    }
    return false;
  });
}

export interface QrPairingModalProps {
  client: LifeOSWebClient;
  isOpen: boolean;
  onClose: () => void;
  onPaired?: (peer?: PeerDevice) => void;
  events?: LifeOSEventPayload[];
  initialChallenge?: PairingInitiateResponse | null;
  initialRemainingSeconds?: number;
}

export const QrPairingModal: React.FC<QrPairingModalProps> = ({
  client,
  isOpen,
  onClose,
  onPaired,
  events = [],
  initialChallenge = null,
  initialRemainingSeconds,
}) => {
  const [challenge, setChallenge] = useState<PairingInitiateResponse | null>(initialChallenge);
  const [remainingSeconds, setRemainingSeconds] = useState<number>(
    initialRemainingSeconds ??
      (initialChallenge ? computeRemainingSeconds(initialChallenge.expires_at) : 300)
  );
  const [isLoading, setIsLoading] = useState(false);
  const [pairedPeer, setPairedPeer] = useState<PeerDevice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [baselinePeerTimestamps, setBaselinePeerTimestamps] = useState<Map<string, string>>(new Map());

  const startPairingChallenge = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setPairedPeer(null);
    try {
      try {
        const currentPeers = await client.listPeers();
        const baselineMap = new Map<string, string>();
        for (const p of currentPeers) {
          if (p.trust_status === "paired") {
            baselineMap.set(p.device_id, `${p.paired_at || ""}|${p.last_seen_at || ""}`);
          }
        }
        setBaselinePeerTimestamps(baselineMap);
      } catch {
        setBaselinePeerTimestamps(new Map());
      }
      const res = await client.initiatePairing({ peer_device_id: "*", ttl_seconds: 300 });
      setChallenge(res);
      setRemainingSeconds(computeRemainingSeconds(res.expires_at) || computeInitialTtlSeconds(res));
    } catch (err: any) {
      setError(err?.message || "Failed to generate QR pairing challenge");
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (!isOpen) return;
    if (!initialChallenge) {
      startPairingChallenge();
    }
  }, [isOpen, initialChallenge, startPairingChallenge]);

  // Live 1-second TTL countdown timer
  useEffect(() => {
    if (!isOpen || !challenge || pairedPeer) return;
    const timer = setInterval(() => {
      const rem = computeRemainingSeconds(challenge.expires_at);
      setRemainingSeconds(rem);
    }, 1000);
    return () => clearInterval(timer);
  }, [isOpen, challenge, pairedPeer]);

  // SSE pairing completion listener
  useEffect(() => {
    if (!isOpen || !challenge || pairedPeer) return;
    const completedEvt = events.find(
      (e) =>
        e.event_type === "device.pairing_completed" &&
        e.payload?.challenge_id === challenge.challenge_id
    );
    if (completedEvt) {
      const peer: PeerDevice = {
        device_id: completedEvt.payload.peer_device_id,
        device_name: completedEvt.payload.device_name || "Mobile Companion",
        device_type: completedEvt.payload.device_type || "mobile",
        trust_status: "paired",
        last_seen_at: completedEvt.timestamp,
      };
      setPairedPeer(peer);
      onPaired?.(peer);
    }
  }, [isOpen, challenge, events, pairedPeer, onPaired]);

  // Polling fallback every 2.5s to detect peer verification (new pairings and re-pairings)
  useEffect(() => {
    if (!isOpen || !challenge || pairedPeer || remainingSeconds <= 0) return;
    const poller = setInterval(async () => {
      try {
        const peers = await client.listPeers();
        const newlyPaired = detectPairedPeerFromPoll(
          peers,
          baselinePeerTimestamps,
          challenge.created_at
        );
        if (newlyPaired) {
          setPairedPeer(newlyPaired);
          onPaired?.(newlyPaired);
        }
      } catch {
        // Ignore transient poll errors
      }
    }, 2500);
    return () => clearInterval(poller);
  }, [isOpen, challenge, pairedPeer, remainingSeconds, client, baselinePeerTimestamps, onPaired]);

  if (!isOpen) {
    return null;
  }

  const qrPayloadString = challenge ? JSON.stringify(buildQrPairingPayload(challenge)) : "";
  const isExpired = Boolean(challenge) && remainingSeconds <= 0 && !pairedPeer;
  const initialTtl = computeInitialTtlSeconds(challenge, initialRemainingSeconds ?? 300);
  const pct = Math.max(0, Math.min(100, Math.round((remainingSeconds / initialTtl) * 100)));

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Pair Mobile Device via QR Code"
      data-testid="qr-pairing-modal"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(15, 23, 42, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: "16px",
      }}
    >
      <div
        style={{
          backgroundColor: "#ffffff",
          borderRadius: "12px",
          border: "1px solid #cbd5e1",
          width: "100%",
          maxWidth: "480px",
          padding: "20px",
          boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.15)",
          display: "flex",
          flexDirection: "column",
          gap: "14px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#0f172a" }}>
            Pair Mobile Companion (QR)
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close Pairing Modal"
            style={{
              backgroundColor: "transparent",
              border: "none",
              fontSize: "18px",
              color: "#64748b",
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {error && (
          <div
            role="alert"
            style={{
              backgroundColor: "#fef2f2",
              color: "#991b1b",
              border: "1px solid #fecaca",
              borderRadius: "6px",
              padding: "10px",
              fontSize: "12px",
            }}
          >
            {error}
          </div>
        )}

        {pairedPeer ? (
          <div
            data-testid="pairing-success-banner"
            style={{
              backgroundColor: "#f0fdf4",
              border: "1px solid #86efac",
              borderRadius: "8px",
              padding: "16px",
              color: "#166534",
              display: "flex",
              flexDirection: "column",
              gap: "6px",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: "14px" }}>
              Mobile Device Paired Successfully
            </div>
            <div style={{ fontSize: "12px" }}>
              Device: <strong>{pairedPeer.device_name}</strong> ({pairedPeer.device_type})
            </div>
            <div style={{ fontSize: "11px", fontFamily: "ui-monospace, monospace" }}>
              ID: {pairedPeer.device_id}
            </div>
          </div>
        ) : isLoading ? (
          <div style={{ padding: "32px", textAlign: "center", fontSize: "13px", color: "#64748b" }}>
            Generating ephemeral cryptographic pairing challenge...
          </div>
        ) : challenge ? (
          <>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}>
              <QrCodeSvg value={qrPayloadString} sizePx={220} />

              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: "11px", color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  6-Digit Verification Code
                </div>
                <div
                  data-testid="pairing-challenge-code"
                  style={{
                    fontSize: "28px",
                    fontWeight: 800,
                    letterSpacing: "0.2em",
                    fontFamily: "ui-monospace, monospace",
                    color: isExpired ? "#94a3b8" : "#0f172a",
                  }}
                >
                  {challenge.challenge_code}
                </div>
              </div>
            </div>

            {/* TTL Countdown */}
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <div
                data-testid="pairing-ttl-countdown"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  fontSize: "12px",
                  fontWeight: 600,
                  color: isExpired ? "#b91c1c" : remainingSeconds <= 60 ? "#b45309" : "#334155",
                }}
              >
                <span>{isExpired ? "Challenge Expired" : "Challenge Expires In"}</span>
                <span>{remainingSeconds}s</span>
              </div>
              <div
                style={{
                  height: "6px",
                  width: "100%",
                  backgroundColor: "#e2e8f0",
                  borderRadius: "9999px",
                  overflow: "hidden",
                }}
              >
                <div
                  data-testid="pairing-ttl-bar"
                  style={{
                    height: "100%",
                    width: `${pct}%`,
                    backgroundColor: isExpired ? "#ef4444" : remainingSeconds <= 60 ? "#f59e0b" : "#2563eb",
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
            </div>

            <div
              style={{
                backgroundColor: "#f8fafc",
                border: "1px solid #e2e8f0",
                borderRadius: "6px",
                padding: "10px",
                fontSize: "11px",
                color: "#475569",
                fontFamily: "ui-monospace, monospace",
              }}
            >
              <div>Host: {challenge.host}:{challenge.port}</div>
              <div>Desktop ID: {challenge.desktop_device_id}</div>
              <div>Challenge: {challenge.challenge_id}</div>
            </div>
          </>
        ) : null}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "4px" }}>
          {!pairedPeer && (
            <button
              type="button"
              onClick={startPairingChallenge}
              disabled={isLoading}
              style={{
                backgroundColor: "#f1f5f9",
                color: "#334155",
                border: "1px solid #cbd5e1",
                borderRadius: "6px",
                padding: "7px 12px",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Regenerate QR
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            style={{
              backgroundColor: pairedPeer ? "#16a34a" : "#0f172a",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              padding: "7px 14px",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {pairedPeer ? "Done" : "Close"}
          </button>
        </div>
      </div>
    </div>
  );
};
