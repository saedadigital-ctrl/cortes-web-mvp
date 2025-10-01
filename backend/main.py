"""FastAPI application exposing endpoints for job submissions."""

import json
import logging
import shutil
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional
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
CLIPS_DIR = DATA_DIR / "clips"

for directory in (VIDEO_DIR, TRANSCRIPTION_DIR, CLIPS_DIR):
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


def build_clip_plan(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create clip windows based on Whisper segments and silence gaps."""

    if not segments:
        return []

    sorted_segments = sorted(segments, key=lambda item: float(item.get("start", 0.0)))
    clips: List[Dict[str, Any]] = []

    current_segments: List[Dict[str, Any]] = []
    current_start: Optional[float] = None
    current_end: Optional[float] = None
    prev_end: Optional[float] = None

    for segment in sorted_segments:
        seg_start = float(segment.get("start", 0.0))
        seg_end = float(segment.get("end", seg_start))

        if current_segments:
            gap = seg_start - (prev_end if prev_end is not None else current_end or seg_start)
            duration = (current_end or seg_end) - (current_start or seg_start)
            potential_duration = seg_end - (current_start or seg_start)

            should_split = False
            if gap > 1.5 and duration >= 30:
                should_split = True
            elif duration >= 90:
                should_split = True
            elif potential_duration > 90:
                should_split = True

            if should_split:
                clips.append(
                    {
                        "start": current_start,
                        "end": current_end,
                        "segments": current_segments[:],
                    }
                )
                current_segments = [segment]
                current_start = seg_start
                current_end = seg_end
            else:
                current_segments.append(segment)
                current_end = max(current_end or seg_end, seg_end)
        else:
            current_segments = [segment]
            current_start = seg_start
            current_end = seg_end

        prev_end = seg_end

    if current_segments and current_start is not None and current_end is not None:
        clips.append(
            {
                "start": current_start,
                "end": current_end,
                "segments": current_segments[:],
            }
        )

    if len(clips) <= 1:
        return clips

    index = 0
    while index < len(clips):
        clip = clips[index]
        duration = float(clip["end"]) - float(clip["start"])
        if duration < 30 and len(clips) > 1:
            if index == 0:
                next_clip = clips[1]
                next_clip["segments"] = clip["segments"] + next_clip["segments"]
                next_clip["start"] = clip["start"]
                clips.pop(index)
                continue
            else:
                prev_clip = clips[index - 1]
                prev_clip["segments"].extend(clip["segments"])
                prev_clip["end"] = clip["end"]
                clips.pop(index)
                index -= 1
                continue
        index += 1

    return clips


def format_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp."""

    total_ms = max(int(round(seconds * 1000)), 0)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def write_srt(segments: List[Dict[str, Any]], clip_start: float, clip_end: float, path: Path) -> None:
    """Persist SRT subtitles for a given clip."""

    lines: List[str] = []
    counter = 1
    for segment in segments:
        seg_text = str(segment.get("text", "")).strip()
        if not seg_text:
            continue

        seg_start = max(float(segment.get("start", clip_start)), clip_start)
        seg_end = min(float(segment.get("end", clip_end)), clip_end)
        if seg_end <= seg_start:
            continue

        relative_start = seg_start - clip_start
        relative_end = seg_end - clip_start

        lines.append(str(counter))
        lines.append(f"{format_timestamp(relative_start)} --> {format_timestamp(relative_end)}")
        lines.append(seg_text)
        lines.append("")
        counter += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def generate_clips(
    job_id: UUID, video_path: Path, transcription: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Generate clips and subtitles from Whisper transcription."""

    clip_segments = build_clip_plan(transcription.get("segments") or [])
    if not clip_segments:
        return []

    clip_directory = CLIPS_DIR / str(job_id)
    clip_directory.mkdir(parents=True, exist_ok=True)
    for existing in clip_directory.glob("*"):
        if existing.is_dir():
            shutil.rmtree(existing, ignore_errors=True)
        else:
            with suppress(FileNotFoundError):
                existing.unlink()

    clips_metadata: List[Dict[str, Any]] = []
    for index, clip in enumerate(clip_segments, start=1):
        start = float(clip["start"])
        end = float(clip["end"])
        duration = max(end - start, 0.0)
        if duration <= 0:
            continue

        clip_path = clip_directory / f"clip_{index}.mp4"
        subtitle_path = clip_directory / f"clip_{index}.srt"

        (
            ffmpeg
            .input(str(video_path), ss=start, t=duration)
            .output(str(clip_path), vcodec="copy", acodec="copy")
            .overwrite_output()
            .run(quiet=True)
        )

        write_srt(clip["segments"], start, end, subtitle_path)

        clips_metadata.append(
            {
                "index": index,
                "clip_path": str(clip_path),
                "subtitle_path": str(subtitle_path),
                "start": start,
                "end": end,
            }
        )

    return clips_metadata


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
            job["status"] = "cutting"
            job["video_path"] = str(target_video_path)
            job["transcription_path"] = str(transcription_path)

        clips_metadata = generate_clips(job_id, target_video_path, transcription_result)

        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            job["status"] = "done"
            job["clips"] = clips_metadata
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Job %s failed: %s", job_id, exc)
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job.setdefault("clips", [])


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
            "clips": [],
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
            "clips": job.get("clips", []),
        }
        if error := job.get("error"):
            job_payload["error"] = error
    return JSONResponse(job_payload)
