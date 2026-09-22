"""Optional Claude-powered advisor.

The rule base in :mod:`knowledge_base` covers the failures that have known
names.  When Fluent produces something the rules do not recognise, this
module can ask Claude to read the transcript and propose a remedy.

Two safety properties matter here and are enforced in code, not in the
prompt:

* the model may only choose from the fixed action vocabulary in
  :mod:`actions` - anything else is dropped;
* the advisor is never required.  If ``anthropic`` is missing, the key is
  unset or the call fails, the agent carries on with the rule base alone.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..config import AdvisorSettings
from ..logging_utils import get_logger
from ..models import GeometryMetrics, MeshPlan
from .actions import (
    ABORT,
    KINDS,
    PLAN,
    PLAN_OPERATIONS,
    RETRY,
    SURFACE_OPERATIONS,
    SURFACE_REPAIR,
    VOLUME_OPERATIONS,
    VOLUME_REPAIR,
    Action,
    Diagnosis,
)

SYSTEM_PROMPT = """You are a CFD meshing specialist embedded in an autonomous agent \
that drives ANSYS Fluent Meshing.

The agent has hit a failure its rule base does not recognise. Read the Fluent \
transcript, the current mesh plan and the geometry summary, then name the root \
cause and choose the remediation.

You may only use the action vocabulary given below. Prefer the cheapest fix \
that could plausibly work: an in-place repair before a re-mesh, a sizing change \
before a workflow change, and abort only when no meshing parameter can help \
(licence problems, unreadable files).

Answer in Turkish for the human-readable fields (diagnosis, root_cause, \
description); keep the identifiers exactly as listed."""

_ALLOWED = {
    PLAN: sorted(PLAN_OPERATIONS),
    SURFACE_REPAIR: sorted(SURFACE_OPERATIONS),
    VOLUME_REPAIR: sorted(VOLUME_OPERATIONS),
}

RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "number"},
        "fatal": {"type": "boolean"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "operation": {"type": "string"},
                    "description": {"type": "string"},
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["kind", "operation", "description", "params"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["diagnosis", "root_cause", "confidence", "fatal", "actions"],
    "additionalProperties": False,
}


class Advisor:
    """Thin wrapper around the Anthropic SDK."""

    def __init__(self, settings: AdvisorSettings) -> None:
        self.settings = settings
        self._client: Any = None
        self._log = get_logger()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        if not self.settings.enabled:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self._log.debug("anthropic paketi kurulu değil, danışman devre dışı.")
            return False
        return True

    def _client_or_none(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic

            # The SDK resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an
            # `ant auth login` profile on its own - an unset env var does not
            # mean there are no credentials.
            key = os.environ.get(self.settings.api_key_env)
            self._client = (
                anthropic.Anthropic(api_key=key, timeout=self.settings.timeout_s)
                if key else
                anthropic.Anthropic(timeout=self.settings.timeout_s)
            )
        except Exception as exc:  # pragma: no cover - credential dependent
            self._log.warning("Claude istemcisi oluşturulamadı: %s", exc)
            self._client = None
        return self._client

    # ------------------------------------------------------------------
    def advise(
        self,
        transcript: str,
        plan: MeshPlan,
        metrics: GeometryMetrics,
        stage: str,
        history: Optional[List[str]] = None,
    ) -> Optional[Diagnosis]:
        if not self.available():
            return None
        client = self._client_or_none()
        if client is None:
            return None

        prompt = _build_prompt(transcript, plan, metrics, stage, history or [],
                              self.settings.transcript_chars)
        payload = self._request(client, prompt)
        if payload is None:
            return None
        return _diagnosis_from_payload(payload, stage)

    # ------------------------------------------------------------------
    def _request(self, client: Any, prompt: str) -> Optional[Dict[str, Any]]:
        import anthropic

        base_kwargs: Dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self.settings.effort,
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
        }
        try:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **base_kwargs,
            )
        except anthropic.BadRequestError:
            # Older API surface, or a beta this account cannot use - the
            # advisor is optional, so degrade instead of failing the run.
            try:
                response = client.messages.create(**base_kwargs)
            except Exception as exc:
                self._log.warning("Claude danışmanı çağrılamadı: %s", exc)
                return None
        except anthropic.APIStatusError as exc:
            self._log.warning("Claude API hatası (%s): %s", exc.status_code, exc.message)
            return None
        except anthropic.APIConnectionError as exc:
            self._log.warning("Claude'a bağlanılamadı: %s", exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._log.warning("Claude danışmanı beklenmedik hata verdi: %s", exc)
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            self._log.warning(
                "Claude isteği reddetti (%s).",
                getattr(details, "category", "bilinmiyor") if details else "bilinmiyor")
            return None

        text = ""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", "") == "text":
                text += block.text
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self._log.warning("Claude yanıtı JSON olarak çözümlenemedi.")
            return None


# --------------------------------------------------------------------------

def _build_prompt(
    transcript: str,
    plan: MeshPlan,
    metrics: GeometryMetrics,
    stage: str,
    history: List[str],
    transcript_chars: int,
) -> str:
    tail = (transcript or "")[-transcript_chars:]
    vocabulary = "\n".join(
        "- {0}: {1}".format(kind, ", ".join(ops)) for kind, ops in _ALLOWED.items()
    )
    lines = [
        "## Aşama",
        stage,
        "",
        "## Geometri özeti",
        json.dumps({
            "bbox_m": list(metrics.bbox.sizes),
            "diagonal_m": metrics.diagonal,
            "volume_m3": metrics.volume,
            "area_m2": metrics.area,
            "body_count": metrics.body_count,
            "face_count": metrics.face_count,
            "min_feature_size_m": metrics.min_feature_size,
            "watertight": metrics.watertight,
            "analyzer": metrics.analyzer,
        }, ensure_ascii=False, indent=2),
        "",
        "## Mevcut mesh planı",
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
        "",
        "## Bu çalışmada daha önce uygulanan düzeltmeler",
        "\n".join("- " + h for h in history) or "- (yok)",
        "",
        "## İzin verilen eylemler",
        vocabulary,
        "- workflow: switch_to_fault_tolerant",
        "- retry / abort: operation boş bırakılır",
        "",
        "Parametreler ad/değer çiftleri olarak verilir; sayısal değerleri de "
        "metin olarak yazın (örn. name=\"factor\", value=\"1.5\").",
        "",
        "## Fluent transcript (son kısım)",
        "```",
        tail,
        "```",
    ]
    return "\n".join(lines)


def _coerce(value: str) -> Any:
    low = (value or "").strip()
    if low.lower() in ("true", "false"):
        return low.lower() == "true"
    try:
        if "." in low or "e" in low.lower():
            return float(low)
        return int(low)
    except ValueError:
        return low


def _diagnosis_from_payload(payload: Dict[str, Any], stage: str) -> Optional[Diagnosis]:
    actions: List[Action] = []
    for raw in payload.get("actions") or []:
        kind = str(raw.get("kind", "")).strip()
        operation = str(raw.get("operation", "")).strip()
        if kind not in KINDS:
            continue
        allowed = _ALLOWED.get(kind)
        if allowed is not None and operation not in allowed:
            continue
        if kind in (RETRY, ABORT):
            operation = ""
        params = {}
        for item in raw.get("params") or []:
            name = str(item.get("name", "")).strip()
            if name:
                params[name] = _coerce(str(item.get("value", "")))
        try:
            actions.append(Action(
                kind=kind, operation=operation, params=params,
                description=str(raw.get("description", "")) or operation,
            ))
        except ValueError:
            continue

    if not actions:
        return None
    confidence = payload.get("confidence")
    title = "Claude danışmanı"
    if isinstance(confidence, (int, float)):
        title += " (güven {0:.0%})".format(max(0.0, min(1.0, float(confidence))))
    return Diagnosis(
        rule_id="advisor",
        title=title,
        stage=stage,
        severity="fatal" if payload.get("fatal") else "error",
        explanation="{0}\n\nKök neden: {1}".format(
            payload.get("diagnosis", ""), payload.get("root_cause", "")).strip(),
        evidence="",
        actions=actions,
        source="advisor",
    )
