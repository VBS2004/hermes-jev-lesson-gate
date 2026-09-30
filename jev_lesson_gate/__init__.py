"""jev_lesson_gate — a Hermes plugin that asks Jev, per turn, whether the user just taught the agent
something lasting, and requests the background review when it did.

Consumer of the ``request_background_review`` hook. Install by copying this directory to
``~/.hermes/plugins/jev_lesson_gate`` and adding ``jev_lesson_gate`` to ``plugins.enabled``.
Needs ``OPENROUTER_API_KEY``. One Decisions API call per eligible turn (about $0.00004 measured).

The questions in ``questions.json`` are frozen: they are exactly the ones measured in
``learngate/run_jev.py``. Editing them invalidates the numbers.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
# Picked on the 57-turn tuning set (17/18 lessons caught, fires on 26/57). Provisional until the
# held-out set is scored.
THRESHOLD = 0.4
TIMEOUT_S = 10.0
QUESTIONS: Dict[str, Any] = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
# Second request, own state: did the WORK discover something reusable? Kept separate so the frozen user-side
# questions keep exactly the state they were measured on. Threshold set IN-SAMPLE on 20 windows.
WORK_QUESTIONS: Dict[str, Any] = json.loads((Path(__file__).parent / "questions_work.json").read_text(encoding="utf-8"))
WORK_THRESHOLD = 0.4

# Which review each signal feeds. Hermes' own review prompt treats a correction as a skill signal as
# well as a memory one; a lasting fact about the user is memory only.
KINDS_BY_QUESTION = {"corrects_assistant": ("memory", "skills"), "reveals_lasting_fact": ("memory",),
                     # A procedure, fix or pitfall found through the work belongs in a skill, not memory.
                     "discovered_in_work": ("skills",)}


def build_state(user_message: str, previous_assistant: str) -> Dict[str, str]:
    """What a reviewer sees at that moment — the same shape and truncation as the measurement."""
    return {"ai_said": (previous_assistant or "")[-1200:] or "(start of session)", "user_said": (user_message or "")[:2000]}


def kinds_for(nouls: Dict[str, Optional[float]], threshold: float = THRESHOLD) -> List[str]:
    kinds = set()
    for name, p in nouls.items():
        bar = WORK_THRESHOLD if name in WORK_QUESTIONS else threshold
        if p is not None and p >= bar:
            kinds.update(KINDS_BY_QUESTION.get(name, ()))
    return sorted(kinds)


def _api_key() -> Optional[str]:
    try:
        from agent.secret_scope import get_secret  # the profile-scoped credential path plugins must use
        return get_secret("OPENROUTER_API_KEY")
    except ImportError:
        import os
        return os.environ.get("OPENROUTER_API_KEY")


def ask_jev(state: Dict[str, str], api_key: str, questions: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[float]]:
    body = json.dumps({"model": MODEL, "state": state, "questions": questions or QUESTIONS}).encode()
    request = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode())
    answers = payload.get("decision", payload).get("answers") or {}
    return {name: (a or {}).get("noul") for name, a in answers.items()}


# Skip safety (only used when Hermes has auxiliary.background_review.judgment_can_skip on), PER KIND: a clock
# review of kind K covers every turn since the previous K review, so this plugin may skip it only if it judged
# EVERY one of those turns itself and none carried a K lesson. Memory is fed by user-side signals only; skills by
# corrections and by lessons found in the work (review_exp: 20 of 22 memory writes on windows with no user lesson
# were junk or duplicates, while most skill writes there were real lessons found through the work).
# Sessions start unverified (turns before the plugin loaded, a resumed session); a failed or partial judgment
# makes every kind unverified until its next review.
_CLEAN: Dict[str, Dict[str, bool]] = {}
_LOCK = threading.Lock()
_KINDS = ("memory", "skills")


def _unverify(session_id: str) -> None:
    with _LOCK:
        _CLEAN[session_id] = {k: False for k in _KINDS}


def judge(user_message: str = "", previous_assistant: str = "", session_id: str = "",
          clock_memory: bool = False, clock_skills: bool = False, clock_can_be_skipped: bool = False,
          turn_messages: Optional[List[Dict[str, Any]]] = None, **_: Any) -> Optional[Dict[str, Any]]:
    """``request_background_review`` callback. Fails open: any error asks for nothing and skips
    nothing, and the Hermes clock still runs."""
    sid = session_id or ""
    key = _api_key()
    if not (user_message or "").strip() or not key:
        _unverify(sid)
        return None
    try:
        nouls = ask_jev(build_state(user_message, previous_assistant), key)
        if turn_messages:
            from .work_summary import summarize_work
            nouls.update(ask_jev({"work": summarize_work(turn_messages)}, key, WORK_QUESTIONS))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.info("jev_lesson_gate: Jev call failed (%s); leaving it to the clock", exc)
        _unverify(sid)
        return None
    kinds = kinds_for(nouls)
    # An older Hermes without turn_messages: the work was not judged, so skills can never be verified clean.
    work_judged = turn_messages is not None
    clock_fired = {k for k, fired in (("memory", clock_memory), ("skills", clock_skills)) if fired}
    with _LOCK:
        before = _CLEAN.get(sid, {k: False for k in _KINDS})
        after, skip = {}, []
        for k in _KINDS:
            judged = work_judged or k == "memory"
            clean_now = before[k] and judged and k not in kinds
            if k in kinds or k in clock_fired:  # a review of k runs now (asked, or clock and not skipped)
                if k in clock_fired and k not in kinds and clean_now and clock_can_be_skipped:
                    skip.append(k)  # skipped: still clean
                    after[k] = True
                else:
                    after[k] = judged  # the review covers everything so far
            else:
                after[k] = clean_now
        _CLEAN[sid] = after
    logger.debug("jev_lesson_gate: %s -> review %s skip %s", nouls, kinds, skip)
    out: Dict[str, Any] = {}
    if kinds:
        out["review"] = kinds
    if skip:
        out["skip"] = skip
    return out or None


def register(ctx: Any) -> None:
    ctx.register_hook("request_background_review", judge)
