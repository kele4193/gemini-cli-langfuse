#!/usr/bin/env python3
"""
sync.py - Manual backfill and synchronization CLI for Gemini CLI sessions into Langfuse.

Usage:
  python3 sync.py --check-auth
  python3 sync.py --list
  python3 sync.py --latest
  python3 sync.py --all
  python3 sync.py --file /path/to/session.jsonl
"""

import os
import sys
import glob
import argparse
from transcript_parser import TranscriptParser
from state_manager import StateManager
from tracer import LangfuseTracer, HAS_LANGFUSE_SDK


def get_all_session_files():
    chat_patterns = [
        os.path.expanduser("~/.gemini/tmp/*/chats/session-*.jsonl"),
        os.path.expanduser("~/.gemini/history/*/session-*.jsonl"),
    ]
    files = []
    for pat in chat_patterns:
        files.extend(glob.glob(pat))
    return sorted(files, key=os.path.getmtime, reverse=True)


def check_auth():
    print("[*] Checking Langfuse configuration...")
    tracer = LangfuseTracer()
    print(f"    Host:        {tracer.host}")
    print(f"    Public Key:  {tracer.public_key or '(Not set)'}")
    print(f"    Secret Key:  {'******' if tracer.secret_key else '(Not set)'}")
    print(f"    SDK Mode:    {'Langfuse Python SDK installed' if HAS_LANGFUSE_SDK else 'HTTP Ingestion fallback (SDK not installed)'}")

    if not tracer.is_configured():
        print("[!] Langfuse credentials not configured. Please set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in ~/.gemini-langfuse/.env", file=sys.stderr)
        return False

    if tracer.client:
        try:
            auth_ok = tracer.client.auth_check()
            print(f"[+] Auth check result: {auth_ok}")
            return auth_ok
        except Exception as e:
            print(f"[-] Auth check failed: {e}", file=sys.stderr)
            return False
    else:
        print("[*] Using HTTP mode. Ready to sync.")
        return True


def list_sessions():
    files = get_all_session_files()
    print(f"Found {len(files)} session transcripts:\n")
    for idx, f in enumerate(files):
        session = TranscriptParser.parse_file(f)
        if session:
            turns_cnt = len(session.turns)
            gens_cnt = sum(len(t.generations) for t in session.turns)
            first_prompt = session.turns[0].user_prompt[:50] if session.turns else "Empty"
            print(f"[{idx+1}] ID: {session.session_id}")
            print(f"    Path:  {f}")
            print(f"    Turns: {turns_cnt} | Generations: {gens_cnt}")
            print(f"    Prompt Preview: {first_prompt}...\n")


def sync_file(file_path: str, force: bool = False):
    print(f"[*] Processing transcript: {file_path}")
    session = TranscriptParser.parse_file(file_path)
    if not session:
        print(f"[X] Could not parse transcript from {file_path}", file=sys.stderr)
        return

    tracer = LangfuseTracer()
    state_mgr = None if force else StateManager()

    if not tracer.is_configured():
        print("[!] Langfuse credentials not set in .env or environment variables.", file=sys.stderr)
        return

    synced = tracer.export_session(session, state_mgr)
    print(f"[+] Successfully exported {synced} turn(s) to Langfuse (Host: {tracer.host}).")


def main():
    parser = argparse.ArgumentParser(description="Gemini CLI -> Langfuse Session Synchronizer")
    parser.add_argument("--check-auth", action="store_true", help="Check Langfuse credentials and connectivity")
    parser.add_argument("--list", action="store_true", help="List all local Gemini CLI session transcripts")
    parser.add_argument("--latest", action="store_true", help="Sync the latest session transcript")
    parser.add_argument("--all", action="store_true", help="Sync all session transcripts")
    parser.add_argument("--file", type=str, help="Sync a specific transcript file")
    parser.add_argument("--force", action="store_true", help="Force sync, ignoring state history")

    args = parser.parse_args()

    if args.check_auth:
        check_auth()
    elif args.list:
        list_sessions()
    elif args.file:
        sync_file(args.file, force=args.force)
    elif args.latest:
        files = get_all_session_files()
        if files:
            sync_file(files[0], force=args.force)
        else:
            print("[!] No session transcripts found.", file=sys.stderr)
    elif args.all:
        files = get_all_session_files()
        for f in files:
            sync_file(f, force=args.force)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
