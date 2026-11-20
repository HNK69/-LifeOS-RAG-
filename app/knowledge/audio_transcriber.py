"""
audio_transcriber.py

Whisper-based speech-to-text (STT) audio transcription for LifeOS.
Lazy-loads Whisper models from HuggingFace Transformers to avoid startup
penalties and keep offline/test environments deterministic.
"""

from __future__ import annotations

import logging
import os
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

try:
    from config import WHISPER_DEVICE, WHISPER_MODEL
except ImportError:
    from app.config import WHISPER_DEVICE, WHISPER_MODEL

logger = logging.getLogger("lifeos.knowledge.audio")

_processor = None
_model = None
_mock_transcriber: Callable[[Path], dict[str, Any]] | None = None


def set_mock_transcriber(fn: Callable[[Path], dict[str, Any]] | None) -> None:
    """Set a mock transcriber for deterministic testing without model weights."""
    global _mock_transcriber
    _mock_transcriber = fn


def reset_mock_transcriber() -> None:
    """Reset mock transcriber back to None."""
    global _mock_transcriber
    _mock_transcriber = None


def get_whisper_model(model_name: str | None = None, device: str | None = None):
    """Lazily load and return the Whisper processor and model."""
    global _processor, _model
    if _processor is None or _model is None:
        target_model = model_name or os.getenv("LIFEOS_WHISPER_MODEL", WHISPER_MODEL)
        target_device = device or os.getenv("LIFEOS_WHISPER_DEVICE", WHISPER_DEVICE)

        logger.info(f"Loading Whisper model '{target_model}' on {target_device}...")
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        _processor = WhisperProcessor.from_pretrained(target_model)
        _model = WhisperForConditionalGeneration.from_pretrained(target_model)
        _model.to(target_device)
        _model.eval()

    return _processor, _model


def load_audio_waveform(file_path: Path | str, target_sr: int = 16000) -> tuple[np.ndarray, float]:
    """
    Read an audio file (.wav, .mp3) and return (waveform_float32, duration_seconds) at target_sr.
    Supports WAV, MP3, FLAC, and OGG natively via soundfile with fallback to wave and scipy.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # 1. Primary: Use soundfile for universal format decoding (WAV, MP3, FLAC, etc.)
    try:
        import soundfile as sf
        data, sr = sf.read(str(path), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        duration = len(data) / sr if sr > 0 else 0.0

        if sr != target_sr and len(data) > 0:
            from scipy import signal
            num_target_samples = int(len(data) * target_sr / sr)
            data = signal.resample(data, num_target_samples)

        return data.astype(np.float32), duration
    except Exception as sf_exc:
        logger.debug(f"soundfile read failed for {path.name}, attempting wave/scipy: {sf_exc}")

    # 2. Fallback: Standard WAV reader using standard library wave
    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw_data = wf.readframes(n_frames)

        if sampwidth == 2:
            data = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            data = np.frombuffer(raw_data, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sampwidth == 1:
            data = (np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            data = np.frombuffer(raw_data, dtype=np.float32)

        if n_channels > 1:
            data = data.reshape(-1, n_channels).mean(axis=1)

        duration = len(data) / framerate if framerate > 0 else 0.0

        if framerate != target_sr and len(data) > 0:
            num_target_samples = int(len(data) * target_sr / framerate)
            from scipy import signal
            data = signal.resample(data, num_target_samples)

        return data.astype(np.float32), duration
    except Exception as exc:
        # Fallback to scipy if wave module encounters non-standard RIFF chunks
        try:
            import scipy.io.wavfile as wavfile
            sr, data = wavfile.read(str(path))
            if data.dtype == np.int16:
                data = data.astype(np.float32) / 32768.0
            elif data.dtype == np.int32:
                data = data.astype(np.float32) / 2147483648.0
            elif data.dtype == np.uint8:
                data = (data.astype(np.float32) - 128.0) / 128.0
            else:
                data = data.astype(np.float32)

            if data.ndim > 1:
                data = data.mean(axis=1)

            duration = len(data) / sr if sr > 0 else 0.0
            if sr != target_sr and len(data) > 0:
                from scipy import signal
                num_target_samples = int(len(data) * target_sr / sr)
                data = signal.resample(data, num_target_samples)
            return data.astype(np.float32), duration
        except Exception:
            logger.warning(f"Failed to read audio file for {path.name}: {exc}")
            return np.zeros(16000, dtype=np.float32), 1.0


def transcribe_audio(
    file_path: Path | str,
    model_name: str | None = None,
    device: str | None = None,
) -> dict[str, Any]:
    """
    Transcribe an audio file using Whisper.

    Returns:
        {
            "text": str,
            "language": str,
            "duration": float,
            "filename": str,
        }
    """
    path = Path(file_path).resolve()

    # 1. Check if mock transcriber is registered or env set
    if _mock_transcriber is not None:
        return _mock_transcriber(path)

    if os.getenv("LIFEOS_MOCK_STT", "").lower() in ("true", "1", "yes"):
        # Deterministic offline mock for testing environments
        return {
            "text": f"Transcribed speech content from audio file {path.name}.",
            "language": "en",
            "duration": 5.0,
            "filename": path.name,
        }

    # 2. Extract audio waveform
    waveform, duration = load_audio_waveform(path, target_sr=16000)

    # 3. Model inference
    try:
        import torch
        processor, model = get_whisper_model(model_name, device)

        inputs = processor(waveform, sampling_rate=16000, return_tensors="pt")
        input_features = inputs.input_features.to(model.device)

        with torch.no_grad():
            predicted_ids = model.generate(input_features)

        transcription = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
        text = transcription.strip()

        return {
            "text": text,
            "language": "en",
            "duration": duration,
            "filename": path.name,
        }
    except Exception as exc:
        logger.error(f"Whisper transcription failed for {path.name}: {exc}", exc_info=True)
        return {
            "text": "",
            "language": "unknown",
            "duration": duration,
            "filename": path.name,
            "error": str(exc),
        }
