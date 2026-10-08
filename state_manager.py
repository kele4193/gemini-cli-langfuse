"""
state_manager.py - Tracks hook execution state across turns.

Stores processed message IDs and turn offsets to avoid duplicating traces
in Langfuse when hooks trigger after every agent turn.
"""

import json
import os
import sys
from typing import Dict, Any, Set, Optional


class StateManager:
    """Manages persistent state for the Langfuse hook."""

    def __init__(self, state_file_path: Optional[str] = None):
        if not state_file_path:
            state_dir = os.path.expanduser("~/.gemini/state")
            os.makedirs(state_dir, exist_ok=True)
            state_file_path = os.path.join(state_dir, "langfuse_hook_state.json")
        self.state_file_path = state_file_path
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file_path):
            try:
                with open(self.state_file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[StateManager] Warning: could not load state: {e}", file=sys.stderr)
        return {"sessions": {}}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.state_file_path), exist_ok=True)
            tmp_path = self.state_file_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.state_file_path)
        except Exception as e:
            print(f"[StateManager] Warning: could not save state: {e}", file=sys.stderr)

    def get_processed_message_ids(self, session_id: str) -> Set[str]:
        session_state = self.state.get("sessions", {}).get(session_id, {})
        return set(session_state.get("processed_message_ids", []))

    def record_processed_messages(self, session_id: str, message_ids: Set[str], last_turn_id: Optional[str] = None):
        if "sessions" not in self.state:
            self.state["sessions"] = {}
        if session_id not in self.state["sessions"]:
            self.state["sessions"][session_id] = {
                "processed_message_ids": [],
                "last_turn_id": None,
                "synced_turns": 0
            }

        curr_ids = set(self.state["sessions"][session_id]["processed_message_ids"])
        curr_ids.update(message_ids)
        self.state["sessions"][session_id]["processed_message_ids"] = list(curr_ids)
        if last_turn_id:
            self.state["sessions"][session_id]["last_turn_id"] = last_turn_id
        self.save()
