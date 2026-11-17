"""Run the pinned audio pipeline without placing provider credentials in argv/env."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory


def transcribe(python: Path, channel: str, value: str, key: str) -> dict:
    result = {"channel": channel, "action": "transcribe"}
    bins = [shutil.which(name) for name in ("ffmpeg", "ffprobe")]
    if not all(bins):
        return {**result, "ok": False, "error": "NEED_SETUP"}
    url = ("https://www.youtube.com/watch?v=" if channel == "youtube"
           else "https://www.xiaoyuzhoufm.com/episode/") + value
    try:
        with TemporaryDirectory(prefix="tcode-transcribe-") as directory:
            run = subprocess.run(
                [str(python), "-I", str(Path(__file__).resolve()), channel, url],
                input=key, text=True, capture_output=True, timeout=170, cwd=directory,
                env={"HOME": directory, "TMPDIR": directory,
                     "PATH": os.pathsep.join([*(str(Path(path).parent) for path in bins), os.defpath])},
            )
        if run.returncode or len(run.stdout) > 512_000:
            return {**result, "ok": False, "error": "TRANSCRIPTION_FAILED"}
        transcript = json.loads(run.stdout)
        if not isinstance(transcript, str) or not transcript.strip():
            return {**result, "ok": False, "error": "NO_VERIFIABLE_CONTENT"}
    except subprocess.TimeoutExpired:
        return {**result, "ok": False, "error": "TIMEOUT"}
    except (OSError, ValueError):
        return {**result, "ok": False, "error": "TRANSCRIPTION_FAILED"}
    return {**result, "ok": True, "backend": "groq-whisper", "fetched_at": datetime.now(UTC).isoformat(),
            "partial": len(transcript) > 12000, "items": [{"url": url, "text": transcript[:12000],
                "data": {"kind": "audio_transcript", "provider": "groq", "model": "whisper-large-v3"}}]}


def _xiaoyuzhou_audio(url: str) -> str:
    import re
    from time import monotonic

    import requests

    deadline = monotonic() + 55

    def chunks(url: str, budget: int):
        with requests.get(url, timeout=(8, 10), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError("Unexpected media response")
            for data in response.iter_content(65536):
                budget -= len(data)
                if budget < 0 or monotonic() > deadline:
                    raise ValueError("Media download exceeded budget")
                yield data

    page = b"".join(chunks(url, 4 * 1024 * 1024))
    match = re.search(rb'https://media\.xyzcdn\.net/[A-Za-z0-9/_%.~-]+\.(m4a|mp3)(?=["?\s<])', page)
    if not match:
        raise ValueError("No approved public audio in episode")
    path = Path("source." + match.group(1).decode())
    with path.open("wb") as stream:
        for data in chunks(match.group(0).decode(), 128 * 1024 * 1024):
            stream.write(data)
    return str(path)


if __name__ == "__main__":
    import sys

    from agent_reach.transcribe import transcribe as run_transcription

    channel, url = sys.argv[1:]
    key = sys.stdin.read()
    try:
        source = _xiaoyuzhou_audio(url) if channel == "xiaoyuzhou" else url
        # Explicit provider: never use the Agent Reach user config or provider fallback.
        print(json.dumps(run_transcription(source, provider="groq", out_dir=Path.cwd(),
                                           config={"groq_api_key": key}), ensure_ascii=False))
    except Exception:
        # Do not emit provider bodies, download diagnostics, or credentials.
        sys.exit(1)
