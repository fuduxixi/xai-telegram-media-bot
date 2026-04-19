# Author: by fuduxixi

import argparse
import asyncio
import base64
import json
import logging
import mimetypes
import os
import shlex
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PIL import Image, ImageOps
from telegram import Document, InputMediaPhoto, Message, Update
from telegram.ext import Application, CommandHandler, ContextTypes

BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR / ".env"


def load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if value and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
            value = value[1:-1]

        os.environ.setdefault(key, value)


load_local_env(DOTENV_PATH)

LOG_LEVEL_NAME = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=LOG_LEVEL,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

XAI_VIDEO_CREATE_URL = "https://api.x.ai/v1/videos/generations"
XAI_VIDEO_STATUS_URL = "https://api.x.ai/v1/videos/{request_id}"
XAI_IMAGE_CREATE_URL = "https://api.x.ai/v1/images/generations"
XAI_REQUEST_TIMEOUT = 120
XAI_POLL_TIMEOUT = 60
XAI_DOWNLOAD_TIMEOUT = 300
XAI_FAILOVER_STATUS_CODES = {401, 403, 408, 429, 500, 502, 503, 504}


def load_xai_api_keys() -> list[str]:
    raw_keys = os.getenv("XAI_API_KEYS", "").strip()
    if raw_keys:
        keys = [item.strip() for item in raw_keys.split(",") if item.strip()]
        if keys:
            return keys

    single_key = os.getenv("XAI_API_KEY", "").strip()
    if single_key:
        return [single_key]

    raise KeyError("需要在 .env 中设置 XAI_API_KEY 或 XAI_API_KEYS")


XAI_API_KEYS = load_xai_api_keys()

DEFAULT_DURATION = 10
DEFAULT_RATIO = "16:9"
DEFAULT_RESOLUTION = "720p"
MAX_TEXT_ATTACHMENT_BYTES = 1024 * 1024
TEXT_ATTACHMENT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml"}
TEXT_ATTACHMENT_MIME_PREFIXES = ("text/",)
TEXT_ATTACHMENT_MIME_ALLOWLIST = {
    "application/json",
    "application/x-yaml",
    "application/yaml",
    "application/csv",
}
DEFAULT_IMAGE_RATIO = os.getenv("XAI_IMAGE_DEFAULT_RATIO", "1:1").strip() or "1:1"
DEFAULT_IMAGE_MODEL = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image").strip() or "grok-imagine-image"
DEFAULT_IMAGE_COUNT = int(os.getenv("XAI_IMAGE_DEFAULT_N", "1").strip() or "1")
MAX_IMAGE_COUNT = int(os.getenv("XAI_IMAGE_MAX_N", "4").strip() or "4")
DEFAULT_VIDEO_COUNT = int(os.getenv("XAI_VIDEO_DEFAULT_N", "1").strip() or "1")
MAX_VIDEO_COUNT = int(os.getenv("XAI_VIDEO_MAX_N", "4").strip() or "4")
VIDEO_AUTO_REWRITE_ON_MODERATION = os.getenv("VIDEO_AUTO_REWRITE_ON_MODERATION", "0").strip().lower() in {"1", "true", "yes", "on"}
VIDEO_REWRITE_MODE = (os.getenv("VIDEO_REWRITE_MODE", "").strip().lower() or ("mild" if VIDEO_AUTO_REWRITE_ON_MODERATION else "off"))
if VIDEO_REWRITE_MODE not in {"off", "mild", "strong"}:
    VIDEO_REWRITE_MODE = "mild" if VIDEO_AUTO_REWRITE_ON_MODERATION else "off"
MAX_WAIT_SECONDS = 900
POLL_INTERVAL_SECONDS = 5

ALLOWED_VIDEO_RATIOS = {"16:9", "9:16", "1:1", "3:2", "2:3"}
ALLOWED_IMAGE_RATIOS = {"auto", "16:9", "9:16", "1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20"}
ALLOWED_IMAGE_MODELS = {"grok-imagine-image", "grok-imagine-image-pro"}
ALLOWED_RESOLUTIONS = {"480p", "720p"}

DEFAULT_IMAGE_COUNT = max(1, min(DEFAULT_IMAGE_COUNT, 4))
MAX_IMAGE_COUNT = max(1, min(MAX_IMAGE_COUNT, 4))
DEFAULT_VIDEO_COUNT = max(1, min(DEFAULT_VIDEO_COUNT, 4))
MAX_VIDEO_COUNT = max(1, min(MAX_VIDEO_COUNT, 4))
if DEFAULT_VIDEO_COUNT > MAX_VIDEO_COUNT:
    DEFAULT_VIDEO_COUNT = MAX_VIDEO_COUNT
if DEFAULT_IMAGE_MODEL not in ALLOWED_IMAGE_MODELS:
    DEFAULT_IMAGE_MODEL = "grok-imagine-image"
if DEFAULT_IMAGE_RATIO not in ALLOWED_IMAGE_RATIOS:
    DEFAULT_IMAGE_RATIO = "1:1"

JOB_QUEUE: asyncio.Queue | None = None
JOB_WORKER_TASK: asyncio.Task | None = None
RELOAD_WATCHER_TASK: asyncio.Task | None = None
STATUS_FILE = Path(os.getenv("BOT_STATUS_FILE", str(BASE_DIR / "data" / "bot-status.json")))
RELOAD_SIGNAL_FILE = Path(os.getenv("BOT_RELOAD_SIGNAL_FILE", str(BASE_DIR / "data" / "reload-bot.signal")))


@dataclass
class Job:
    kind: str
    chat_id: int
    prompt: str
    user_id: int
    duration: int | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    cached_image_path: str | None = None
    image_model: str | None = None
    image_count: int | None = None
    video_count: int | None = None
    preferred_key_index: int | None = None
    progress_message_ids: list[int] = field(default_factory=list)
    rewrite_mode: str = "off"


def load_allowed_user_ids() -> set[int]:
    raw = os.getenv("TG_ALLOWED_USER_IDS", "").strip()
    if not raw:
        return set()

    result = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        result.add(int(item))
    return result


ALLOWED_USER_IDS = load_allowed_user_ids()


def is_user_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def parse_video_command_args(raw_text: str, *, allow_empty_prompt: bool = False) -> dict:
    parts = shlex.split(raw_text)

    duration = DEFAULT_DURATION
    ratio = DEFAULT_RATIO
    resolution = DEFAULT_RESOLUTION
    count = DEFAULT_VIDEO_COUNT
    rewrite_mode = VIDEO_REWRITE_MODE
    prompt_parts = []

    i = 0
    while i < len(parts):
        part = parts[i]

        if part in ("-d", "--duration"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 duration 值")
            duration = int(parts[i])
        elif part in ("-r", "--ratio"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 ratio 值")
            ratio = parts[i]
        elif part in ("-q", "--resolution"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 resolution 值")
            resolution = parts[i]
        elif part in ("-n", "--count"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 count 值")
            count = int(parts[i])
        elif part == "--safe-rewrite":
            rewrite_mode = "mild"
        elif part == "--safe-rewrite-mild":
            rewrite_mode = "mild"
        elif part == "--safe-rewrite-strong":
            rewrite_mode = "strong"
        elif part == "--no-safe-rewrite":
            rewrite_mode = "off"
        else:
            prompt_parts.append(part)

        i += 1

    prompt = " ".join(prompt_parts).strip()
    if not prompt and not allow_empty_prompt:
        raise ValueError("提示词不能为空")
    if duration < 5 or duration > 30:
        raise ValueError("duration 仅支持 5 到 30 秒")
    if ratio not in ALLOWED_VIDEO_RATIOS:
        raise ValueError(f"ratio 仅支持: {', '.join(sorted(ALLOWED_VIDEO_RATIOS))}")
    if resolution not in ALLOWED_RESOLUTIONS:
        raise ValueError(f"resolution 仅支持: {', '.join(sorted(ALLOWED_RESOLUTIONS))}")
    if count < 1 or count > MAX_VIDEO_COUNT:
        raise ValueError(f"count 仅支持 1 到 {MAX_VIDEO_COUNT}")

    return {
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": ratio,
        "resolution": resolution,
        "count": count,
        "rewrite_mode": rewrite_mode,
    }


def is_text_document(document: Document) -> bool:
    file_name = (document.file_name or "").lower()
    suffix = Path(file_name).suffix.lower()
    mime_type = (document.mime_type or "").lower()
    if suffix in TEXT_ATTACHMENT_SUFFIXES:
        return True
    if any(mime_type.startswith(prefix) for prefix in TEXT_ATTACHMENT_MIME_PREFIXES):
        return True
    return mime_type in TEXT_ATTACHMENT_MIME_ALLOWLIST


def decode_text_attachment(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("附件不是可识别的文本编码，请使用 UTF-8 / UTF-16 / GB18030 文本文件")


async def load_text_document(document: Document) -> str:
    if not is_text_document(document):
        raise ValueError("仅支持文本类附件：txt / md / csv / tsv / json / yaml")
    if document.file_size and document.file_size > MAX_TEXT_ATTACHMENT_BYTES:
        raise ValueError(f"文本附件过大，当前仅支持不超过 {MAX_TEXT_ATTACHMENT_BYTES // 1024}KB 的文件")

    tg_file = await document.get_file()
    suffix = Path(document.file_name or "script.txt").suffix or ".txt"
    fd, temp_path = tempfile.mkstemp(prefix="tg_prompt_", suffix=suffix)
    os.close(fd)
    try:
        await tg_file.download_to_drive(custom_path=temp_path)
        raw_bytes = Path(temp_path).read_bytes()
    finally:
        cleanup_paths(temp_path)

    text = decode_text_attachment(raw_bytes).strip()
    if not text:
        raise ValueError("文本附件内容为空")
    return text


async def load_video_prompt_from_reply(message: Message) -> tuple[str, list[str]]:
    reply = message.reply_to_message
    if not reply:
        return "", []

    parts: list[str] = []
    sources: list[str] = []

    reply_text = (reply.text or reply.caption or "").strip()
    if reply_text:
        parts.append(reply_text)
        sources.append("reply_text")

    if reply.document:
        attachment_text = await load_text_document(reply.document)
        parts.append(attachment_text)
        sources.append(f"attachment:{reply.document.file_name or 'unnamed'}")

    return "\n\n".join(part for part in parts if part).strip(), sources


def merge_video_prompt(inline_prompt: str, reply_prompt: str) -> str:
    inline_prompt = inline_prompt.strip()
    reply_prompt = reply_prompt.strip()
    if inline_prompt and reply_prompt:
        return f"{inline_prompt}\n\n以下是补充分镜/脚本，请一并吸收：\n{reply_prompt}".strip()
    return inline_prompt or reply_prompt


def parse_image_command_args(raw_text: str) -> dict:
    parts = shlex.split(raw_text)

    ratio = DEFAULT_IMAGE_RATIO
    model = DEFAULT_IMAGE_MODEL
    count = DEFAULT_IMAGE_COUNT
    prompt_parts = []

    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ("-r", "--ratio"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 ratio 值")
            ratio = parts[i]
        elif part in ("-n", "--count"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 count 值")
            count = int(parts[i])
        elif part in ("-m", "--model"):
            i += 1
            if i >= len(parts):
                raise ValueError("缺少 model 值")
            model = parts[i]
        else:
            prompt_parts.append(part)
        i += 1

    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        raise ValueError("提示词不能为空")
    if ratio not in ALLOWED_IMAGE_RATIOS:
        raise ValueError(f"ratio 仅支持: {', '.join(sorted(ALLOWED_IMAGE_RATIOS))}")
    if model not in ALLOWED_IMAGE_MODELS:
        raise ValueError(f"model 仅支持: {', '.join(sorted(ALLOWED_IMAGE_MODELS))}")
    if count < 1 or count > MAX_IMAGE_COUNT:
        raise ValueError(f"count 仅支持 1 到 {MAX_IMAGE_COUNT}")

    return {
        "prompt": prompt,
        "aspect_ratio": ratio,
        "model": model,
        "count": count,
    }


def file_to_data_url(file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    with open(file_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def rewrite_prompt_for_moderation(prompt: str, mode: str = "mild") -> str:
    cleaned = " ".join(prompt.split())
    mild_replacements = {
        "nude": "fully clothed",
        "naked": "fully clothed",
        "sexy": "elegant",
        "sensual": "graceful",
        "lingerie": "fashion outfit",
        "bikini": "summer outfit",
        "underwear": "casual clothing",
        "kiss": "look at each other",
        "blood": "dramatic lighting",
        "violent": "intense",
        "weapon": "prop",
        "fight": "face off",
    }
    strong_replacements = {
        **mild_replacements,
        "bed": "indoor scene",
        "breast": "upper body",
        "butt": "back view",
        "thigh": "leg",
        "gun": "object",
        "kill": "confront",
        "dominate": "approach",
        "seduce": "greet",
        "provocative": "stylish",
        "intimate": "friendly",
        "adult": "general audience",
    }
    replacements = strong_replacements if mode == "strong" else mild_replacements

    lowered = cleaned.lower()
    for src, dst in replacements.items():
        lowered = lowered.replace(src, dst)

    if mode == "strong":
        return (
            "Create a safe, policy-compliant, general-audience cinematic video prompt. "
            "Make the scene fully clothed, non-violent, non-romantic, non-sensitive, and suitable for all audiences. "
            "Keep only the broad setting, camera movement, mood, and action, while removing any sexualized, graphic, suggestive, risky, or sensitive elements. "
            f"Sanitized prompt: {lowered}"
        )

    return (
        "Create a safe, policy-compliant cinematic video prompt. "
        "Keep it non-explicit, fully clothed, non-violent, non-sensitive, and suitable for general audiences. "
        "Preserve the broad scene, camera language, lighting, and motion, but remove any sexualized, graphic, illegal, or sensitive content. "
        f"Rewritten prompt: {lowered}"
    )


def compress_image_for_img2video(
    input_path: str,
    max_side: int = 1600,
    jpeg_quality: int = 88,
    max_bytes: int = 2 * 1024 * 1024,
) -> str:
    img = Image.open(input_path)
    img = ImageOps.exif_transpose(img)

    if img.mode != "RGB":
        img = img.convert("RGB")

    width, height = img.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / float(longest)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    fd, output_path = tempfile.mkstemp(prefix="tg_input_compressed_", suffix=".jpg")
    os.close(fd)

    quality = jpeg_quality
    current = img

    while True:
        current.save(output_path, format="JPEG", quality=quality, optimize=True)
        file_size = os.path.getsize(output_path)
        if file_size <= max_bytes:
            return output_path

        width, height = current.size
        if width <= 768 or height <= 768:
            if quality <= 55:
                return output_path
            quality -= 8
            continue

        new_size = (max(1, int(width * 0.85)), max(1, int(height * 0.85)))
        current = current.resize(new_size, Image.LANCZOS)
        if quality > 60:
            quality -= 5


def make_headers(api_key: str, include_json: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


def rotate_start_index() -> int:
    if not XAI_API_KEYS:
        return 0
    return int(time.time()) % len(XAI_API_KEYS)


def is_failover_status(status_code: int) -> bool:
    return status_code in XAI_FAILOVER_STATUS_CODES


def xai_request(method: str, url: str, *, json: dict | None = None, timeout: int = XAI_REQUEST_TIMEOUT, preferred_key_index: int | None = None) -> tuple[requests.Response, int]:
    errors = []
    total = len(XAI_API_KEYS)
    start = preferred_key_index if preferred_key_index is not None else rotate_start_index()

    for offset in range(total):
        key_index = (start + offset) % total
        api_key = XAI_API_KEYS[key_index]
        try:
            resp = requests.request(
                method,
                url,
                headers=make_headers(api_key, include_json=json is not None),
                json=json,
                timeout=timeout,
            )
            if resp.ok:
                return resp, key_index

            body_preview = resp.text[:1000]
            error_msg = f"key#{key_index} status={resp.status_code} body={body_preview}"

            if is_failover_status(resp.status_code) and offset < total - 1:
                logger.warning(
                    "xAI request failed on key #%s status=%s, switching to next key",
                    key_index,
                    resp.status_code,
                )
                errors.append(error_msg)
                continue

            if 400 <= resp.status_code < 500 and resp.status_code not in XAI_FAILOVER_STATUS_CODES:
                raise RuntimeError(
                    "xAI 请求被拒绝（更像是请求参数/模型/提示词问题，不是 key 失效）: "
                    + error_msg
                )

            resp.raise_for_status()
        except requests.RequestException as e:
            if offset < total - 1:
                logger.warning("xAI request exception on key #%s: %s, switching to next key", key_index, e)
                errors.append(f"key#{key_index} exception={e}")
                continue
            errors.append(f"key#{key_index} exception={e}")

    raise RuntimeError("所有 XAI_API_KEY 都不可用: " + " | ".join(errors))


def submit_video(prompt: str, duration: int, aspect_ratio: str, resolution: str) -> tuple[str, int]:
    payload = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    resp, key_index = xai_request("POST", XAI_VIDEO_CREATE_URL, json=payload, timeout=XAI_REQUEST_TIMEOUT)
    data = resp.json()
    request_id = data.get("request_id")
    if not request_id:
        raise RuntimeError(f"上游返回里没有 request_id: {data}")
    return request_id, key_index


def submit_image_to_video(
    prompt: str,
    image_data_url: str,
    duration: int,
    aspect_ratio: str,
    resolution: str,
) -> tuple[str, int]:
    payload = {
        "model": "grok-imagine-video",
        "prompt": prompt,
        "image": {"url": image_data_url},
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    resp, key_index = xai_request("POST", XAI_VIDEO_CREATE_URL, json=payload, timeout=XAI_REQUEST_TIMEOUT)
    data = resp.json()

    # 更智能地提取 request_id，兼容不同返回结构
    request_id = None
    for key in ("request_id", "id"):
        if key in data:
            request_id = data.get(key)
            break
        if "data" in data and isinstance(data["data"], dict):
            if key in data["data"]:
                request_id = data["data"].get(key)
                break

    if not request_id:
        logger.error("图生视频生成失败，xAI 返回内容：%s", data)
        raise RuntimeError(f"未能从 xAI 响应中获取有效的 request_id: {data}")

    logger.info("图生视频提交成功，request_id=%s", request_id)
    return request_id, key_index


def submit_image(
    prompt: str,
    aspect_ratio: str = DEFAULT_IMAGE_RATIO,
    model: str = DEFAULT_IMAGE_MODEL,
    count: int = DEFAULT_IMAGE_COUNT,
) -> list[str]:
    payload = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "n": count,
    }

    resp, _ = xai_request("POST", XAI_IMAGE_CREATE_URL, json=payload, timeout=XAI_REQUEST_TIMEOUT)
    data = resp.json()

    urls: list[str] = []
    if "data" in data and isinstance(data["data"], list):
        for item in data["data"]:
            if isinstance(item, dict) and item.get("url"):
                urls.append(item["url"])

    if not urls and data.get("url"):
        urls.append(data["url"])

    if urls:
        return urls

    raise RuntimeError(f"图片接口未返回可用 URL: {data}")


async def poll_video_result(request_id: str, preferred_key_index: int | None = None) -> dict:
    started = time.time()

    while True:
        try:
            resp, key_index = await asyncio.to_thread(
                xai_request,
                "GET",
                XAI_VIDEO_STATUS_URL.format(request_id=request_id),
                timeout=XAI_POLL_TIMEOUT,
                preferred_key_index=preferred_key_index,
            )
            data = resp.json()
            preferred_key_index = key_index
        except RuntimeError as e:
            if "Malformed request id" in str(e):
                logger.error("xAI 图生视频状态查询失败，request_id 不被接受。完整错误: %s", e)
                raise RuntimeError("图生视频服务暂时异常（Malformed request id），请稍后重试或改用普通 /video 命令生成视频") from e
            raise

        status = data.get("status")
        if status == "done":
            logger.info("视频生成完成，request_id=%s", request_id)
            return data
        if status in {"failed", "expired"}:
            logger.error("视频生成失败，status=%s，完整响应=%s", status, data)
            raise RuntimeError(f"任务失败，status={status}，响应={data}")
        if time.time() - started > MAX_WAIT_SECONDS:
            raise TimeoutError(f"等待超时，request_id={request_id}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def download_binary(url: str, suffix: str) -> str:
    resp = requests.get(url, timeout=300, stream=True)
    resp.raise_for_status()

    fd, temp_path = tempfile.mkstemp(prefix="xai_asset_", suffix=suffix)
    os.close(fd)

    with open(temp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    return temp_path


async def download_telegram_photo(update: Update) -> str:
    reply = update.message.reply_to_message
    if not reply or not reply.photo:
        raise ValueError("请用 /img2video 回复一张图片")

    photo = reply.photo[-1]
    tg_file = await photo.get_file()

    fd, temp_path = tempfile.mkstemp(prefix="tg_input_", suffix=".jpg")
    os.close(fd)

    await tg_file.download_to_drive(custom_path=temp_path)
    return temp_path


async def cache_img2video_source(update: Update) -> str:
    raw_input_image_path = None
    compressed_image_path = None
    try:
        raw_input_image_path = await download_telegram_photo(update)
        compressed_image_path = compress_image_for_img2video(raw_input_image_path)

        logger.info(
            "img2video source cached: raw=%s bytes, compressed=%s bytes",
            os.path.getsize(raw_input_image_path),
            os.path.getsize(compressed_image_path),
        )
        return compressed_image_path
    finally:
        cleanup_paths(raw_input_image_path)


def cleanup_paths(*paths: str | None) -> None:
    for temp_path in paths:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                logger.warning("failed to delete temp file: %s", temp_path)


def write_bot_status(**fields) -> None:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        current = {}
        if STATUS_FILE.exists():
            current = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        current.update(fields)
        current["updated_at"] = int(time.time())
        STATUS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("failed to write bot status: %s", e)


def clear_reload_signal() -> None:
    try:
        RELOAD_SIGNAL_FILE.unlink(missing_ok=True)
    except Exception:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "命令说明：\n"
        "/image [-r ratio] [-n count] [-m model] prompt\n"
        "/image4 prompt  （快捷 4 张图）\n"
        "/imagepro prompt  （快捷 Pro 图像模型）\n"
        "/models 查看当前默认图片配置\n"
        "/video [-d seconds] [-r ratio] [-q resolution] [-n count] prompt\n"
        "/video 也支持回复文字或文本附件发送\n"
        "/img2video [-d seconds] [-r ratio] [-q resolution] [-n count] prompt，需回复一张图片\n"
        "/me 查看你的 Telegram user id\n"
        "/help 查看帮助"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "用法：\n"
        "/image [-r ratio] [-n count] [-m model] prompt\n"
        "/image4 prompt  （快捷生成 4 张）\n"
        "/imagepro prompt  （快捷使用 Pro 模型）\n"
        "/models 查看当前默认图片配置\n"
        "/video [-d seconds] [-r ratio] [-q resolution] [-n count] prompt\n"
        "/video 也支持回复文字或文本附件（txt/md/csv/tsv/json/yaml）后发送\n"
        "/img2video [-d seconds] [-r ratio] [-q resolution] [-n count] prompt，需回复一张图片\n\n"
        "图片比例：auto / 16:9 / 9:16 / 1:1 / 3:2 / 2:3 / 4:3 / 3:4 / 4:5 / 5:4 / 2:1 / 1:2 / 19.5:9 / 9:19.5 / 20:9 / 9:20\n"
        "图片模型：grok-imagine-image / grok-imagine-image-pro\n"
        f"图片数量：1 到 {MAX_IMAGE_COUNT}\n"
        f"当前默认：model={DEFAULT_IMAGE_MODEL}, ratio={DEFAULT_IMAGE_RATIO}, count={DEFAULT_IMAGE_COUNT}\n"
        "视频比例：16:9 / 9:16 / 1:1 / 3:2 / 2:3\n"
        "视频分辨率：480p / 720p\n"
        f"视频数量：1 到 {MAX_VIDEO_COUNT}\n"
        f"当前默认视频数量：count={DEFAULT_VIDEO_COUNT}\n"
        f"文本附件大小限制：不超过 {MAX_TEXT_ATTACHMENT_BYTES // 1024}KB\n\n"
        "示例：\n"
        "/image -r 1:1 A cute robot in a retro arcade\n"
        "/image4 A cute robot in a retro arcade\n"
        "/imagepro A luxury perfume bottle on black silk\n"
        "/image -n 4 -m grok-imagine-image-pro A luxury perfume bottle on black silk\n"
        "/models\n"
        "/video -d 12 -r 9:16 -q 720p -n 2 A cyberpunk girl walking in neon rain\n"
        "回复一段分镜文字后发送：/video -d 12 -r 16:9 -n 3\n"
        "回复一个 script.md / storyboard.csv 后发送：/video -d 12 -r 16:9 -n 2\n"
        "/img2video -d 8 -r 9:16 -n 2 A cinematic close-up, she blinks and smiles gently"
    )


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.username:
        await update.message.reply_text(f"user_id: {user.id}\nusername: @{user.username}")
    else:
        await update.message.reply_text(f"user_id: {user.id}")


def get_queue_size() -> int:
    return 0 if JOB_QUEUE is None else JOB_QUEUE.qsize()


async def send_progress_message(app: Application, job: Job, text: str):
    logger.info("progress | job=%s | chat_id=%s | %s", job.kind, job.chat_id, text.replace("\n", " | "))
    sent = await app.bot.send_message(job.chat_id, text)
    if sent and getattr(sent, "message_id", None):
        job.progress_message_ids.append(sent.message_id)
    return sent


async def cleanup_progress_messages(app: Application, job: Job) -> None:
    for message_id in job.progress_message_ids:
        try:
            await app.bot.delete_message(chat_id=job.chat_id, message_id=message_id)
        except Exception:
            pass
    job.progress_message_ids.clear()


async def enqueue_job(job: Job, message, queued_text: str) -> None:
    if JOB_QUEUE is None:
        raise RuntimeError("任务队列尚未初始化")

    await JOB_QUEUE.put(job)
    logger.info("enqueue | job=%s | chat_id=%s | queue_size=%s", job.kind, job.chat_id, get_queue_size())
    sent = await message.reply_text(f"{queued_text}\n当前排队数：{get_queue_size()}")
    if sent and getattr(sent, "message_id", None):
        job.progress_message_ids.append(sent.message_id)


async def queue_image_job(update: Update, raw_text: str, *, force_count: int | None = None, force_model: str | None = None, usage_text: str = "用法：/image [-r ratio] [-n count] [-m model] 你的提示词") -> None:
    user = update.effective_user
    message = update.message

    if not is_user_allowed(user.id):
        await message.reply_text("你没有使用权限")
        return

    raw_text = raw_text.strip()
    if not raw_text:
        await message.reply_text(usage_text)
        return

    try:
        params = parse_image_command_args(raw_text)
        if force_count is not None:
            params["count"] = force_count
        if force_model is not None:
            params["model"] = force_model

        await enqueue_job(
            Job(
                kind="image",
                chat_id=message.chat_id,
                user_id=user.id,
                prompt=params["prompt"],
                aspect_ratio=params["aspect_ratio"],
                image_model=params["model"],
                image_count=params["count"],
            ),
            message,
            (
                "图片任务已加入队列\n"
                f"ratio={params['aspect_ratio']}, count={params['count']}, model={params['model']}"
            ),
        )
    except Exception as e:
        await message.reply_text(f"图片任务参数错误：{e}")


async def image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    raw_text = message.text.removeprefix("/image").strip()
    await queue_image_job(update, raw_text)


async def image4(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    raw_text = message.text.removeprefix("/image4").strip()
    await queue_image_job(
        update,
        raw_text,
        force_count=min(4, MAX_IMAGE_COUNT),
        usage_text="用法：/image4 你的提示词",
    )


async def imagepro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    raw_text = message.text.removeprefix("/imagepro").strip()
    await queue_image_job(
        update,
        raw_text,
        force_model="grok-imagine-image-pro",
        usage_text="用法：/imagepro 你的提示词",
    )


async def models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "当前图片配置：\n"
        f"default_model={DEFAULT_IMAGE_MODEL}\n"
        f"default_ratio={DEFAULT_IMAGE_RATIO}\n"
        f"default_count={DEFAULT_IMAGE_COUNT}\n"
        f"max_count={MAX_IMAGE_COUNT}\n"
        f"default_video_count={DEFAULT_VIDEO_COUNT}\n"
        f"max_video_count={MAX_VIDEO_COUNT}\n"
        "supported_models=grok-imagine-image, grok-imagine-image-pro"
    )


async def video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message

    if not is_user_allowed(user.id):
        await message.reply_text("你没有使用权限")
        return

    raw_text = message.text.removeprefix("/video").strip()
    if not raw_text and not message.reply_to_message:
        await message.reply_text(
            "用法：/video [-d seconds] [-r ratio] [-q resolution] [-n count] 你的提示词\n"
            "也支持回复一段文字或一个文本附件（txt/md/csv/tsv/json/yaml）后再发送 /video 参数"
        )
        return

    try:
        params = parse_video_command_args(raw_text, allow_empty_prompt=True)
        reply_prompt, prompt_sources = await load_video_prompt_from_reply(message)
        merged_prompt = merge_video_prompt(params["prompt"], reply_prompt)
        if not merged_prompt:
            raise ValueError("提示词不能为空；可直接写在 /video 后面，或回复文字/文本附件后再发送 /video")

        await enqueue_job(
            Job(
                kind="video",
                chat_id=message.chat_id,
                user_id=user.id,
                prompt=merged_prompt,
                duration=params["duration"],
                aspect_ratio=params["aspect_ratio"],
                resolution=params["resolution"],
                rewrite_mode=params["rewrite_mode"],
                video_count=params["count"],
            ),
            message,
            (
                "视频任务已加入队列\n"
                f"duration={params['duration']}, ratio={params['aspect_ratio']}, resolution={params['resolution']}, count={params['count']}"
                + (f"\nprompt_sources={','.join(prompt_sources)}" if prompt_sources else "")
            ),
        )
    except Exception as e:
        await message.reply_text(f"视频任务参数错误：{e}")


async def img2video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message

    if not is_user_allowed(user.id):
        await message.reply_text("你没有使用权限")
        return

    raw_text = message.text.removeprefix("/img2video").strip()
    if not raw_text:
        await message.reply_text(
            "用法：回复一张图片后发送\n"
            "/img2video [-d seconds] [-r ratio] [-q resolution] [-n count] 让这张图动起来"
        )
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply_text("请用 /img2video 回复一张图片")
        return

    cached_image_path = None
    try:
        params = parse_video_command_args(raw_text)
        cache_notice = await message.reply_text("正在缓存原图到本地，稍后加入队列...")
        if cache_notice and getattr(cache_notice, "message_id", None):
            temp_job = Job(kind="img2video", chat_id=message.chat_id, user_id=user.id, prompt=params["prompt"])
            temp_job.progress_message_ids.append(cache_notice.message_id)
        else:
            temp_job = Job(kind="img2video", chat_id=message.chat_id, user_id=user.id, prompt=params["prompt"])
        cached_image_path = await cache_img2video_source(update)

        temp_job.duration = params["duration"]
        temp_job.aspect_ratio = params["aspect_ratio"]
        temp_job.resolution = params["resolution"]
        temp_job.cached_image_path = cached_image_path
        temp_job.rewrite_mode = params["rewrite_mode"]
        temp_job.video_count = params["count"]
        await enqueue_job(
            temp_job,
            message,
            (
                "图生视频任务已加入队列，原图已缓存到本地\n"
                f"duration={params['duration']}, ratio={params['aspect_ratio']}, resolution={params['resolution']}, count={params['count']}"
            ),
        )
        cached_image_path = None
    except Exception as e:
        cleanup_paths(cached_image_path)
        await message.reply_text(f"图生视频任务参数错误：{e}")


async def process_image_job(app: Application, job: Job) -> None:
    image_paths: list[str] = []
    file_handles = []
    try:
        ratio = job.aspect_ratio or DEFAULT_IMAGE_RATIO
        model = job.image_model or DEFAULT_IMAGE_MODEL
        count = job.image_count or DEFAULT_IMAGE_COUNT

        await send_progress_message(
            app,
            job,
            f"开始生成图片，ratio={ratio}, count={count}, model={model}",
        )

        effective_prompt = job.prompt
        try:
            image_urls = submit_image(effective_prompt, ratio, model, count)
        except RuntimeError as e:
            if job.rewrite_mode != "off" and "content moderation" in str(e).lower():
                effective_prompt = rewrite_prompt_for_moderation(job.prompt, job.rewrite_mode)
                await send_progress_message(app, job, f"检测到图片审核拒绝，正在自动改写为更安全的提示词后重试一次...（mode={job.rewrite_mode}）")
                try:
                    image_urls = submit_image(effective_prompt, ratio, model, count)
                except RuntimeError as retry_error:
                    if "content moderation" in str(retry_error).lower():
                        raise RuntimeError("图片生成连续两次被审核拒绝。请把提示词改得更日常、更中性，减少性感/暴力/敏感描述后再试。") from retry_error
                    raise
            else:
                raise

        for index, image_url in enumerate(image_urls, 1):
            image_path = download_binary(image_url, f"_{index}.png")
            image_paths.append(image_path)

        caption = (
            "图片生成完成\n"
            f"ratio={ratio}, count={len(image_paths)}, model={model}"
        )

        if len(image_paths) == 1:
            with open(image_paths[0], "rb") as f:
                await app.bot.send_photo(
                    chat_id=job.chat_id,
                    photo=f,
                    caption=caption,
                    read_timeout=180,
                    write_timeout=180,
                )
            await cleanup_progress_messages(app, job)
            return

        media = []
        for index, path in enumerate(image_paths):
            handle = open(path, "rb")
            file_handles.append(handle)
            media.append(
                InputMediaPhoto(
                    media=handle,
                    caption=caption if index == 0 else None,
                )
            )

        await app.bot.send_media_group(
            chat_id=job.chat_id,
            media=media,
            read_timeout=180,
            write_timeout=180,
        )
        await cleanup_progress_messages(app, job)
    finally:
        for handle in file_handles:
            try:
                handle.close()
            except Exception:
                pass
        cleanup_paths(*image_paths)


async def process_video_job(app: Application, job: Job) -> None:
    output_video_paths: list[str] = []
    try:
        total_count = job.video_count or DEFAULT_VIDEO_COUNT
        await send_progress_message(
            app,
            job,
            "任务开始执行。\n"
            f"duration={job.duration}, ratio={job.aspect_ratio}, resolution={job.resolution}, count={total_count}",
        )

        for current_index in range(1, total_count + 1):
            effective_prompt = job.prompt
            try:
                request_id, preferred_key_index = submit_video(
                    prompt=effective_prompt,
                    duration=job.duration or DEFAULT_DURATION,
                    aspect_ratio=job.aspect_ratio or DEFAULT_RATIO,
                    resolution=job.resolution or DEFAULT_RESOLUTION,
                )

                await send_progress_message(app, job, f"任务已提交（{current_index}/{total_count}）\nrequest_id={request_id}\n开始轮询生成结果，请稍等。")

                result = await poll_video_result(request_id, preferred_key_index=preferred_key_index)
            except RuntimeError as e:
                if job.rewrite_mode != "off" and "content moderation" in str(e).lower():
                    effective_prompt = rewrite_prompt_for_moderation(job.prompt, job.rewrite_mode)
                    await send_progress_message(app, job, f"检测到审核拒绝，正在自动改写为更安全的提示词后重试一次...（mode={job.rewrite_mode}，{current_index}/{total_count}）")
                    try:
                        request_id, preferred_key_index = submit_video(
                            prompt=effective_prompt,
                            duration=job.duration or DEFAULT_DURATION,
                            aspect_ratio=job.aspect_ratio or DEFAULT_RATIO,
                            resolution=job.resolution or DEFAULT_RESOLUTION,
                        )
                        await send_progress_message(app, job, f"重试任务已提交（{current_index}/{total_count}）\nrequest_id={request_id}\n开始轮询生成结果，请稍等。")
                        result = await poll_video_result(request_id, preferred_key_index=preferred_key_index)
                    except RuntimeError as retry_error:
                        if "content moderation" in str(retry_error).lower():
                            raise RuntimeError("视频生成连续两次被审核拒绝。请把提示词改得更日常、更中性，减少性感/暴力/敏感描述后再试。") from retry_error
                        raise
                else:
                    raise
            video_info = result.get("video", {})
            video_url = video_info.get("url")
            if not video_url:
                raise RuntimeError(f"任务完成，但没拿到视频 URL: {result}")

            await send_progress_message(app, job, f"视频已生成，开始下载并回传 Telegram...（{current_index}/{total_count}）")
            output_video_path = download_binary(video_url, f"_{current_index}.mp4")
            output_video_paths.append(output_video_path)

            with open(output_video_path, "rb") as f:
                await app.bot.send_video(
                    chat_id=job.chat_id,
                    video=f,
                    caption=(
                        "视频生成完成\n"
                        f"progress={current_index}/{total_count}\n"
                        f"request_id={request_id}\n"
                        f"duration={job.duration}, ratio={job.aspect_ratio}, resolution={job.resolution}"
                    ),
                    read_timeout=300,
                    write_timeout=300,
                )
        await cleanup_progress_messages(app, job)
    finally:
        cleanup_paths(*output_video_paths)


async def process_img2video_job(app: Application, job: Job) -> None:
    input_image_path = job.cached_image_path
    output_video_paths: list[str] = []
    try:
        if not input_image_path or not Path(input_image_path).exists():
            raise RuntimeError("本地图生视频缓存图不存在，无法执行任务")

        total_count = job.video_count or DEFAULT_VIDEO_COUNT
        await send_progress_message(app, job, "图生视频任务开始执行，使用本地缓存原图...")
        image_data_url = file_to_data_url(input_image_path)

        await send_progress_message(
            app,
            job,
            "图片已就绪，开始提交图生视频任务。\n"
            f"duration={job.duration}, ratio={job.aspect_ratio}, resolution={job.resolution}, count={total_count}",
        )

        for current_index in range(1, total_count + 1):
            effective_prompt = job.prompt
            try:
                request_id, preferred_key_index = submit_image_to_video(
                    prompt=effective_prompt,
                    image_data_url=image_data_url,
                    duration=job.duration or DEFAULT_DURATION,
                    aspect_ratio=job.aspect_ratio or DEFAULT_RATIO,
                    resolution=job.resolution or DEFAULT_RESOLUTION,
                )

                await send_progress_message(app, job, f"任务已提交（{current_index}/{total_count}）\nrequest_id={request_id}\n开始轮询生成结果，请稍等。")

                result = await poll_video_result(request_id, preferred_key_index=preferred_key_index)
            except RuntimeError as e:
                if job.rewrite_mode != "off" and "content moderation" in str(e).lower():
                    effective_prompt = rewrite_prompt_for_moderation(job.prompt, job.rewrite_mode)
                    await send_progress_message(app, job, f"检测到审核拒绝，正在自动改写为更安全的提示词后重试一次...（mode={job.rewrite_mode}，{current_index}/{total_count}）")
                    try:
                        request_id, preferred_key_index = submit_image_to_video(
                            prompt=effective_prompt,
                            image_data_url=image_data_url,
                            duration=job.duration or DEFAULT_DURATION,
                            aspect_ratio=job.aspect_ratio or DEFAULT_RATIO,
                            resolution=job.resolution or DEFAULT_RESOLUTION,
                        )
                        await send_progress_message(app, job, f"重试任务已提交（{current_index}/{total_count}）\nrequest_id={request_id}\n开始轮询生成结果，请稍等。")
                        result = await poll_video_result(request_id, preferred_key_index=preferred_key_index)
                    except RuntimeError as retry_error:
                        if "content moderation" in str(retry_error).lower():
                            raise RuntimeError("图生视频连续两次被审核拒绝。请把提示词改得更日常、更中性，减少性感/暴力/敏感描述后再试；如果仍失败，一个可能原因是原图本身触发了审核。") from retry_error
                        raise
                else:
                    raise
            video_info = result.get("video", {})
            video_url = video_info.get("url")
            if not video_url:
                raise RuntimeError(f"任务完成，但没拿到视频 URL: {result}")

            await send_progress_message(app, job, f"视频已生成，开始下载并回传 Telegram...（{current_index}/{total_count}）")
            output_video_path = download_binary(video_url, f"_{current_index}.mp4")
            output_video_paths.append(output_video_path)

            with open(output_video_path, "rb") as f:
                await app.bot.send_video(
                    chat_id=job.chat_id,
                    video=f,
                    caption=(
                        "图生视频完成\n"
                        f"progress={current_index}/{total_count}\n"
                        f"request_id={request_id}\n"
                        f"duration={job.duration}, ratio={job.aspect_ratio}, resolution={job.resolution}"
                    ),
                    read_timeout=300,
                    write_timeout=300,
                )
        await cleanup_progress_messages(app, job)
    finally:
        cleanup_paths(input_image_path, *output_video_paths)


async def job_worker(app: Application) -> None:
    if JOB_QUEUE is None:
        raise RuntimeError("任务队列尚未初始化")

    try:
        while True:
            job = await JOB_QUEUE.get()
            write_bot_status(state="busy", current_job=job.kind, queue_size=JOB_QUEUE.qsize())
            try:
                if job.kind == "image":
                    await process_image_job(app, job)
                elif job.kind == "video":
                    await process_video_job(app, job)
                elif job.kind == "img2video":
                    await process_img2video_job(app, job)
                else:
                    raise RuntimeError(f"未知任务类型: {job.kind}")
                write_bot_status(state="idle", last_job=job.kind, last_result="success", queue_size=JOB_QUEUE.qsize())
            except Exception as e:
                logger.exception("job failed: %s", job.kind)
                await cleanup_progress_messages(app, job)
                await app.bot.send_message(job.chat_id, f"任务执行失败：{e}")
                write_bot_status(state="idle", last_job=job.kind, last_result=f"error: {e}", queue_size=JOB_QUEUE.qsize())
            finally:
                JOB_QUEUE.task_done()
    except asyncio.CancelledError:
        logger.info("job worker cancelled")
        write_bot_status(state="stopped", current_job=None)
        raise


async def watch_reload_signal() -> None:
    try:
        while True:
            if RELOAD_SIGNAL_FILE.exists():
                logger.info("reload signal detected, exiting bot process for container restart")
                clear_reload_signal()
                write_bot_status(state="reloading")
                os.kill(os.getpid(), signal.SIGTERM)
                return
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        raise


async def post_init(app: Application) -> None:
    global JOB_QUEUE, JOB_WORKER_TASK, RELOAD_WATCHER_TASK
    JOB_QUEUE = asyncio.Queue()
    clear_reload_signal()
    JOB_WORKER_TASK = asyncio.create_task(job_worker(app))
    RELOAD_WATCHER_TASK = asyncio.create_task(watch_reload_signal())

    bot_info = None
    try:
        bot_info = await app.bot.get_me()
    except Exception as e:
        logger.warning("bot started, but getMe failed during startup: %s", e)

    logger.info(
        "bot started | mode=polling | image_model=%s | image_ratio=%s | image_count=%s | video_count=%s | video_rewrite_mode=%s | allowed_users=%s",
        DEFAULT_IMAGE_MODEL,
        DEFAULT_IMAGE_RATIO,
        DEFAULT_IMAGE_COUNT,
        DEFAULT_VIDEO_COUNT,
        VIDEO_REWRITE_MODE,
        "all" if not ALLOWED_USER_IDS else ",".join(str(x) for x in sorted(ALLOWED_USER_IDS)),
    )
    if bot_info is not None:
        logger.info(
            "bot identity | id=%s | username=@%s | name=%s",
            bot_info.id,
            bot_info.username,
            bot_info.full_name,
        )
        write_bot_status(
            state="idle",
            bot_id=bot_info.id,
            bot_username=bot_info.username,
            bot_name=bot_info.full_name,
            image_model=DEFAULT_IMAGE_MODEL,
            image_ratio=DEFAULT_IMAGE_RATIO,
            image_count=DEFAULT_IMAGE_COUNT,
            video_count=DEFAULT_VIDEO_COUNT,
            video_rewrite_mode=VIDEO_REWRITE_MODE,
            queue_size=0,
        )
    else:
        write_bot_status(
            state="idle",
            image_model=DEFAULT_IMAGE_MODEL,
            image_ratio=DEFAULT_IMAGE_RATIO,
            image_count=DEFAULT_IMAGE_COUNT,
            video_count=DEFAULT_VIDEO_COUNT,
            video_rewrite_mode=VIDEO_REWRITE_MODE,
            queue_size=0,
        )


async def post_shutdown(app: Application) -> None:
    global JOB_WORKER_TASK, RELOAD_WATCHER_TASK
    write_bot_status(state="stopping")
    if RELOAD_WATCHER_TASK is not None:
        RELOAD_WATCHER_TASK.cancel()
        try:
            await RELOAD_WATCHER_TASK
        except asyncio.CancelledError:
            pass
        RELOAD_WATCHER_TASK = None
    if JOB_WORKER_TASK is not None:
        JOB_WORKER_TASK.cancel()
        try:
            await JOB_WORKER_TASK
        except asyncio.CancelledError:
            pass
        JOB_WORKER_TASK = None
    write_bot_status(state="stopped")


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("models", models))
    app.add_handler(CommandHandler("image", image))
    app.add_handler(CommandHandler("image4", image4))
    app.add_handler(CommandHandler("imagepro", imagepro))
    app.add_handler(CommandHandler("video", video))
    app.add_handler(CommandHandler("img2video", img2video))
    return app


def main() -> None:
    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
