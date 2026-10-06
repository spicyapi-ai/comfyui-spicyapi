"""Conversions between ComfyUI values and the files SpicyAPI accepts and returns.

IMAGE is a float tensor shaped [batch, height, width, channels] in 0..1; MASK is [batch, height,
width]; AUDIO is {"waveform": [1, channels, samples], "sample_rate": int}; VIDEO is a
``VideoInput`` from ``comfy_api``.
"""

from __future__ import annotations

import io
import wave
from typing import Any

import numpy as np
import torch
from PIL import Image

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_MEDIA_UPLOAD_BYTES = 90 * 1024 * 1024


def split_image_batch(images: torch.Tensor | None) -> list[torch.Tensor]:
    """One [H, W, C] tensor per picture; a batch connected to one socket counts as several."""
    if images is None:
        return []
    if images.ndim == 3:
        return [images]
    return [images[i] for i in range(images.shape[0])]


def encode_image(image: torch.Tensor) -> tuple[bytes, str]:
    """PNG when it fits the 10 MiB upload ceiling, otherwise a high-quality JPEG."""
    array = (image.detach().cpu().clamp(0, 1).numpy() * 255.0).round().astype(np.uint8)
    if array.ndim == 3 and array.shape[-1] == 1:
        array = array[..., 0]
    picture = Image.fromarray(array)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", optimize=False, compress_level=6)
    if buffer.tell() <= MAX_IMAGE_UPLOAD_BYTES:
        return buffer.getvalue(), "image/png"
    rgb = picture.convert("RGB")
    for quality in (95, 90, 85):
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_IMAGE_UPLOAD_BYTES:
            return buffer.getvalue(), "image/jpeg"
    raise ValueError(
        f"an input image of {rgb.width}x{rgb.height} is too large to upload even as a JPEG; scale it down first"
    )


def encode_audio(audio: dict[str, Any]) -> tuple[bytes, str]:
    """16-bit PCM WAV: lossless and needs nothing beyond the standard library."""
    waveform = audio["waveform"]
    sample_rate = int(audio["sample_rate"])
    if waveform.ndim == 3:
        waveform = waveform[0]
    samples = waveform.detach().cpu().float().clamp(-1, 1).numpy()  # [channels, samples]
    pcm = (samples.T * 32767.0).round().astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(samples.shape[0])
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm.tobytes())
    data = buffer.getvalue()
    if len(data) > MAX_MEDIA_UPLOAD_BYTES:
        raise ValueError("the input audio is longer than the 90 MiB upload limit allows")
    return data, "audio/wav"


def encode_video(video: Any) -> tuple[bytes, str]:
    """MP4 bytes. An MP4 source is remuxed without re-encoding."""
    from comfy_api.latest import Types

    buffer = io.BytesIO()
    video.save_to(buffer, format=Types.VideoContainer.MP4)
    data = buffer.getvalue()
    if len(data) > MAX_MEDIA_UPLOAD_BYTES:
        raise ValueError("the input video is larger than the 90 MiB upload limit")
    return data, "video/mp4"


def decode_images(blobs: list[bytes]) -> tuple[torch.Tensor, torch.Tensor]:
    """An RGB batch plus the alpha of each picture (1 = opaque).

    Transparent areas come out black: a PNG keeps the old colours behind its transparent pixels,
    and showing them would make a background removal look as if it had done nothing. The alpha
    output carries the transparency itself.

    Pictures of different sizes cannot share one batch, so later ones are resized to the size of
    the first.
    """
    pictures = [Image.open(io.BytesIO(blob)) for blob in blobs]
    if not pictures:
        raise ValueError("the task finished without any image")
    width, height = pictures[0].size
    rgb_batch: list[torch.Tensor] = []
    alpha_batch: list[torch.Tensor] = []
    for picture in pictures:
        has_alpha = "A" in picture.getbands() or "transparency" in picture.info
        rgba = picture.convert("RGBA")
        if rgba.size != (width, height):
            rgba = rgba.resize((width, height), Image.Resampling.LANCZOS)
        array = np.asarray(rgba).astype(np.float32) / 255.0
        alpha = array[..., 3] if has_alpha else np.ones((height, width), dtype=np.float32)
        rgb = array[..., :3] * alpha[..., None] if has_alpha else array[..., :3]
        rgb_batch.append(torch.from_numpy(rgb.copy()))
        alpha_batch.append(torch.from_numpy(alpha.copy()))
    return torch.stack(rgb_batch), torch.stack(alpha_batch)


def decode_audio(blob: bytes) -> dict[str, Any]:
    import av

    frames: list[np.ndarray] = []
    with av.open(io.BytesIO(blob)) as container:
        if not container.streams.audio:
            raise ValueError("the result has no audio stream")
        stream = container.streams.audio[0]
        sample_rate = int(stream.codec_context.sample_rate or stream.rate)
        resampler = av.AudioResampler(format="fltp", layout="stereo" if (stream.channels or 1) > 1 else "mono")
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                frames.append(converted.to_ndarray())
        for converted in resampler.resample(None):
            frames.append(converted.to_ndarray())
    if not frames:
        raise ValueError("the result decoded to zero audio samples")
    waveform = torch.from_numpy(np.concatenate(frames, axis=1).astype(np.float32))
    return {"waveform": waveform.unsqueeze(0).contiguous(), "sample_rate": sample_rate}


def decode_video(blob: bytes) -> Any:
    from comfy_api.latest import InputImpl

    return InputImpl.VideoFromFile(io.BytesIO(blob))


def empty_image() -> torch.Tensor:
    return torch.zeros((1, 64, 64, 3), dtype=torch.float32)
