#!/usr/bin/env python3
"""
langfuse_hook.py - Gemini CLI Lifecycle Hook for Langfuse Observability.

This script is registered in Gemini CLI's settings.json under `AfterAgent`
or `SessionEnd` hooks. It receives the hook payload via stdin, reads the
session transcript JSONL, and ships traces to Langfuse.

Follows the Gemini CLI Hook Protocol:
- Receives JSON payload via stdin.
- Must ONLY output valid JSON to stdout (e.g. `{}`).
- All debug and warning messages are redirected to stderr.
- Fails open gracefully (never interrupts CLI operation).
"""

import sys
import os
import json
import traceback

# Add current directory to path so imports work seamlessly
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from transcript_parser import TranscriptParser
from state_manager import StateManager
from tracer import LangfuseTracer


def respond_and_exit(success: bool = True):
    """Outputs clean JSON to stdout and exits cleanly."""
    sys.stdout.write("{}\n")
    sys.stdout.flush()
    sys.exit(0)


def main():
    # 1. Read input payload from stdin
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            respond_and_exit()
        payload = json.loads(raw_input)
    except Exception as e:
        print(f"[Langfuse Hook] Error reading stdin: {e}", file=sys.stderr)
        respond_and_exit()

    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    event_name = payload.get("hook_event_name", "Unknown")

    if not transcript_path or not os.path.exists(transcript_path):
        print(f"[Langfuse Hook] Transcript path not found or missing: {transcript_path}", file=sys.stderr)
        respond_and_exit()

    # 2. Parse session transcript
    try:
        session = TranscriptParser.parse_file(transcript_path)
        if not session:
            respond_and_exit()

        # Override session_id if provided by hook payload
        if session_id:
            session.session_id = session_id

    except Exception as e:
        print(f"[Langfuse Hook] Failed to parse transcript {transcript_path}: {e}", file=sys.stderr)
        respond_and_exit()

    # 3. Export to Langfuse
    try:
        state_mgr = StateManager()
        cwd = payload.get("cwd")
        tracer = LangfuseTracer(cwd=cwd)

        if not tracer.is_configured():
            # If not configured, print hint to stderr once and exit cleanly
            print("[Langfuse Hook] Not configured. Run 'gemini extensions config gemini-langfuse' or set LANGFUSE_PUBLIC_KEY & LANGFUSE_SECRET_KEY.", file=sys.stderr)
            respond_and_exit()

        synced = tracer.export_session(session, state_mgr)
        if synced > 0:
            print(f"[Langfuse Hook] Synced {synced} turn(s) for session {session_id} on {event_name}", file=sys.stderr)

    except Exception as e:
        print(f"[Langfuse Hook] Exception during trace export: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    respond_and_exit()


if __name__ == "__main__":
    main()
