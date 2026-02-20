"""Gemini-backed OCR and decision engine for installer navigation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types


ALLOWED_SIMPLE_KEYS = {
    "tab",
    "enter",
    "space",
    "esc",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pagedown",
    "pageup",
}
ALLOWED_MODIFIERS = {"alt", "shift", "ctrl"}


@dataclass(slots=True)
class BrainAction:
    keys: list[str]
    reason: str


@dataclass(slots=True)
class BrainDecision:
    ocr_text: str
    language: str
    intent: str
    done: bool
    needs_human: bool
    confidence: float
    reason: str
    actions: list[BrainAction]


class GeminiBrain:
    """Wraps Gemini OCR + reasoning for next-step keyboard actions."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.0-flash") -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.client = genai.Client(api_key=key)
        self.model = model

    def analyze_step(self, image_bytes: bytes, context: dict[str, Any]) -> BrainDecision:
        prompt = self._build_prompt(context)
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    types.Part.from_text(text=prompt),
                ],
            )
        ]

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        text = response.text or ""
        payload = self._parse_json(text)
        return self._validate(payload)

    def _build_prompt(self, context: dict[str, Any]) -> str:
        recent = context.get("recent_actions", [])
        window_title = context.get("window_title", "")
        previous_ocr = context.get("previous_ocr", "")
        return (
            "You are a Windows installer keyboard automation agent.\n"
            "Given this screenshot, extract text and decide the safest next key actions.\n"
            "Prefer reversible actions (Tab, Shift+Tab, arrows, Enter, Space, Alt+letter).\n"
            "Avoid destructive shortcuts and never invent unsupported keys.\n"
            "If uncertain, set needs_human=true and return no actions.\n\n"
            f"Current window title: {window_title}\n"
            f"Recent actions: {recent}\n"
            f"Previous OCR excerpt: {previous_ocr[:500]}\n\n"
            "Return ONLY JSON with this exact schema:\n"
            "{\n"
            '  "ocr_text": "string",\n'
            '  "language": "string",\n'
            '  "intent": "license|path_select|progress|finish|confirm|not_installer|unknown",\n'
            '  "done": false,\n'
            '  "needs_human": false,\n'
            '  "confidence": 0.0,\n'
            '  "reason": "string",\n'
            '  "actions": [\n'
            '    {"keys": ["alt", "n"], "reason": "string"}\n'
            "  ]\n"
            "}\n"
            "Rules for keys:\n"
            "- keys must be lowercase strings\n"
            "- allow simple keys: tab, enter, space, esc, up, down, left, right, home, end, pagedown, pageup\n"
            "- allow modifiers alt/shift/ctrl with one additional key\n"
            "- no more than 3 actions\n"
            "- if intent is progress, usually return empty actions unless a prompt requires confirmation\n"
        )

    def _parse_json(self, text: str) -> dict[str, Any]:
        raw = text.strip()
        if not raw:
            raise ValueError("Gemini returned empty response")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                raise ValueError("Gemini did not return valid JSON")
            return json.loads(match.group(0))

    def _validate(self, payload: dict[str, Any]) -> BrainDecision:
        ocr_text = str(payload.get("ocr_text", ""))
        language = str(payload.get("language", "unknown"))
        intent = str(payload.get("intent", "unknown"))
        done = bool(payload.get("done", False))
        needs_human = bool(payload.get("needs_human", False))
        confidence = float(payload.get("confidence", 0.0))
        reason = str(payload.get("reason", ""))
        raw_actions = payload.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("actions must be a list")

        actions: list[BrainAction] = []
        for item in raw_actions[:3]:
            if not isinstance(item, dict):
                continue
            keys = item.get("keys", [])
            if not isinstance(keys, list) or not keys:
                continue
            normalized = [str(k).strip().lower() for k in keys if str(k).strip()]
            if not self._is_allowed_action(normalized):
                continue
            actions.append(BrainAction(keys=normalized, reason=str(item.get("reason", ""))))

        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        if needs_human:
            actions = []

        return BrainDecision(
            ocr_text=ocr_text,
            language=language,
            intent=intent,
            done=done,
            needs_human=needs_human,
            confidence=confidence,
            reason=reason,
            actions=actions,
        )

    def _is_allowed_action(self, keys: list[str]) -> bool:
        if not keys:
            return False
        if len(keys) == 1:
            token = keys[0]
            return token in ALLOWED_SIMPLE_KEYS or (len(token) == 1 and token.isalnum())

        modifiers = [k for k in keys if k in ALLOWED_MODIFIERS]
        non_modifiers = [k for k in keys if k not in ALLOWED_MODIFIERS]
        if len(non_modifiers) != 1:
            return False
        if len(modifiers) != len(keys) - 1:
            return False
        target = non_modifiers[0]
        if target in ALLOWED_SIMPLE_KEYS:
            return True
        return len(target) == 1 and target.isalnum()
