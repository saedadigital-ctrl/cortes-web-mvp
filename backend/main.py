"""FastAPI application exposing endpoints for job submissions."""

import json
import logging
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

import ffmpeg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL

import whisper

logger = logging.getLogger(__name__)

app = FastAPI()


class JobCreateRequest(BaseModel):
    """Payload for creating a new job."""

    link: str


jobs: Dict[UUID, Dict[str, Any]] = {}
jobs_lock = threading.Lock()

DATA_DIR = Path("/data")
VIDEO_DIR = DATA_DIR / "videos"
TRANSCRIPTION_DIR = DATA_DIR / "transcriptions"

for directory in (VIDEO_DIR, TRANSCRIPTION_DIR):
    directory.mkdir(parents=True, exist_ok=True)

_whisper_model: Optional[Any] = None
_whisper_model_lock = threading.Lock()


def get_whisper_model() -> whisper.Whisper:
    """Lazily load and return the Whisper model instance."""

    global _whisper_model
    if _whisper_model is None:
        with _whisper_model_lock:
            if _whisper_model is None:
                logger.info("Loading Whisper model for the first time.")
                _whisper_model = whisper.load_model("base")
    return _whisper_model


def process_job(job_id: UUID) -> None:
    """Process a job in a background thread without blocking the API."""

    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job["status"] = "processing"
        link = job["link"]

    target_video_path = VIDEO_DIR / f"{job_id}.mp4"
    temp_template = VIDEO_DIR / f"{job_id}.%(ext)s"
    transcription_path = TRANSCRIPTION_DIR / f"{job_id}.json"

    try:
        with suppress(FileNotFoundError):
            target_video_path.unlink()

        ydl_opts = {
            "outtmpl": str(temp_template),
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }
            ],
            "quiet": True,
            "no_warnings": True,
        }

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([link])

        if not target_video_path.exists():
            downloaded_file = next(VIDEO_DIR.glob(f"{job_id}.*"), None)
            if downloaded_file is None:
                raise RuntimeError("Video download failed: file not found")
            if downloaded_file.suffix != ".mp4":
                (
                    ffmpeg
                    .input(str(downloaded_file))
                    .output(str(target_video_path), vcodec="copy", acodec="copy")
                    .overwrite_output()
                    .run(quiet=True)
                )
                with suppress(FileNotFoundError):
                    downloaded_file.unlink()
            else:
                downloaded_file.replace(target_video_path)

        if not target_video_path.exists():
            raise RuntimeError("Video download failed")

        model = get_whisper_model()
        transcription_result = model.transcribe(str(target_video_path))

        with transcription_path.open("w", encoding="utf-8") as fp:
            json.dump(transcription_result, fp, ensure_ascii=False, indent=2)

        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            job["status"] = "done"
            job["video_path"] = str(target_video_path)
            job["transcription_path"] = str(transcription_path)
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Job %s failed: %s", job_id, exc)
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)


@app.get("/status")
def read_status() -> JSONResponse:
    """Return a basic status payload for health checks."""

    return JSONResponse({"status": "ok"})


@app.post("/jobs")
def create_job(job_request: JobCreateRequest) -> JSONResponse:
    """Accept a new job and schedule it for asynchronous processing."""

    job_id = uuid4()
    with jobs_lock:
        jobs[job_id] = {
            "link": job_request.link,
            "status": "pending",
            "video_path": None,
            "transcription_path": None,
        }

    threading.Thread(target=process_job, args=(job_id,), daemon=True).start()

    return JSONResponse({"job_id": str(job_id), "status": "pending"})


@app.get("/jobs/{job_id}")
def get_job(job_id: UUID) -> JSONResponse:
    """Retrieve job information by identifier."""

    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return JSONResponse({"detail": "Job not found"}, status_code=404)
        job_payload: Dict[str, Any] = {
            "job_id": str(job_id),
            "status": job.get("status"),
            "url": job.get("link"),
            "video_path": job.get("video_path"),
            "transcription_path": job.get("transcription_path"),
        }
        if error := job.get("error"):
            job_payload["error"] = error
    return JSONResponse(job_payload)
