"""Backend for a remote HTTP verification service (e.g. Axiom's Axle).

**Read this before using it.** The request and response shapes of these
services are not stable public standards, and this module does not
pretend to know them. What it provides is the plumbing that is the same
for any of them -- auth, retries with backoff, timeouts, honest error
classification, response-to-``VerificationResult`` mapping -- driven by
a small declarative config. You supply the field names from the
provider's own docs; nothing here is guessed on your behalf.

That is deliberate. A backend that silently mis-maps a response field
reports ``failed`` for proofs that were actually accepted, which is the
one failure mode an evaluation harness must never have.

Configure via constructor kwargs or environment:

* ``FTP_EVAL_AXLE_URL``     -- full endpoint URL
* ``FTP_EVAL_AXLE_API_KEY`` -- bearer token
* ``FTP_EVAL_AXLE_LANGUAGE``-- source language tag (default ``axle``)

Example
-------
::

    AxleVerifier(
        url="https://api.example.com/v1/verify",
        api_key_env="FTP_EVAL_AXLE_API_KEY",
        request_map={"source": "code", "timeout": "timeout_seconds"},
        response_map={
            "ok": "verified",              # bool field meaning "accepted"
            "messages": "diagnostics",     # list of {message, line, severity}
            "error": "error_message",
        },
    )
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from ..types import (
    BackendInfo,
    Diagnostic,
    ErrorKind,
    ProofAttempt,
    ProofTask,
    Severity,
    Status,
)
from ..verifier import BackendUnavailable, RawVerdict, Verifier, VerifierError

__all__ = ["AxleVerifier", "HttpVerifier"]

#: Default names, overridable through ``request_map`` / ``response_map``.
_DEFAULT_REQUEST_MAP = {
    "source": "source",
    "timeout": "timeout_s",
    "task_id": "task_id",
    "language": "language",
}
_DEFAULT_RESPONSE_MAP = {
    "ok": "verified",
    "messages": "messages",
    "error": "error",
    "status": "status",
}

_SEVERITY_ALIASES = {
    "error": Severity.ERROR,
    "err": Severity.ERROR,
    "fatal": Severity.ERROR,
    "warning": Severity.WARNING,
    "warn": Severity.WARNING,
    "info": Severity.INFO,
    "note": Severity.INFO,
}


class HttpVerifier(Verifier):
    """Generic verifier that POSTs a source file to an HTTP endpoint."""

    name = "http"
    language = "unknown"
    #: One HTTP request per attempt, no shared session state.
    thread_safe = True

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "FTP_EVAL_AXLE_API_KEY",
        language: str | None = None,
        request_map: Mapping[str, str] | None = None,
        response_map: Mapping[str, str] | None = None,
        extra_payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        max_retries: int = 3,
        backoff_s: float = 1.5,
        **config: Any,
    ) -> None:
        super().__init__(**config)
        self.url = url or os.environ.get("FTP_EVAL_AXLE_URL") or ""
        self.api_key = api_key or os.environ.get(api_key_env) or ""
        self.api_key_env = api_key_env
        if language:
            self.language = language
        self.request_map = {**_DEFAULT_REQUEST_MAP, **(request_map or {})}
        self.response_map = {**_DEFAULT_RESPONSE_MAP, **(response_map or {})}
        self.extra_payload = dict(extra_payload or {})
        self.headers = dict(headers or {})
        self.max_retries = max(1, int(max_retries))
        self.backoff_s = max(0.0, float(backoff_s))

    # -- availability -------------------------------------------------

    def info(self) -> BackendInfo:
        if not self.url:
            return BackendInfo(
                self.name,
                self.language,
                False,
                detail="no endpoint configured; pass url= or set FTP_EVAL_AXLE_URL",
            )
        if not self.api_key:
            return BackendInfo(
                self.name,
                self.language,
                False,
                detail="no credential; set %s (or pass api_key=)" % self.api_key_env,
            )
        return BackendInfo(
            self.name,
            self.language,
            True,
            detail="endpoint %s (response mapping is user-supplied and unverified)" % self.url,
            supports=("remote", "retry", "configurable_mapping"),
        )

    # -- verification -------------------------------------------------

    def _verify(
        self, task: ProofTask, attempt: ProofAttempt, source: str, timeout_s: float
    ) -> RawVerdict:
        info = self.info()
        if not info.available:
            raise BackendUnavailable(info.detail or "%s backend unavailable" % self.name)

        payload: dict[str, Any] = dict(self.extra_payload)
        payload[self.request_map["source"]] = source
        payload[self.request_map["timeout"]] = timeout_s
        payload[self.request_map["task_id"]] = task.task_id
        payload[self.request_map["language"]] = task.language

        body, http_status = self._post(payload, timeout_s)
        return self._interpret(body, http_status)

    def _post(self, payload: Mapping[str, Any], timeout_s: float) -> tuple[Mapping[str, Any], int]:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ftp-eval/0.1",
            **self.headers,
        }
        if self.api_key:
            headers.setdefault("Authorization", "Bearer %s" % self.api_key)

        last_error: Exception | None = None
        for attempt_no in range(self.max_retries):
            request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
            try:
                # +5s so a server-side timeout surfaces as a real verdict
                # rather than as our own socket timeout.
                with urllib.request.urlopen(request, timeout=timeout_s + 5) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    status = int(getattr(response, "status", 200) or 200)
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise VerifierError(
                        "endpoint returned non-JSON body (%s): %s" % (exc.msg, raw[:300])
                    ) from exc
                if not isinstance(parsed, dict):
                    raise VerifierError("endpoint returned %s, expected an object" % type(parsed).__name__)
                return parsed, status
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code in (400, 401, 403, 404, 422):
                    # Config or credential problems do not improve on retry.
                    raise BackendUnavailable(
                        "endpoint rejected the request with HTTP %d: %s" % (exc.code, detail)
                    ) from exc
                last_error = VerifierError("HTTP %d from endpoint: %s" % (exc.code, detail))
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = VerifierError("could not reach endpoint: %s" % exc)

            if attempt_no < self.max_retries - 1 and self.backoff_s:
                time.sleep(self.backoff_s * (2 ** attempt_no))

        raise last_error or VerifierError("request failed for an unknown reason")

    def _interpret(self, body: Mapping[str, Any], http_status: int) -> RawVerdict:
        raw = {"http_status": http_status, "response": _truncate_json(body)}
        diagnostics = self._parse_messages(body)

        ok_field = self.response_map["ok"]
        status_field = self.response_map["status"]
        error_field = self.response_map["error"]

        if ok_field in body:
            accepted = bool(body[ok_field])
        elif status_field in body:
            accepted = str(body[status_field]).lower() in ("ok", "verified", "proved", "success", "valid")
        else:
            # Refusing to guess is the whole point: an unrecognized shape
            # is a configuration error, not a failed proof.
            raise VerifierError(
                "response has neither %r nor %r; set response_map to match the "
                "provider's schema. Keys seen: %s"
                % (ok_field, status_field, ", ".join(sorted(body)[:12]))
            )

        if accepted:
            return RawVerdict.verified(raw)

        message = str(body.get(error_field) or "")
        kind = _classify_remote(message, diagnostics)
        if kind is ErrorKind.TIMEOUT:
            return RawVerdict(Status.TIMEOUT, ErrorKind.TIMEOUT, diagnostics, raw)
        if not diagnostics and message:
            diagnostics = (Diagnostic(Severity.ERROR, message, kind=kind),)
        return RawVerdict.failed(kind, diagnostics, raw)

    def _parse_messages(self, body: Mapping[str, Any]) -> tuple[Diagnostic, ...]:
        messages = body.get(self.response_map["messages"])
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            return ()
        out: list[Diagnostic] = []
        for item in messages:
            if isinstance(item, str):
                out.append(Diagnostic(Severity.ERROR, item, kind=_classify_remote(item, ())))
                continue
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("message") or item.get("text") or item.get("detail") or "")
            severity = _SEVERITY_ALIASES.get(str(item.get("severity", "error")).lower(), Severity.ERROR)
            out.append(
                Diagnostic(
                    severity=severity,
                    message=text,
                    line=_as_int(item.get("line") or item.get("row")),
                    column=_as_int(item.get("column") or item.get("col")),
                    kind=_classify_remote(text, ()) if severity is Severity.ERROR else None,
                )
            )
        return tuple(out)


class AxleVerifier(HttpVerifier):
    """:class:`HttpVerifier` preset for Axiom's Axle.

    The mapping defaults are placeholders, not documented API facts.
    Confirm the field names against the provider's current API reference
    and pass ``request_map`` / ``response_map`` accordingly; run
    ``ftp-eval doctor --backend axle`` plus a known-good and a
    known-broken proof before trusting any numbers from it.
    """

    name = "axle"
    language = "axle"

    def __init__(self, *, language: str | None = None, **config: Any) -> None:
        super().__init__(
            language=language or os.environ.get("FTP_EVAL_AXLE_LANGUAGE") or "axle",
            **config,
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate_json(body: Mapping[str, Any], limit: int = 4000) -> Any:
    text = json.dumps(body, ensure_ascii=False)
    if len(text) <= limit:
        return dict(body)
    return {"_truncated": True, "_text": text[:limit]}


def _classify_remote(message: str, diagnostics: Sequence[Diagnostic]) -> ErrorKind:
    lowered = message.lower()
    if any(word in lowered for word in ("timeout", "timed out", "deadline")):
        return ErrorKind.TIMEOUT
    if any(word in lowered for word in ("parse", "syntax", "unexpected token")):
        return ErrorKind.SYNTAX
    if "unknown" in lowered and ("identifier" in lowered or "constant" in lowered):
        return ErrorKind.UNKNOWN_IDENTIFIER
    if "unsolved" in lowered or "remaining goal" in lowered:
        return ErrorKind.UNSOLVED_GOALS
    if "type" in lowered and "mismatch" in lowered:
        return ErrorKind.TYPE
    if any(word in lowered for word in ("memory", "resource", "limit exceeded")):
        return ErrorKind.RESOURCE_LIMIT
    for d in diagnostics:
        if d.kind is not None:
            return d.kind
    return ErrorKind.UNKNOWN
