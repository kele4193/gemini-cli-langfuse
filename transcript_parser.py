"""
transcript_parser.py - Parser for Gemini CLI session transcripts.

Gemini CLI stores session logs in JSONL format:
~/.gemini/tmp/<workspace_hash>/chats/session-<timestamp>-<session_id_suffix>.jsonl
"""

import json
import os
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolCallRecord:
    id: str
    name: str
    args: Dict[str, Any]
    output: Any = None
    status: str = "success"
    start_time: Optional[str] = None
    end_time: Optional[str] = None


@dataclass
class GenerationRecord:
    id: str
    timestamp: str
    model: str
    content: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    thoughts: List[Dict[str, Any]] = field(default_factory=list)
    tokens: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)


@dataclass
class TurnRecord:
    turn_id: str
    user_message_id: str
    user_prompt: str
    timestamp: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    generations: List[GenerationRecord] = field(default_factory=list)


@dataclass
class SessionRecord:
    session_id: str
    project_hash: Optional[str] = None
    start_time: Optional[str] = None
    last_updated: Optional[str] = None
    turns: List[TurnRecord] = field(default_factory=list)


class TranscriptParser:
    """Parses a Gemini CLI session JSONL transcript into structured turns and generations with accurate timestamps."""

    @staticmethod
    def parse_file(file_path: str) -> Optional[SessionRecord]:
        if not os.path.exists(file_path):
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        return TranscriptParser.parse_lines(lines)

    @staticmethod
    def parse_lines(lines: List[str]) -> SessionRecord:
        session_id = "unknown"
        project_hash = None
        start_time = None
        last_updated = None
        messages_by_id: Dict[str, Dict[str, Any]] = {}
        tool_end_times: Dict[str, str] = {}

        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except Exception:
                continue

            # Header metadata
            if "sessionId" in data:
                session_id = data.get("sessionId", session_id)
                project_hash = data.get("projectHash", project_hash)
                start_time = data.get("startTime", start_time)
                last_updated = data.get("lastUpdated", last_updated)

            # Record tool response timestamps
            if data.get("type") == "user":
                content = data.get("content", [])
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and "functionResponse" in part:
                            fr_id = part["functionResponse"].get("id")
                            if fr_id:
                                tool_end_times[fr_id] = data.get("timestamp")

            # Bulk messages in $set
            if "$set" in data and "messages" in data["$set"]:
                for msg in data["$set"]["messages"]:
                    if isinstance(msg, dict) and "id" in msg:
                        messages_by_id[msg["id"]] = msg

            # Individual message update/append
            elif "id" in data and "type" in data:
                messages_by_id[data["id"]] = data

        turns: List[TurnRecord] = []
        current_turn: Optional[TurnRecord] = None
        turn_counter = 0

        for mid, msg in messages_by_id.items():
            msg_type = msg.get("type")
            content = msg.get("content")
            msg_ts = msg.get("timestamp", datetime.utcnow().isoformat() + "Z")

            # 1. User turn boundary check
            if msg_type == "user":
                is_function_response = False
                text_parts = []

                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            if "functionResponse" in part:
                                is_function_response = True
                            elif "text" in part:
                                text_parts.append(part["text"])
                elif isinstance(content, str):
                    text_parts.append(content)

                prompt_text = "".join(text_parts).strip()

                if not is_function_response and prompt_text:
                    turn_counter += 1
                    current_turn = TurnRecord(
                        turn_id=f"turn-{turn_counter}-{mid[:8]}",
                        user_message_id=mid,
                        user_prompt=prompt_text,
                        timestamp=msg_ts,
                        start_time=msg_ts,
                        end_time=msg_ts,
                        generations=[]
                    )
                    turns.append(current_turn)
                continue

            # 2. Assistant (Gemini) response
            if msg_type == "gemini":
                if current_turn is None:
                    turn_counter += 1
                    current_turn = TurnRecord(
                        turn_id=f"turn-{turn_counter}-{mid[:8]}",
                        user_message_id="initial",
                        user_prompt="(System Init / Session Start)",
                        timestamp=msg_ts,
                        start_time=msg_ts,
                        end_time=msg_ts,
                        generations=[]
                    )
                    turns.append(current_turn)

                # Determine generation start time from previous step or turn start
                gen_start = msg_ts
                if current_turn.generations:
                    # Previous generation's end time is this generation's start
                    gen_start = current_turn.generations[-1].end_time or msg_ts
                else:
                    gen_start = current_turn.start_time or msg_ts

                tool_calls: List[ToolCallRecord] = []
                max_tool_end = msg_ts

                for tc in msg.get("toolCalls", []):
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("name", "unknown_tool")
                    tc_args = tc.get("args", {})
                    output = None

                    results = tc.get("result", [])
                    if results and isinstance(results, list):
                        res_obj = results[0]
                        if isinstance(res_obj, dict) and "functionResponse" in res_obj:
                            output = res_obj["functionResponse"].get("response", {}).get("output")
                        else:
                            output = res_obj

                    tc_end = tool_end_times.get(tc_id, msg_ts)
                    if tc_end > max_tool_end:
                        max_tool_end = tc_end

                    tool_calls.append(ToolCallRecord(
                        id=tc_id,
                        name=tc_name,
                        args=tc_args,
                        output=output,
                        status="success" if output is not None else "pending",
                        start_time=msg_ts,
                        end_time=tc_end
                    ))

                # Generation end time is either when tool finished, or generation timestamp
                gen_end = max_tool_end if tool_calls else msg_ts

                thoughts = msg.get("thoughts", [])
                tokens = msg.get("tokens", {})

                generation = GenerationRecord(
                    id=mid,
                    timestamp=msg_ts,
                    start_time=gen_start,
                    end_time=gen_end,
                    model=msg.get("model", "gemini"),
                    content=msg.get("content", "") or "",
                    thoughts=thoughts,
                    tokens=tokens,
                    tool_calls=tool_calls
                )
                current_turn.generations.append(generation)
                current_turn.end_time = gen_end

        return SessionRecord(
            session_id=session_id,
            project_hash=project_hash,
            start_time=start_time,
            last_updated=last_updated,
            turns=turns
        )
