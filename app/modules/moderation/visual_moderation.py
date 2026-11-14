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
import time
from types import SimpleNamespace
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
_VIDEO_SAMPLE_FPS = 1.0
_FRAME_WIDTH = 768
_MUTE_HOURS = 24
_VISUAL_JOB_TIMEOUT_SECONDS = 8.0
_VISUAL_WORKER_COUNT = 3
_RESULT_CACHE_TTL_SECONDS = 6 * 60 * 60
_RESULT_CACHE_MAX_ENTRIES = 5000


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


async def _download_media(context, file_id: str, *, chat_id: int, message_id: int) -> bytes | None:
    """Download Telegram media with a longer read timeout and bounded retries."""
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            telegram_file = await context.bot.get_file(file_id, read_timeout=30)
            size = getattr(telegram_file, "file_size", None)
            if size is not None and size > _MAX_DOWNLOAD_BYTES:
                logger.warning(
                    "SMARTGC_VISUAL stage=download result=skipped reason=file_too_large "
                    "chat_id=%s message_id=%s size=%s limit=%s",
                    chat_id, message_id, size, _MAX_DOWNLOAD_BYTES,
                )
                return None
            data = bytes(
                await telegram_file.download_as_bytearray(read_timeout=120)
            )
            if not data or len(data) > _MAX_DOWNLOAD_BYTES:
                logger.warning(
                    "SMARTGC_VISUAL stage=download result=skipped reason=empty_or_too_large "
                    "chat_id=%s message_id=%s bytes=%s limit=%s",
                    chat_id, message_id, len(data), _MAX_DOWNLOAD_BYTES,
                )
                return None
            logger.info(
                "SMARTGC_VISUAL stage=download result=ok chat_id=%s message_id=%s "
                "bytes=%s attempt=%s",
                chat_id, message_id, len(data), attempt,
            )
            return data
        except TelegramError as exc:
            logger.warning(
                "SMARTGC_VISUAL stage=download result=retryable_error "
                "chat_id=%s message_id=%s attempt=%s/%s error_type=%s error=%s",
                chat_id, message_id, attempt, attempts,
                type(exc).__name__, str(exc)[:300],
                exc_info=True,
            )
            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)
    logger.error(
        "SMARTGC_VISUAL stage=download result=failed_after_retries "
        "chat_id=%s message_id=%s attempts=%s",
        chat_id, message_id, attempts,
    )
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
        except FileNotFoundError:
            logger.warning("SMARTGC_VISUAL stage=decode result=ffmpeg_missing")
            return []
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
            logger.warning(
                "SMARTGC_VISUAL stage=decode result=ffmpeg_error detail=%s",
                detail[:400],
            )
            return []
        except subprocess.TimeoutExpired:
            logger.warning("SMARTGC_VISUAL stage=decode result=ffmpeg_timeout")
            return []
        for frame_path in sorted(root.glob("frame-*.jpg"))[:_MAX_VIDEO_FRAMES]:
            try:
                frame = _normalize_image(frame_path.read_bytes())
                if frame:
                    frames.append(frame)
            except OSError:
                continue
    return frames


async def _moderation_flags(
    api_key: str, images: list[bytes], *, chat_id: int, message_id: int
) -> bool | None:
    if not images:
        logger.warning(
            "SMARTGC_VISUAL stage=moderation result=skipped reason=no_decodable_frames "
            "chat_id=%s message_id=%s",
            chat_id, message_id,
        )
        return None
    logger.info(
        "SMARTGC_VISUAL stage=moderation result=requesting chat_id=%s message_id=%s images=%s",
        chat_id, message_id, len(images),
    )
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
        logger.warning(
            "SMARTGC_VISUAL stage=moderation result=api_error chat_id=%s message_id=%s",
            chat_id, message_id, exc_info=True,
        )
        return None

    results = payload.get("results", [])
    sexual_scores: list[float] = []
    for result in results:
        scores = result.get("category_scores") or {}
        try:
            sexual_scores.append(float(scores.get("sexual", 0.0)))
        except (TypeError, ValueError):
            continue
    max_score = max(sexual_scores, default=0.0)
    flagged = max_score >= _SEXUAL_THRESHOLD
    logger.info(
        "SMARTGC_VISUAL stage=moderation result=%s chat_id=%s message_id=%s "
        "api_results=%s max_sexual_score=%.4f threshold=%.2f",
        "flagged" if flagged else "clear",
        chat_id, message_id, len(results), max_score, _SEXUAL_THRESHOLD,
    )
    return flagged


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
    async def delete_target():
        return await context.bot.delete_message(chat_id=chat.id, message_id=message.message_id)

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError:
        logger.info("Smart GC could not inspect media sender", exc_info=True)
        return

    # Telegram group administrators and owners are deletion-only.
    if member.status in {ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        try:
            await delete_target()
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


async def enqueue_visual_media_moderation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Queue media inspection so downloads/API calls never block update dispatch."""
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if (
        not message or not chat or not user or user.is_bot
        or chat.type not in {Chat.GROUP, Chat.SUPERGROUP}
    ):
        logger.debug(
            "SMARTGC_VISUAL stage=enqueue result=skipped reason=not_human_group_message"
        )
        return
    queue = context.application.bot_data.get("visual_moderation_queue")
    if queue is None:
        logger.error(
            "SMARTGC_VISUAL stage=enqueue result=failed reason=queue_missing "
            "chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
        return
    media_kind = (
        "sticker" if message.sticker else
        "photo" if message.photo else
        "video" if message.video else
        "animation" if message.animation else
        "video_note" if message.video_note else
        "document" if message.document else
        "paid_media" if getattr(message, "paid_media", None) else
        "other"
    )
    try:
        media_fields = ("photo", "sticker", "animation", "video", "video_note", "document")
        media = {}
        for field in media_fields:
            value = getattr(message, field, None)
            if field == "photo" and value:
                value = value[-1]
            if value:
                media[field] = {
                    "file_id": getattr(value, "file_id", None),
                    "file_unique_id": getattr(value, "file_unique_id", None),
                    "mime_type": getattr(value, "mime_type", None),
                    "is_video": bool(getattr(value, "is_video", False)),
                    "is_animated": bool(getattr(value, "is_animated", False)),
                    "is_premium": bool(getattr(value, "is_premium", False)),
                }
        if not media:
            logger.debug("SMARTGC_VISUAL stage=enqueue result=skipped reason=no_media_metadata chat_id=%s message_id=%s", chat.id, message.message_id)
            return
        job = {
            "chat_id": chat.id,
            "message_id": message.message_id,
            "user_id": user.id,
            "media_kind": media_kind,
            "media": media,
            "enqueued_at": time.monotonic(),
        }
        queue.put_nowait(job)
        logger.info(
            "SMARTGC_VISUAL stage=enqueue result=queued chat_id=%s message_id=%s "
            "user_id=%s media_kind=%s queue_size=%s",
            chat.id, message.message_id, user.id, media_kind, queue.qsize(),
        )
    except asyncio.QueueFull:
        logger.error(
            "SMARTGC_VISUAL stage=enqueue result=failed reason=queue_full "
            "chat_id=%s message_id=%s queue_size=%s",
            chat.id, message.message_id, queue.qsize(),
        )


async def visual_moderation_worker(application, stop_event: asyncio.Event) -> None:
    """Process compact metadata jobs; no Update or CallbackContext is retained."""
    queue = application.bot_data["visual_moderation_queue"]
    while not stop_event.is_set() or not queue.empty():
        try:
            job = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        started = time.monotonic()
        chat_id, message_id = job["chat_id"], job["message_id"]
        wait_ms = round((started - job["enqueued_at"]) * 1000)
        logger.info(
            "SMARTGC_VISUAL stage=worker result=started chat_id=%s message_id=%s "
            "queue_wait_ms=%s queue_size=%s",
            chat_id, message_id, wait_ms, queue.qsize(),
        )
        try:
            media_objects = {}
            for field, metadata in job["media"].items():
                media_objects[field] = SimpleNamespace(**metadata)
            message = SimpleNamespace(
                message_id=message_id,
                photo=[media_objects["photo"]] if "photo" in media_objects else None,
                sticker=media_objects.get("sticker"),
                animation=media_objects.get("animation"),
                video=media_objects.get("video"),
                video_note=media_objects.get("video_note"),
                document=media_objects.get("document"),
                paid_media=None,
            )
            chat = SimpleNamespace(id=chat_id, type=Chat.SUPERGROUP)
            user = SimpleNamespace(id=job["user_id"], is_bot=False)
            update = SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=user)
            context = SimpleNamespace(application=application, bot=application.bot)
            await asyncio.wait_for(
                visual_media_moderation_handler(update, context),
                timeout=_VISUAL_JOB_TIMEOUT_SECONDS,
            )
            logger.info(
                "SMARTGC_VISUAL stage=worker result=finished chat_id=%s message_id=%s "
                "duration_ms=%s total_ms=%s",
                chat_id, message_id, round((time.monotonic() - started) * 1000),
                round((time.monotonic() - job["enqueued_at"]) * 1000),
            )
        except asyncio.TimeoutError:
            logger.error(
                "SMARTGC_VISUAL stage=worker result=timeout chat_id=%s message_id=%s "
                "deadline_seconds=%.1f total_ms=%s",
                chat_id, message_id, _VISUAL_JOB_TIMEOUT_SECONDS,
                round((time.monotonic() - job["enqueued_at"]) * 1000),
            )
        except Exception:
            logger.exception("Smart GC visual worker failed chat_id=%s message_id=%s", chat_id, message_id)
        finally:
            queue.task_done()


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
        logger.debug("SMARTGC_VISUAL stage=handler result=skipped reason=invalid_sender_or_chat")
        return

    logger.info(
        "SMARTGC_VISUAL stage=handler result=started chat_id=%s message_id=%s "
        "user_id=%s sticker=%s photo=%s video=%s animation=%s document=%s",
        chat.id, message.message_id, user.id,
        bool(message.sticker), bool(message.photo), bool(message.video),
        bool(message.animation), bool(message.document),
    )
    settings = context.application.bot_data.get("settings")
    api_key = getattr(settings, "visual_moderation_api_key", "") if settings else ""
    if not api_key:
        logger.error(
            "SMARTGC_VISUAL stage=config result=skipped reason=moderation_api_key_missing "
            "chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
        return

    sessions = context.application.bot_data.get("sessions")
    if sessions is None:
        logger.error(
            "SMARTGC_VISUAL stage=policy result=skipped reason=session_factory_missing "
            "chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
        return
    policy = await GroupPolicyRepository(sessions).get(chat.id)
    smart_gc = policy.get("smart_gc", {})
    enabled = isinstance(smart_gc, dict) and bool(smart_gc.get("enabled"))
    nsfw_enabled = feature_enabled(smart_gc, "nsfw_detection")
    exemption_enabled = feature_enabled(smart_gc, "approved_exemption")
    logger.info(
        "SMARTGC_VISUAL stage=policy result=checked chat_id=%s message_id=%s "
        "smart_gc_enabled=%s nsfw_detection=%s approved_exemption=%s",
        chat.id, message.message_id, enabled, nsfw_enabled, exemption_enabled,
    )
    if not isinstance(smart_gc, dict) or not enabled or not nsfw_enabled:
        logger.info(
            "SMARTGC_VISUAL stage=policy result=skipped reason=feature_disabled "
            "chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
        return
    approved = {int(value) for value in policy.get("approved_users", [])}
    if exemption_enabled and user.id in approved:
        logger.info(
            "SMARTGC_VISUAL stage=policy result=skipped reason=approved_user "
            "chat_id=%s message_id=%s user_id=%s",
            chat.id, message.message_id, user.id,
        )
        return

    file_ids, is_video = _media_file_ids(message)
    sticker = message.sticker
    if sticker:
        logger.info(
            "SMARTGC_VISUAL stage=media result=identified chat_id=%s message_id=%s "
            "kind=sticker is_video=%s is_animated=%s is_premium=%s",
            chat.id, message.message_id,
            bool(getattr(sticker, "is_video", False)),
            bool(getattr(sticker, "is_animated", False)),
            bool(getattr(sticker, "is_premium", False)),
        )
    if not file_ids:
        logger.warning(
            "SMARTGC_VISUAL stage=media result=skipped reason=no_supported_file_id "
            "chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
        return

    images: list[bytes] = []
    for file_id in file_ids[:4]:
        raw = await _download_media(
            context, file_id, chat_id=chat.id, message_id=message.message_id
        )
        if not raw:
            continue
        if is_video:
            extracted = await asyncio.to_thread(_extract_video_frames, raw)
            logger.info(
                "SMARTGC_VISUAL stage=decode result=%s chat_id=%s message_id=%s "
                "frames=%s media_bytes=%s",
                "ok" if extracted else "no_frames",
                chat.id, message.message_id, len(extracted), len(raw),
            )
            images.extend(extracted)
        else:
            normalized = await asyncio.to_thread(_normalize_image, raw)
            if normalized:
                images.append(normalized)
                logger.info(
                    "SMARTGC_VISUAL stage=decode result=ok chat_id=%s message_id=%s "
                    "images=%s",
                    chat.id, message.message_id, len(images),
                )
            else:
                logger.warning(
                    "SMARTGC_VISUAL stage=decode result=unsupported_format "
                    "chat_id=%s message_id=%s bytes=%s video=%s sticker_animated=%s",
                    chat.id, message.message_id, len(raw), is_video,
                    bool(getattr(sticker, "is_animated", False)) if sticker else False,
                )
        if len(images) >= _MAX_VIDEO_FRAMES:
            images = images[:_MAX_VIDEO_FRAMES]
            break

    cache = context.application.bot_data.setdefault("visual_moderation_result_cache", {})
    cache_key = tuple(sorted(
        getattr(item, "file_unique_id", None) or getattr(item, "file_id", "")
        for field in ("photo", "sticker", "animation", "video", "video_note", "document")
        for item in ([getattr(message, field)[-1]] if field == "photo" and getattr(message, field, None) else [getattr(message, field)] if getattr(message, field, None) else [])
    ))
    now = time.monotonic()
    cached = cache.get(cache_key)
    if cached and now - cached[0] < _RESULT_CACHE_TTL_SECONDS:
        flagged = cached[1]
        cache_status = "hit"
    else:
        flagged = await _moderation_flags(
            api_key, images, chat_id=chat.id, message_id=message.message_id
        )
        cache_status = "miss"
        if flagged is not None:
            cache[cache_key] = (now, flagged)
            if len(cache) > _RESULT_CACHE_MAX_ENTRIES:
                cutoff = now - _RESULT_CACHE_TTL_SECONDS
                for old_key, (created_at, _) in list(cache.items()):
                    if created_at < cutoff:
                        cache.pop(old_key, None)
                while len(cache) > _RESULT_CACHE_MAX_ENTRIES:
                    cache.pop(next(iter(cache)))
    logger.info(
        "SMARTGC_VISUAL stage=result_cache result=%s chat_id=%s message_id=%s",
        cache_status, chat.id, message.message_id,
    )
    if flagged is None:
        logger.error(
            "SMARTGC_VISUAL stage=action result=unknown reason=moderation_unavailable "
            "chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
        return
    if flagged:
        logger.warning(
            "SMARTGC_VISUAL stage=action result=flagged chat_id=%s message_id=%s user_id=%s",
            chat.id, message.message_id, user.id,
        )
        await _delete_and_maybe_mute(update, context, message, user)
    else:
        logger.info(
            "SMARTGC_VISUAL stage=action result=no_action chat_id=%s message_id=%s",
            chat.id, message.message_id,
        )
