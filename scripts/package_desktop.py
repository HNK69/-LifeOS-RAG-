"""
package_desktop.py

Desktop packaging validation, Tauri updater manifest generator, and Ed25519/Minisign
artifact signing & verification utility for LifeOS Milestone C4.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAURI_DIR = REPO_ROOT / "desktop" / "src-tauri"


def _decode_minisign_pubkey(public_key_b64: str) -> tuple[bytes, ed25519.Ed25519PublicKey]:
    """Decode a Tauri/Minisign base64 public key into (key_id, Ed25519PublicKey)."""
    raw_text = public_key_b64.strip()
    if not raw_text:
        raise ValueError("Updater public key is empty")

    try:
        decoded = base64.b64decode(raw_text, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 public key: {exc}") from exc

    # Case 1: Base64-encoded Minisign .pub file text
    try:
        text = decoded.decode("utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[0].startswith("untrusted comment:"):
            key_bytes = base64.b64decode(lines[1], validate=True)
        else:
            key_bytes = decoded
    except UnicodeDecodeError:
        key_bytes = decoded

    if len(key_bytes) != 42 or key_bytes[:2] != b"Ed":
        raise ValueError("Invalid Minisign public key structure (expected 42 bytes starting with 'Ed')")

    key_id = key_bytes[2:10]
    pub_raw = key_bytes[10:42]
    pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_raw)
    return key_id, pub_key


def generate_minisign_keypair() -> tuple[str, str]:
    """
    Generate an ephemeral Ed25519 signing keypair formatted for Tauri/Minisign.
    Returns (private_key_b64, public_key_b64).
    """
    priv_key = ed25519.Ed25519PrivateKey.generate()
    priv_raw = priv_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = priv_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = hashlib.sha256(pub_raw).digest()[:8]

    priv_blob = b"Ed" + key_id + priv_raw
    private_key_b64 = base64.b64encode(priv_blob).decode("ascii")

    pub_line = base64.b64encode(b"Ed" + key_id + pub_raw).decode("ascii")
    pub_file = f"untrusted comment: minisign public key {key_id.hex().upper()}\n{pub_line}\n"
    public_key_b64 = base64.b64encode(pub_file.encode("utf-8")).decode("ascii")
    return private_key_b64, public_key_b64


def sign_payload(payload: bytes, private_key_b64: str, trusted_comment: str = "lifeos-release") -> str:
    """
    Sign binary payload bytes using an Ed25519 Minisign-compatible private key.
    Returns a base64-encoded 4-line Minisign signature block suitable for Tauri updater manifests.
    """
    try:
        priv_blob = base64.b64decode(private_key_b64.strip(), validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 private key: {exc}") from exc

    if len(priv_blob) != 42 or priv_blob[:2] != b"Ed":
        raise ValueError("Invalid private key structure (expected 42 bytes starting with 'Ed')")

    key_id = priv_blob[2:10]
    priv_raw = priv_blob[10:42]
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_raw)

    sig_bytes = priv_key.sign(payload)
    sig_line = base64.b64encode(b"Ed" + key_id + sig_bytes).decode("ascii")

    comment_text = trusted_comment.strip() or "lifeos-release"
    global_sig = priv_key.sign(sig_bytes + comment_text.encode("utf-8"))
    global_sig_b64 = base64.b64encode(global_sig).decode("ascii")

    sig_block = (
        f"untrusted comment: signature from lifeos release key\n"
        f"{sig_line}\n"
        f"trusted comment: {comment_text}\n"
        f"{global_sig_b64}\n"
    )
    return base64.b64encode(sig_block.encode("utf-8")).decode("ascii")


def verify_signature(payload: bytes, signature_str: str, public_key_b64: str) -> bool:
    """
    Verify an Ed25519/Minisign signature against payload bytes and public key.
    Returns True if valid, False on any mismatch, tampering, or format error.
    """
    try:
        expected_key_id, pub_key = _decode_minisign_pubkey(public_key_b64)
        raw_sig = signature_str.strip()
        if not raw_sig:
            return False

        try:
            sig_text = base64.b64decode(raw_sig, validate=True).decode("utf-8")
        except Exception:
            sig_text = raw_sig

        lines = [line.strip() for line in sig_text.splitlines() if line.strip()]
        if len(lines) < 4:
            return False
        if not lines[0].startswith("untrusted comment:") or not lines[2].startswith("trusted comment:"):
            return False

        sig_blob = base64.b64decode(lines[1], validate=True)
        if len(sig_blob) != 74 or sig_blob[:2] != b"Ed":
            return False

        sig_key_id = sig_blob[2:10]
        if sig_key_id != expected_key_id:
            return False

        sig_bytes = sig_blob[10:74]
        trusted_comment = lines[2][len("trusted comment:"):].strip()
        global_sig = base64.b64decode(lines[3], validate=True)

        pub_key.verify(sig_bytes, payload)
        pub_key.verify(global_sig, sig_bytes + trusted_comment.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def _validate_iso8601_timestamp(value: str) -> str:
    """Validate that a string is a full ISO-8601 timestamp with timezone."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Manifest pub_date must be a non-empty ISO-8601 timestamp")
    pub_str = value.strip()
    if "T" not in pub_str and " " not in pub_str:
        raise ValueError(f"Manifest pub_date must be a full ISO-8601 timestamp: '{value}'")
    normalized = pub_str[:-1] + "+00:00" if pub_str.endswith("Z") else pub_str
    try:
        parsed_dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Manifest pub_date is not a valid ISO-8601 timestamp: '{value}'") from exc
    if parsed_dt.tzinfo is None:
        raise ValueError(f"Manifest pub_date must include a timezone offset (e.g. 'Z' or '+00:00'): '{value}'")
    return pub_str


def generate_update_manifest(
    version: str,
    notes: str,
    platforms: dict[str, dict[str, str]],
    pub_date: str | None = None,
) -> dict[str, Any]:
    """Construct a Tauri v2 updater JSON manifest."""
    if not version or not version.strip():
        raise ValueError("Manifest version must be non-empty")
    if not platforms:
        raise ValueError("Manifest must include at least one platform entry")

    for plat, info in platforms.items():
        if not info.get("url", "").startswith("https://"):
            raise ValueError(f"Platform '{plat}' update URL must use HTTPS")
        if not info.get("signature", "").strip():
            raise ValueError(f"Platform '{plat}' must include a non-empty signature")

    validated_pub_date = (
        _validate_iso8601_timestamp(pub_date)
        if pub_date is not None
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    return {
        "version": version.strip(),
        "notes": notes,
        "pub_date": validated_pub_date,
        "platforms": platforms,
    }


def build_signed_update_manifest(
    version: str,
    notes: str,
    artifacts: dict[str, tuple[Path, str]],
    private_key_b64: str,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Sign artifact files and build a Tauri v2 updater JSON manifest."""
    platforms: dict[str, dict[str, str]] = {}
    for target, (artifact_path, download_url) in artifacts.items():
        data = Path(artifact_path).read_bytes()
        sha256_hex = hashlib.sha256(data).hexdigest()
        sig = sign_payload(
            data,
            private_key_b64,
            trusted_comment=f"version:{version} target:{target} sha256:{sha256_hex}",
        )
        platforms[target] = {
            "signature": sig,
            "url": download_url,
            "sha256": sha256_hex,
        }

    manifest = generate_update_manifest(version=version, notes=notes, platforms=platforms)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _validate_strict_loopback_csp(csp: str) -> None:
    """Parse CSP directives and enforce strict loopback-only network origins."""
    if not isinstance(csp, str) or not csp.strip():
        raise ValueError("Missing app.security.csp in tauri.conf.json")

    directives = [d.strip() for d in csp.split(";") if d.strip()]
    if not directives:
        raise ValueError("CSP must contain at least one directive")

    all_sources: list[str] = []
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    allowed_scheme_only = {"data:", "blob:", "tauri:", "asset:"}

    for directive in directives:
        tokens = directive.split()
        if len(tokens) < 2:
            continue
        sources = tokens[1:]
        for tok in sources:
            all_sources.append(tok)
            tok_lower = tok.lower()

            if tok_lower == "*" or tok_lower.startswith("*.") or tok_lower.startswith("*://"):
                raise ValueError(f"CSP must not allow wildcard or non-loopback origin: '{tok}'")

            if "0.0.0.0" in tok_lower:
                raise ValueError(f"CSP must not allow wildcard or non-loopback origin: '{tok}'")

            if tok_lower.startswith(("'") ) and tok_lower.endswith("'"):
                continue

            if tok_lower in allowed_scheme_only:
                continue

            if tok_lower in {"http:", "https:", "ws:", "wss:"}:
                raise ValueError(f"CSP must not allow wildcard scheme-only non-loopback origin: '{tok}'")

            if "://" in tok_lower:
                try:
                    parsed = urlparse(tok_lower)
                    scheme = parsed.scheme
                    hostname = parsed.hostname or ""
                except ValueError as exc:
                    raise ValueError(
                        f"CSP contains malformed non-loopback origin '{tok}': {exc}"
                    ) from exc
                if scheme in {"http", "https", "ws", "wss"}:
                    if "*" in hostname or hostname not in loopback_hosts:
                        raise ValueError(
                            f"CSP must only allow loopback HTTP/HTTPS/WS origins, rejected non-loopback/wildcard origin: '{tok}'"
                        )
                else:
                    raise ValueError(f"CSP contains disallowed origin scheme: '{tok}'")
            else:
                # Bare host or pattern without scheme (e.g. 'example.com' or '*.example.com')
                raise ValueError(f"CSP must not allow non-loopback host pattern: '{tok}'")

    if "http://127.0.0.1:*" not in all_sources or "ws://127.0.0.1:*" not in all_sources:
        raise ValueError("CSP must allow strict loopback 127.0.0.1 HTTP/WS sidecar transport")


def validate_tauri_assets(tauri_dir: Path | None = None) -> dict[str, Any]:
    """
    Validate Tauri configuration, security CSP, updater pubkey/endpoints,
    Cargo dependencies, capabilities, and icon assets.
    """
    base = Path(tauri_dir) if tauri_dir else DEFAULT_TAURI_DIR
    conf_path = base / "tauri.conf.json"
    cargo_path = base / "Cargo.toml"
    cap_path = base / "capabilities" / "default.json"

    if not conf_path.is_file():
        raise ValueError(f"Missing tauri.conf.json at {conf_path}")
    if not cargo_path.is_file():
        raise ValueError(f"Missing Cargo.toml at {cargo_path}")
    if not cap_path.is_file():
        raise ValueError(f"Missing capabilities/default.json at {cap_path}")

    conf = json.loads(conf_path.read_text(encoding="utf-8"))

    # 1. CSP security audit
    csp = conf.get("app", {}).get("security", {}).get("csp", "")
    _validate_strict_loopback_csp(csp)

    # 2. Bundle & updater artifacts check
    bundle = conf.get("bundle", {})
    if bundle.get("createUpdaterArtifacts") is not True:
        raise ValueError("bundle.createUpdaterArtifacts must be true in tauri.conf.json")

    # 3. Icons validation
    icon_paths = bundle.get("icon", [])
    if not icon_paths:
        raise ValueError("bundle.icon list is empty in tauri.conf.json")

    validated_icons: list[str] = []
    for rel_icon in icon_paths:
        icon_file = base / rel_icon
        if not icon_file.is_file():
            raise ValueError(f"Missing referenced icon asset: {icon_file}")
        raw = icon_file.read_bytes()
        if len(raw) < 16:
            raise ValueError(f"Icon asset is unexpectedly small/corrupt: {icon_file}")
        suffix = icon_file.suffix.lower()
        if suffix == ".png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Invalid PNG header in {icon_file}")
        if suffix == ".ico" and not raw.startswith(b"\x00\x00\x01\x00"):
            raise ValueError(f"Invalid ICO header in {icon_file}")
        if suffix == ".icns" and not raw.startswith(b"icns"):
            raise ValueError(f"Invalid ICNS header in {icon_file}")
        validated_icons.append(rel_icon)

    # 4. Updater plugin configuration audit (Tauri v2 schema)
    updater = conf.get("plugins", {}).get("updater", {})
    legacy_v1_keys = {"active", "dialog"} & set(updater.keys())
    if legacy_v1_keys:
        raise ValueError(
            f"plugins.updater contains removed Tauri v1 fields {sorted(legacy_v1_keys)}; "
            "only Tauri v2 schema fields (endpoints, pubkey, windows, dangerousInsecureTransportProtocol) are allowed"
        )
    endpoints = updater.get("endpoints", [])
    if not endpoints:
        raise ValueError("plugins.updater.endpoints must not be empty")
    for ep in endpoints:
        if not isinstance(ep, str) or not ep.startswith("https://"):
            raise ValueError(f"Updater endpoint must use HTTPS: {ep}")

    pubkey = updater.get("pubkey", "")
    key_id, _ = _decode_minisign_pubkey(pubkey)

    # 5. Cargo.toml audit
    cargo_text = cargo_path.read_text(encoding="utf-8")
    if "tauri-plugin-updater" not in cargo_text:
        raise ValueError("Cargo.toml must declare tauri-plugin-updater dependency")

    # 6. Capabilities audit
    cap = json.loads(cap_path.read_text(encoding="utf-8"))
    perms = set(cap.get("permissions", []))
    if "core:default" not in perms or "updater:default" not in perms:
        raise ValueError("capabilities/default.json must include core:default and updater:default")

    return {
        "valid": True,
        "version": conf.get("version", "0.0.0"),
        "identifier": conf.get("identifier", ""),
        "icons": validated_icons,
        "endpoints": endpoints,
        "key_id": key_id.hex().upper(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LifeOS Desktop Packaging & Updater Tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="Validate Tauri packaging, icons, CSP, and updater config")
    p_val.add_argument("--tauri-dir", default=str(DEFAULT_TAURI_DIR), help="Path to src-tauri directory")

    sub.add_parser("keygen", help="Generate a new Minisign/Ed25519 keypair (JSON)")

    p_sign = sub.add_parser("sign", help="Sign an update artifact file")
    p_sign.add_argument("artifact", help="Path to artifact file")
    p_sign.add_argument("--private-key", required=True, help="Base64 private key")
    p_sign.add_argument("--comment", default="lifeos-release", help="Trusted comment")

    p_ver = sub.add_parser("verify", help="Verify an update artifact signature")
    p_ver.add_argument("artifact", help="Path to artifact file")
    p_ver.add_argument("--signature", required=True, help="Signature string")
    p_ver.add_argument("--public-key", required=True, help="Base64 public key")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        summary = validate_tauri_assets(Path(args.tauri_dir))
        print(json.dumps(summary, indent=2))
        return 0
    if args.command == "keygen":
        priv, pub = generate_minisign_keypair()
        print(json.dumps({"private_key": priv, "public_key": pub}, indent=2))
        return 0
    if args.command == "sign":
        data = Path(args.artifact).read_bytes()
        sig = sign_payload(data, args.private_key, trusted_comment=args.comment)
        print(sig)
        return 0
    if args.command == "verify":
        data = Path(args.artifact).read_bytes()
        ok = verify_signature(data, args.signature, args.public_key)
        print("VALID" if ok else "INVALID")
        return 0 if ok else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
