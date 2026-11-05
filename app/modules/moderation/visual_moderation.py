"""Visual media moderation for Smart GC.

Only general adult-content classification is sent to the moderation API.
Do not use this module for known or suspected child-exploitation material.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError
from telegram import Chat, ChatMember, ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.services.group_policy import GroupPolicyRepository
from app.modules.moderation.smart_gc import feature_enabled
from app.services.moderation import ModerationRepository

logger = logging.getLogger(__name__)

_MODERATION_URL = "https://api.openai.com/v1/moderations"
_MODERATION_MODEL = "omni-moderation-latest"
_SEXUAL_THRESHOLD = 0.72
_MAX_DOWNLOAD_BYTES = 18 * 1024 * 1024
_MAX_VIDEO_FRAMES = 5
_VIDEO_SAMPLE_FPS = 0.2
_FRAME_WIDTH = 768
_MUTE_HOURS = 24


def _media_file_ids(message) -> tuple[list[str], bool]:
    """Return downloadable file IDs and whether the media needs video sampling."""
    ids: list[str] = []
    is_video = False

    def add(item, video: bool = False) -> None:
        file_id = getattr(item, "file_id", None)
        if file_id and file_id not in ids:
            ids.append(file_id)
        nonlocal is_video
        is_video = is_video or video

    photos = getattr(message, "photo", None)
    if photos:
        add(photos[-1])

    for field in ("animation", "video", "video_note"):
        item = getattr(message, field, None)
        if item:
            add(item, video=True)

    sticker = getattr(message, "sticker", None)
    if sticker:
        add(sticker, video=bool(getattr(sticker, "is_video", False)))

    document = getattr(message, "document", None)
    if document:
        mime = (getattr(document, "mime_type", None) or "").lower()
        if mime.startswith("image/"):
            add(document)
        elif mime.startswith("video/"):
            add(document, video=True)

    # Gift and paid-media fields vary by Bot API / python-telegram-bot version.
    for container_name in ("gift", "unique_gift"):
        container = getattr(message, container_name, None)
        gift_sticker = getattr(container, "sticker", None)
        if gift_sticker:
            add(gift_sticker, video=bool(getattr(gift_sticker, "is_video", False)))

    for paid in getattr(message, "paid_media", None) or ():
        for field in ("photo", "video"):
            item = getattr(paid, field, None)
            if item:
                if isinstance(item, (list, tuple)):
                    if item:
                        add(item[-1], video=field == "video")
                else:
                    add(item, video=field == "video")

    return ids, is_video


async def _download_media(context, file_id: str) -> bytes | None:
    try:
        telegram_file = await context.bot.get_file(file_id)
        size = getattr(telegram_file, "file_size", None)
        if size is not None and size > _MAX_DOWNLOAD_BYTES:
            return None
        data = bytes(await telegram_file.download_as_bytearray())
        if not data or len(data) > _MAX_DOWNLOAD_BYTES:
            return None
        return data
    except TelegramError:
        logger.info("Smart GC could not download a media attachment", exc_info=True)
        return None


def _normalize_image(data: bytes) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
            image.thumbnail((1024, 1024))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _extract_video_frames(data: bytes) -> list[bytes]:
    """Sample a few frames with ffmpeg; never retain source media on disk."""
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="xyron-visual-") as directory:
        root = Path(directory)
        source = root / "media.bin"
        source.write_bytes(data)
        pattern = root / "frame-%02d.jpg"
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-vf", f"fps={_VIDEO_SAMPLE_FPS},scale={_FRAME_WIDTH}:-2",
            "-frames:v", str(_MAX_VIDEO_FRAMES),
            "-q:v", "5", str(pattern),
        ]
        try:
            subprocess.run(
                command, check=True, timeout=15, capture_output=True
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            logger.warning("ffmpeg unavailable or could not sample Smart GC video")
            return []
        for frame_path in sorted(root.glob("frame-*.jpg"))[:_MAX_VIDEO_FRAMES]:
            try:
                frame = _normalize_image(frame_path.read_bytes())
                if frame:
                    frames.append(frame)
            except OSError:
                continue
    return frames


async def _moderation_flags(api_key: str, images: list[bytes]) -> bool:
    if not images:
        return False
    encoded = [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
            },
        }
        for image in images
    ]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            response = await client.post(
                _MODERATION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": _MODERATION_MODEL, "input": encoded},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("Smart GC visual moderation request failed", exc_info=True)
        return False

    for result in payload.get("results", []):
        scores = result.get("category_scores") or {}
        try:
            if float(scores.get("sexual", 0.0)) >= _SEXUAL_THRESHOLD:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _permissions_from_member(member) -> dict[str, bool]:
    return {
        name: bool(getattr(member, name, False))
        for name in ChatPermissions.__slots__
        if name.startswith("can_")
    }


async def _delete_and_maybe_mute(update, context, message, user) -> None:
    chat = update.effective_chat
    if not chat or not user:
        return

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        logger.info("Smart GC could not inspect media sender", exc_info=True)
        return

    # Telegram group administrators and owners are deletion-only.
    if member.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        try:
            await message.delete()
        except TelegramError:
            logger.warning("Smart GC could not delete flagged admin media", exc_info=True)
        return

    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if not getattr(bot_member, "can_delete_messages", False):
            logger.warning("Smart GC needs Delete Messages permission in %s", chat.id)
            return
        await message.delete()
    except TelegramError:
        logger.warning("Smart GC could not delete flagged media", exc_info=True)
        return

    if not getattr(bot_member, "can_restrict_members", False):
        logger.warning("Smart GC needs Restrict Members permission in %s", chat.id)
        return

    repository = ModerationRepository(context.application.bot_data["sessions"])
    existing = await repository.restriction_permissions(chat.id, user.id)
    if existing is not None:
        original_permissions = existing
        created_snapshot = False
    else:
        if member.status == ChatMember.RESTRICTED:
            original_permissions = _permissions_from_member(member)
        else:
            group = await context.bot.get_chat(chat.id)
            defaults = getattr(group, "permissions", None)
            if defaults is None:
                logger.warning("Smart GC cannot read default permissions for %s", chat.id)
                return
            original_permissions = _permissions_from_member(defaults)
        created_snapshot = await repository.remember_permissions(
            chat.id, user.id, context.bot.id, original_permissions
        )

    try:
        await context.bot.restrict_chat_member(
            chat.id,
            user.id,
            permissions=ChatPermissions(
                **{
                    name: False
                    for name in ChatPermissions.__slots__
                    if name.startswith("can_")
                }
            ),
        )
        expires_at = datetime.now(UTC) + timedelta(hours=_MUTE_HOURS)
        await repository.schedule(
            chat.id, user.id, "unmute", expires_at, original_permissions
        )
        await repository.record(
            chat.id,
            context.bot.id,
            user.id,
            "smartgc_visual_mute",
            "Explicit adult media detected; 24-hour mute",
        )
    except Exception:
        if created_snapshot:
            await repository.forget_permissions(chat.id, user.id)
        logger.exception("Smart GC failed to apply the 24-hour media mute")


async def visual_media_moderation_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Inspect group images/video/stickers/gifts when Smart GC is enabled."""
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if (
        not message
        or not chat
        or not user
        or user.is_bot
        or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}
    ):
        return

    api_key = getattr(context.application.bot_data["settings"], "visual_moderation_api_key", "")
    if not api_key:
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        return
    policy = await GroupPolicyRepository(sessions).get(chat.id)
    smart_gc = policy.get("smart_gc", {})
    if (
        not isinstance(smart_gc, dict)
        or not smart_gc.get("enabled")
        or not feature_enabled(smart_gc, "nsfw_detection")
    ):
        return
    approved = {int(value) for value in policy.get("approved_users", [])}
    if feature_enabled(smart_gc, "approved_exemption") and user.id in approved:
        return

    file_ids, is_video = _media_file_ids(message)
    if not file_ids:
        return

    images: list[bytes] = []
    for file_id in file_ids[:4]:
        raw = await _download_media(context, file_id)
        if not raw:
            continue
        if is_video:
            images.extend(await asyncio.to_thread(_extract_video_frames, raw))
        else:
            normalized = _normalize_image(raw)
            if normalized:
                images.append(normalized)
        if len(images) >= _MAX_VIDEO_FRAMES:
            images = images[:_MAX_VIDEO_FRAMES]
            break

    if await _moderation_flags(api_key, images):
        await _delete_and_maybe_mute(update, context, message, user)
