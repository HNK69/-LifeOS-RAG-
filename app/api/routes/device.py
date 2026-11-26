"""
device.py

REST API endpoints for device identity, peer trust management,
pairing handshakes, and cryptographic envelope sealing/opening.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.api.auth import AuthenticatedUser, _extract_token, verify_token
from app.api.dependencies import require_auth
from app.api.schemas import (
    CryptographicEnvelopeSchema,
    DeviceIdentityResponse,
    OpenEnvelopeRequest,
    OpenEnvelopeResponse,
    PairingInitiateRequest,
    PairingInitiateResponse,
    PairingVerifyRequest,
    PairingVerifyResponse,
    PeerDeviceResponse,
    SealEnvelopeRequest,
)
from app.security.device_identity import get_device_identity
from app.security.device_store import (
    create_pairing_challenge,
    get_trusted_device,
    list_trusted_devices,
    revoke_trusted_device,
    verify_pairing_challenge,
    verify_pairing_token,
)
from app.security.envelope import (
    CryptographicEnvelope,
    DecryptionError,
    SignatureVerificationError,
    open_payload,
    seal_payload,
)

logger = logging.getLogger("lifeos.api.device")

router = APIRouter()


@router.get(
    "/identity",
    response_model=DeviceIdentityResponse,
    summary="Get Local Device Identity",
    description="Returns public cryptographic identity, key material, and system metadata for this LifeOS instance.",
)
def get_local_device_identity(user: AuthenticatedUser = Depends(require_auth)):
    device = get_device_identity()
    return DeviceIdentityResponse(
        device_id=device.device_id,
        device_name=device.device_name,
        device_type=device.device_type,
        os_info=device.os_info,
        signing_public_key=device.signing_public_key_b64,
        exchange_public_key=device.exchange_public_key_b64,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
    )


@router.get(
    "/peers",
    response_model=list[PeerDeviceResponse],
    summary="List Trusted Peer Devices",
    description="Returns all registered peer devices in the local trust store.",
)
def list_peers(
    status_filter: str | None = Query(None, alias="status", description="Filter by trust status ('paired', 'revoked')"),
    user: AuthenticatedUser = Depends(require_auth),
):
    peers = list_trusted_devices(status_filter=status_filter)
    return [PeerDeviceResponse(**p) for p in peers]


@router.get(
    "/peers/{device_id}",
    response_model=PeerDeviceResponse,
    summary="Get Trusted Peer Device Details",
)
def get_peer(
    device_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    peer = get_trusted_device(device_id)
    if not peer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Peer device '{device_id}' not found in trust store.",
        )
    return PeerDeviceResponse(**peer)


@router.delete(
    "/peers/{device_id}",
    summary="Revoke Trusted Peer Device",
    description="Revokes peer device trust status, rejecting future encrypted payloads or sync events.",
)
def revoke_peer(
    device_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    peer = get_trusted_device(device_id)
    if not peer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Peer device '{device_id}' not found.",
        )
    revoked = revoke_trusted_device(device_id)
    return {"revoked": revoked, "device_id": device_id, "status": "revoked"}


@router.post(
    "/peers/{device_id}/wipe",
    summary="Request Remote Wipe for Trusted Peer Device",
    description="Revokes trust and dispatches a privacy.remote_wipe directive for a paired mobile companion.",
)
def request_peer_remote_wipe(
    device_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    peer = get_trusted_device(device_id)
    if not peer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Peer device '{device_id}' not found.",
        )
    revoked = revoke_trusted_device(device_id)
    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type="privacy.remote_wipe",
            producer="device",
            payload={"device_id": device_id, "actor": user.user_id, "wipe_scope": "all"},
        ))
    except Exception:
        pass
    return {"wiped": True, "revoked": revoked, "device_id": device_id, "status": "revoked"}


@router.post(
    "/pairing/initiate",
    response_model=PairingInitiateResponse,
    summary="Initiate Device Pairing",
    description="Generates an ephemeral 6-digit cryptographic pairing challenge and QR metadata for a new peer.",
)
def initiate_pairing(
    request: Request,
    req: PairingInitiateRequest | None = None,
    user: AuthenticatedUser = Depends(require_auth),
):
    effective_req = req or PairingInitiateRequest()
    challenge = create_pairing_challenge(
        peer_device_id=effective_req.peer_device_id or "*",
        ttl_seconds=effective_req.ttl_seconds,
    )
    device = get_device_identity()
    try:
        import config as cfg
    except ImportError:
        from app import config as cfg
    default_host = getattr(cfg, "API_HOST", "127.0.0.1")
    default_port = int(getattr(cfg, "API_PORT", 8000))
    req_host = (
        request.url.hostname
        if request.url.hostname and request.url.hostname != "testserver"
        else default_host
    )
    req_port = int(request.url.port) if request.url.port else default_port

    return PairingInitiateResponse(
        challenge_id=challenge["challenge_id"],
        peer_device_id=challenge["peer_device_id"],
        challenge_code=challenge["challenge_code"],
        expires_at=challenge["expires_at"],
        created_at=challenge["created_at"],
        host=req_host,
        port=req_port,
        desktop_device_id=device.device_id,
        signing_public_key=device.signing_public_key_b64,
        exchange_public_key=device.exchange_public_key_b64,
        pairing_token=challenge.get("pairing_token"),
    )


@router.post(
    "/pairing/verify",
    response_model=PairingVerifyResponse,
    summary="Verify Device Pairing Challenge",
    description="Verifies the 6-digit challenge code and peer signature, promoting peer to trusted.",
)
def verify_pairing(
    req: PairingVerifyRequest,
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    try:
        import config as cfg
    except ImportError:
        from app import config as cfg

    if getattr(cfg, "API_AUTH_ENABLED", True):
        token = _extract_token(authorization=authorization, x_api_key=x_api_key)
        auth_user = verify_token(token) if token else None
        if not auth_user and not (token and verify_pairing_token(req.challenge_id, token)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required: valid API key or active pairing token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    try:
        success = verify_pairing_challenge(
            challenge_id=req.challenge_id,
            challenge_code=req.challenge_code,
            peer_device_id=req.peer_device_id,
            peer_device_name=req.peer_device_name,
            peer_device_type=req.peer_device_type,
            peer_signing_pub_b64=req.peer_signing_public_key,
            peer_exchange_pub_b64=req.peer_exchange_public_key,
            signature_b64=req.signature,
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(ve),
        )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pairing verification failed: invalid code, expired challenge, or cryptographic signature mismatch.",
        )
    peer = get_trusted_device(req.peer_device_id)
    return PairingVerifyResponse(
        success=True,
        message="Device successfully verified and registered in trusted peer store.",
        device_id=req.peer_device_id,
        device_token=peer.get("device_token") if peer else None,
    )


@router.post(
    "/envelope/seal",
    response_model=CryptographicEnvelopeSchema,
    summary="Seal Payload in Cryptographic Envelope",
    description="Encrypts and signs a payload using asymmetric X25519-HKDF-AES256GCM for a trusted peer.",
)
def seal_envelope_endpoint(
    req: SealEnvelopeRequest,
    user: AuthenticatedUser = Depends(require_auth),
):
    peer = get_trusted_device(req.recipient_device_id)
    if not peer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recipient device '{req.recipient_device_id}' is not trusted or paired.",
        )
    if peer.get("trust_status") != "paired":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot seal envelope for peer with status '{peer.get('trust_status')}'.",
        )

    envelope = seal_payload(
        payload=req.payload.encode("utf-8"),
        recipient_exchange_public_key_b64=peer["exchange_public_key"],
        recipient_device_id=req.recipient_device_id,
    )
    return CryptographicEnvelopeSchema(**envelope.to_dict())


@router.post(
    "/envelope/open",
    response_model=OpenEnvelopeResponse,
    summary="Open Cryptographic Envelope",
    description="Decrypts and verifies an authenticated envelope addressed to this device.",
)
def open_envelope_endpoint(
    req: OpenEnvelopeRequest,
    user: AuthenticatedUser = Depends(require_auth),
):
    device = get_device_identity()
    env_dict = req.envelope.model_dump()

    # Look up sender in trusted peer store to verify signature
    sender_peer = get_trusted_device(req.envelope.sender_device_id)
    sender_pub_key = sender_peer["signing_public_key"] if sender_peer else None

    try:
        plaintext_bytes = open_payload(
            envelope=env_dict,
            recipient_identity=device,
            sender_signing_public_key_b64=sender_pub_key,
        )
        return OpenEnvelopeResponse(
            success=True,
            payload=plaintext_bytes.decode("utf-8", errors="replace"),
            sender_device_id=req.envelope.sender_device_id,
        )
    except SignatureVerificationError as sig_err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Envelope signature verification failed: {sig_err}",
        )
    except DecryptionError as dec_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Envelope decryption failed: {dec_err}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to open envelope: {exc}",
        )
