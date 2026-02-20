"""Local Windows installer automation agent (v1)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from brain_agent import BrainAction, GeminiBrain

try:
    import pyautogui
except Exception as exc:  # pragma: no cover - runtime environment dependent
    pyautogui = None
    _PYAUTOGUI_IMPORT_ERROR = exc
else:
    _PYAUTOGUI_IMPORT_ERROR = None

SUPPORTED_INPUTS = {".zip", ".exe", ".msi"}
INSTALLER_HINTS = ("setup", "install", "installer", "msi")


@dataclass(slots=True)
class Observation:
    step_index: int
    screenshot_path: str
    state_hash: str
    window_title: str
    timestamp: float
    ocr_text: str = ""
    intent: str = "unknown"


@dataclass(slots=True)
class RunResult:
    status: str
    reason: str
    binary_paths: list[str]
    artifacts_dir: str
    steps: int
    error_code: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows local installer automation agent")
    parser.add_argument("--file", required=True, help="Path to .exe/.msi/.zip")
    parser.add_argument("--zip-password", default=None, help="Optional zip password")
    parser.add_argument("--gemini-api-key", default=None, help="Gemini API key override")
    parser.add_argument("--model", default="gemini-3-flash-preview", help="Gemini model")
    parser.add_argument("--max-steps", type=int, default=80, help="Maximum UI steps")
    parser.add_argument("--step-delay", type=float, default=1.4, help="Delay between steps")
    parser.add_argument("--run-as-admin", action="store_true", help="Run installer elevated")
    parser.add_argument("--dry-run", action="store_true", help="Do not send keys")
    parser.add_argument(
        "--artifacts-dir",
        default=".agent_runs",
        help="Directory where screenshots and logs are saved",
    )
    return parser.parse_args()


def ensure_windows_native() -> tuple[bool, str | None]:
    if platform.system() != "Windows":
        return False, "native_windows_required"
    if "WSL_DISTRO_NAME" in os.environ or "microsoft" in platform.release().lower():
        return False, "native_windows_required"
    return True, None


def setup_artifacts(base_dir: str) -> Path:
    run_stamp = time.strftime("%Y%m%d-%H%M%S")
    root = Path(base_dir).resolve() / f"run-{run_stamp}"
    (root / "screenshots").mkdir(parents=True, exist_ok=True)
    return root


def prepare_input(input_path: str, zip_password: str | None) -> tuple[Path, Path | None]:
    source = Path(input_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_INPUTS:
        raise ValueError("Unsupported file type; expected .zip/.exe/.msi")

    if source.suffix.lower() in {".exe", ".msi"}:
        return source, None

    extract_root = Path(tempfile.mkdtemp(prefix="auto-installer-"))
    pwd = zip_password.encode("utf-8") if zip_password else None
    try:
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(path=extract_root, pwd=pwd)
    except RuntimeError as exc:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise RuntimeError("Failed to extract zip archive (wrong password?)") from exc

    candidates = sorted(
        [p for p in extract_root.rglob("*") if p.is_file() and p.suffix.lower() in {".exe", ".msi"}],
        key=score_installer_candidate,
        reverse=True,
    )
    if not candidates:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise RuntimeError("No installer executable found in zip archive")
    return candidates[0], extract_root


def score_installer_candidate(path: Path) -> int:
    name = path.name.lower()
    score = 0
    for hint in INSTALLER_HINTS:
        if hint in name:
            score += 20
    if path.suffix.lower() == ".msi":
        score += 10
    depth = len(path.parts)
    score -= min(depth, 10)
    return score


def launch_installer(installer_path: Path, run_as_admin: bool) -> subprocess.Popen[bytes] | None:
    if run_as_admin:
        cmd = (
            "Start-Process "
            f"-FilePath '{str(installer_path)}' "
            "-Verb RunAs"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            check=True,
            capture_output=True,
            text=True,
        )
        return None

    if installer_path.suffix.lower() == ".msi":
        args = ["msiexec", "/i", str(installer_path)]
    else:
        args = [str(installer_path)]
    return subprocess.Popen(args)


def active_window_title() -> str:
    return ""


def capture_observation(step_index: int, screenshots_dir: Path) -> Observation:
    if pyautogui is None:
        raise RuntimeError(f"pyautogui is required: {_PYAUTOGUI_IMPORT_ERROR}")

    path = screenshots_dir / f"step-{step_index:03d}.png"
    image = pyautogui.screenshot()
    image.save(path)
    image_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return Observation(
        step_index=step_index,
        screenshot_path=str(path),
        state_hash=image_hash,
        window_title=active_window_title(),
        timestamp=time.time(),
    )


def send_action(action: BrainAction, dry_run: bool) -> None:
    if dry_run:
        return
    if pyautogui is None:
        raise RuntimeError(f"pyautogui is required: {_PYAUTOGUI_IMPORT_ERROR}")
    if len(action.keys) == 1:
        pyautogui.press(action.keys[0])
        return
    pyautogui.hotkey(*action.keys)


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def detect_not_installer(ocr_text: str, intent: str, window_title: str) -> bool:
    title = window_title.lower()
    text = ocr_text.lower()
    if intent == "not_installer":
        return True
    if "welcome to" in text and "setup" not in text and "installer" not in text:
        return True
    if title and all(token not in title for token in ("setup", "install", "installer", "wizard")):
        if any(token in text for token in ("dashboard", "workspace", "project", "open file")):
            return True
    return False


def discover_binary_candidates(installer: Path) -> list[str]:
    candidates: list[str] = []
    stem = installer.stem.replace(" installer", "").replace(" setup", "").strip()
    program_files = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData"),
    ]
    for base in program_files:
        if not base:
            continue
        root = Path(base)
        if not root.exists():
            continue
        direct = root / stem
        if direct.exists():
            for exe in direct.rglob("*.exe"):
                candidates.append(str(exe))
                if len(candidates) >= 10:
                    return candidates
    return candidates


def main() -> int:
    args = parse_args()
    ok, error = ensure_windows_native()
    if not ok:
        result = RunResult(
            status="failed",
            reason="This v1 agent must be run in native Windows Python, not WSL/Linux.",
            binary_paths=[],
            artifacts_dir="",
            steps=0,
            error_code=error,
        )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 2

    artifacts_dir = setup_artifacts(args.artifacts_dir)
    events_file = artifacts_dir / "events.jsonl"
    screenshots_dir = artifacts_dir / "screenshots"
    temp_extract_dir: Path | None = None

    try:
        installer_path, temp_extract_dir = prepare_input(args.file, args.zip_password)
    except Exception as exc:
        result = RunResult(
            status="failed",
            reason=str(exc),
            binary_paths=[],
            artifacts_dir=str(artifacts_dir),
            steps=0,
            error_code="input_prepare_failed",
        )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 2

    try:
        brain = GeminiBrain(api_key=args.gemini_api_key, model=args.model)
    except Exception as exc:
        result = RunResult(
            status="failed",
            reason=f"Gemini initialization failed: {exc}",
            binary_paths=[],
            artifacts_dir=str(artifacts_dir),
            steps=0,
            error_code="gemini_unavailable",
        )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 2

    try:
        process = launch_installer(installer_path, args.run_as_admin)
    except Exception as exc:
        result = RunResult(
            status="failed",
            reason=f"Installer launch failed: {exc}",
            binary_paths=[],
            artifacts_dir=str(artifacts_dir),
            steps=0,
            error_code="installer_launch_failed",
        )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 2

    time.sleep(2.0)
    repeated_hash_count = 0
    previous_hash = ""
    previous_ocr = ""
    recent_actions: list[list[str]] = []
    final_status = "failed"
    final_reason = "Max steps reached"
    step_count = 0

    for step in range(1, args.max_steps + 1):
        step_count = step
        obs = capture_observation(step, screenshots_dir)
        if obs.state_hash == previous_hash:
            repeated_hash_count += 1
        else:
            repeated_hash_count = 0
        previous_hash = obs.state_hash

        if repeated_hash_count >= 8:
            final_status = "manual_required"
            final_reason = "UI appears stalled on the same screen"
            break

        with open(obs.screenshot_path, "rb") as handle:
            image_bytes = handle.read()

        context = {
            "step_index": step,
            "window_title": obs.window_title,
            "previous_ocr": previous_ocr,
            "recent_actions": recent_actions[-6:],
        }
        try:
            decision = brain.analyze_step(image_bytes=image_bytes, context=context)
        except Exception as exc:
            final_status = "failed"
            final_reason = f"Gemini decision failed: {exc}"
            write_jsonl(events_file, {"step": step, "error": str(exc), "kind": "brain_error"})
            break

        obs.ocr_text = decision.ocr_text
        obs.intent = decision.intent
        previous_ocr = decision.ocr_text

        write_jsonl(
            events_file,
            {
                "step": step,
                "observation": asdict(obs),
                "decision": {
                    "intent": decision.intent,
                    "confidence": decision.confidence,
                    "done": decision.done,
                    "needs_human": decision.needs_human,
                    "reason": decision.reason,
                    "actions": [asdict(a) for a in decision.actions],
                },
            },
        )

        if detect_not_installer(decision.ocr_text, decision.intent, obs.window_title):
            final_status = "not_installer"
            final_reason = "Input appears to launch an app, not an installer wizard"
            break

        if decision.done:
            final_status = "success"
            final_reason = "Installer flow indicates completion"
            break

        if decision.needs_human or decision.confidence < 0.35:
            final_status = "manual_required"
            final_reason = "Model confidence too low for safe automation"
            break

        if not decision.actions:
            time.sleep(args.step_delay)
            continue

        for action in decision.actions:
            try:
                send_action(action, args.dry_run)
            except Exception as exc:
                final_status = "failed"
                final_reason = f"Action execution failed: {exc}"
                break
            recent_actions.append(action.keys)
            time.sleep(0.3)

        if final_status == "failed":
            break
        time.sleep(args.step_delay)

        if process is not None and process.poll() is not None and step > 2:
            final_status = "success"
            final_reason = "Installer process exited"
            break

    binary_paths = discover_binary_candidates(installer_path)
    result = RunResult(
        status=final_status,
        reason=final_reason,
        binary_paths=binary_paths,
        artifacts_dir=str(artifacts_dir),
        steps=step_count,
        error_code=None if final_status == "success" else final_status,
    )
    print(json.dumps(asdict(result), ensure_ascii=True, indent=2))

    if temp_extract_dir is not None:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
    return 0 if final_status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
