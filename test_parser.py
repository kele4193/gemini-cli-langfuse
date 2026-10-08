"""
test_parser.py - Validates transcript parsing against existing Gemini CLI session logs.
"""

import glob
import os
import sys
from transcript_parser import TranscriptParser


def main():
    chat_dirs = [
        os.path.expanduser("~/.gemini/tmp/*/chats/session-*.jsonl"),
        os.path.expanduser("~/.gemini/history/*/session-*.jsonl"),
    ]

    session_files = []
    for pattern in chat_dirs:
        session_files.extend(glob.glob(pattern))

    if not session_files:
        print("[!] No session transcripts found in ~/.gemini/tmp/", file=sys.stderr)
        return

    print(f"[*] Found {len(session_files)} session files. Testing latest file:")
    latest_file = sorted(session_files, key=os.path.getmtime, reverse=True)[0]
    print(f"    File: {latest_file}\n")

    session = TranscriptParser.parse_file(latest_file)
    if not session:
        print("[X] Failed to parse session", file=sys.stderr)
        return

    print("=" * 60)
    print(f"Session ID:   {session.session_id}")
    print(f"Project Hash: {session.project_hash}")
    print(f"Start Time:   {session.start_time}")
    print(f"Turns Count:  {len(session.turns)}")
    print("=" * 60)

    for i, turn in enumerate(session.turns):
        print(f"\n[Turn {i + 1}] ID: {turn.turn_id}")
        prompt_snippet = turn.user_prompt.replace("\n", " ")[:80]
        print(f"  User Prompt: {prompt_snippet}...")
        print(f"  Generations: {len(turn.generations)}")

        total_tokens = sum(g.tokens.get("total", 0) for g in turn.generations)
        total_tools = sum(len(g.tool_calls) for g in turn.generations)
        print(f"  Total Tokens: {total_tokens} | Tool Calls: {total_tools}")

        for g_idx, gen in enumerate(turn.generations):
            thought_snippet = gen.thoughts[0]["subject"] if gen.thoughts else "(None)"
            print(f"    - Gen #{g_idx+1} [Model: {gen.model}]: thoughts='{thought_snippet}', tools={len(gen.tool_calls)}")
            for tc in gen.tool_calls:
                out_snippet = str(tc.output)[:50].replace("\n", " ") if tc.output else "(empty)"
                print(f"      * Tool [{tc.name}] args={list(tc.args.keys())} -> result preview: {out_snippet}...")

    print("\n[OK] Parser test completed successfully!")


if __name__ == "__main__":
    main()
