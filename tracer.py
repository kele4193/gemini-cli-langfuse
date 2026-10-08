"""
tracer.py - Langfuse Trace Exporter for Gemini CLI.

Translates SessionRecord, TurnRecord, GenerationRecord, and ToolCallRecord
into Langfuse Traces, Generations, and Spans.
"""

import os
import sys
import json
import base64
import urllib.request
from typing import Dict, Any, List, Optional, Set
from transcript_parser import SessionRecord, TurnRecord, GenerationRecord, ToolCallRecord
from state_manager import StateManager

# Try importing official Langfuse SDK
try:
    from langfuse import Langfuse, propagate_attributes
    from opentelemetry import trace as trace_api
    from datetime import datetime
    HAS_LANGFUSE_SDK = True
except ImportError:
    HAS_LANGFUSE_SDK = False


def to_ns(iso_str: Optional[str]) -> Optional[int]:
    """Converts an ISO timestamp string to integer nanoseconds."""
    if not iso_str:
        return None
    try:
        clean_str = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        return int(dt.timestamp() * 1e9)
    except Exception:
        return None


def load_env_file(env_path: str, override: bool = False):
    """Simple parser for .env files without requiring external packages."""
    if not env_path or not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if override or (k not in os.environ):
                    os.environ[k] = v
    except Exception as e:
        print(f"[Tracer] Warning loading {env_path}: {e}", file=sys.stderr)


def load_json_config(json_path: str, override: bool = True):
    """Loads configuration from a langfuse.json file."""
    if not json_path or not os.path.exists(json_path):
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            return

        key_mappings = {
            "LANGFUSE_PUBLIC_KEY": ["publicKey", "public_key", "LANGFUSE_PUBLIC_KEY"],
            "LANGFUSE_SECRET_KEY": ["secretKey", "secret_key", "LANGFUSE_SECRET_KEY"],
            "LANGFUSE_BASE_URL": ["baseUrl", "base_url", "host", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"],
            "LANGFUSE_ENABLED": ["enabled", "LANGFUSE_ENABLED"],
        }

        for env_key, possible_keys in key_mappings.items():
            for pk in possible_keys:
                if pk in config:
                    val = str(config[pk])
                    if override or (env_key not in os.environ):
                        os.environ[env_key] = val
                    break
    except Exception as e:
        print(f"[Tracer] Warning loading {json_path}: {e}", file=sys.stderr)


class LangfuseTracer:
    """Exports parsed Gemini CLI sessions to Langfuse."""

    def __init__(self, cwd: Optional[str] = None, env_file: Optional[str] = None):
        target_dir = cwd or os.getcwd()

        # 1. Per-Project JSON configuration (Highest Priority)
        possible_project_jsons = [
            os.path.join(target_dir, "langfuse.json"),
            os.path.join(target_dir, ".gemini", "langfuse.json"),
        ]
        for pj in possible_project_jsons:
            if os.path.exists(pj):
                load_json_config(pj, override=True)
                break

        # 2. Per-Project .env file (High Priority, overrides shell env)
        possible_project_envs = [
            env_file,
            os.path.join(target_dir, ".env"),
            os.path.join(target_dir, ".gemini", ".env"),
        ]
        for pe in possible_project_envs:
            if pe and os.path.exists(pe):
                load_env_file(pe, override=True)
                break

        # 3. Global Fallback configuration (Does not override already set envs)
        fallback_envs = [
            os.path.expanduser("~/gemini-langfuse/langfuse.json"),
            os.path.expanduser("~/.gemini/langfuse.json"),
            os.path.expanduser("~/gemini-langfuse/.env"),
            os.path.expanduser("~/.gemini/langfuse.env"),
            os.path.expanduser("~/langfuse/.env"),
            os.path.expanduser("~/.env"),
        ]
        for p in fallback_envs:
            if p.endswith(".json"):
                load_json_config(p, override=False)
            else:
                load_env_file(p, override=False)

        self.public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        self.secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        self.host = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "http://localhost:3000"
        self.debug = os.getenv("LANGFUSE_DEBUG", "false").lower() in ("1", "true", "yes")

        self.client = None
        if self.public_key and self.secret_key and HAS_LANGFUSE_SDK:
            try:
                self.client = Langfuse(
                    public_key=self.public_key,
                    secret_key=self.secret_key,
                    host=self.host
                )
            except Exception as e:
                print(f"[Tracer] Warning initializing Langfuse SDK: {e}", file=sys.stderr)

    def is_configured(self) -> bool:
        return bool(self.public_key and self.secret_key)

    def export_session(self, session: SessionRecord, state_mgr: Optional[StateManager] = None) -> int:
        """
        Exports newly completed turns to Langfuse.
        Returns the number of turns exported.
        """
        if not self.is_configured():
            if self.debug:
                print("[Tracer] Langfuse credentials not configured, skipping export.", file=sys.stderr)
            return 0

        processed_ids: Set[str] = set()
        if state_mgr:
            processed_ids = state_mgr.get_processed_message_ids(session.session_id)

        synced_turns = 0
        newly_processed_ids: Set[str] = set()

        for turn in session.turns:
            # Skip turns that have no model response yet
            if not turn.generations:
                continue

            # Check if all generations in this turn were already processed
            turn_gen_ids = {g.id for g in turn.generations}
            if turn_gen_ids.issubset(processed_ids):
                continue

            try:
                if self.client:
                    self._export_turn_sdk(session, turn)
                else:
                    self._export_turn_http(session, turn)

                synced_turns += 1
                newly_processed_ids.update(turn_gen_ids)
                newly_processed_ids.add(turn.user_message_id)

            except Exception as e:
                print(f"[Tracer] Error exporting turn {turn.turn_id}: {e}", file=sys.stderr)

        # Flush SDK queue if using SDK
        if self.client and synced_turns > 0:
            try:
                self.client.flush()
            except Exception as e:
                print(f"[Tracer] Error flushing Langfuse SDK: {e}", file=sys.stderr)

        # Persist state
        if state_mgr and newly_processed_ids:
            state_mgr.record_processed_messages(
                session.session_id,
                newly_processed_ids,
                last_turn_id=session.turns[-1].turn_id if session.turns else None
            )

        return synced_turns

    def _export_turn_sdk(self, session: SessionRecord, turn: TurnRecord):
        """Export a conversational turn using the official Langfuse v4 SDK with backdated timestamps."""
        prompt_snippet = turn.user_prompt.strip().replace("\n", " ")[:60]
        turn_start_ns = to_ns(turn.start_time)
        turn_end_ns = to_ns(turn.end_time)

        with propagate_attributes(
            session_id=session.session_id,
            trace_name=f"Gemini: {prompt_snippet}...",
            user_id=os.getenv("USER", "gemini-user"),
            tags=["gemini-cli"],
            metadata={
                "project_hash": session.project_hash,
                "user_message_id": turn.user_message_id,
                "timestamp": turn.timestamp,
            }
        ):
            otel_turn = self.client._otel_tracer.start_span(
                name=f"Turn: {prompt_snippet}...",
                start_time=turn_start_ns
            )
            turn_obs = self.client._create_observation_from_otel_span(
                otel_span=otel_turn,
                as_type="span",
                input={"prompt": turn.user_prompt}
            )

            conversation_history = [
                {"role": "user", "content": turn.user_prompt}
            ]

            for gen in turn.generations:
                gen_start_ns = to_ns(gen.start_time) or turn_start_ns
                gen_end_ns = to_ns(gen.end_time) or gen_start_ns

                output_content = gen.content
                if not output_content and gen.tool_calls:
                    tool_summaries = [f"{tc.name}({json.dumps(tc.args, ensure_ascii=False)})" for tc in gen.tool_calls]
                    output_content = f"[Tool Call] " + "; ".join(tool_summaries)

                usage_dict = {
                    "input": gen.tokens.get("input", 0),
                    "output": gen.tokens.get("output", 0),
                    "total": gen.tokens.get("total", 0),
                }

                gen_input = list(conversation_history)

                with trace_api.use_span(otel_turn, end_on_exit=False):
                    otel_gen = self.client._otel_tracer.start_span(
                        name=f"Gemini ({gen.model})",
                        start_time=gen_start_ns
                    )
                    gen_obs = self.client._create_observation_from_otel_span(
                        otel_span=otel_gen,
                        as_type="generation",
                        model=gen.model,
                        input=gen_input,
                        output=output_content,
                        usage_details=usage_dict,
                        metadata={
                            "thoughts": gen.thoughts,
                            "tokens": gen.tokens,
                        }
                    )

                    for tc in gen.tool_calls:
                        tc_start_ns = to_ns(tc.start_time) or gen_start_ns
                        tc_end_ns = to_ns(tc.end_time) or gen_end_ns

                        with trace_api.use_span(otel_gen, end_on_exit=False):
                            otel_tool = self.client._otel_tracer.start_span(
                                name=f"Tool: {tc.name}",
                                start_time=tc_start_ns
                            )
                            tool_obs = self.client._create_observation_from_otel_span(
                                otel_span=otel_tool,
                                as_type="tool",
                                input=tc.args,
                                output=tc.output,
                                metadata={"status": tc.status}
                            )
                            tool_obs.end(end_time=tc_end_ns)

                    gen_obs.end(end_time=gen_end_ns)

                # Append assistant tool call and results to context for next generation
                asst_msg = {
                    "role": "assistant",
                    "content": gen.content or ""
                }
                if gen.tool_calls:
                    asst_msg["tool_calls"] = [
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.args}}
                        for tc in gen.tool_calls
                    ]
                conversation_history.append(asst_msg)

                for tc in gen.tool_calls:
                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": str(tc.output) if tc.output is not None else ""
                    })

            if turn.generations and turn.generations[-1].content:
                turn_obs.update(output=turn.generations[-1].content)

            turn_obs.end(end_time=turn_end_ns)

    def _export_turn_http(self, session: SessionRecord, turn: TurnRecord):
        """Fallback HTTP ingestion for when the official Python SDK is not installed."""
        prompt_snippet = turn.user_prompt.strip().replace("\n", " ")[:60]
        trace_id = f"{session.session_id}_{turn.turn_id}"

        batch_events = []

        # 1. Trace create
        batch_events.append({
            "id": f"event_trace_{trace_id}",
            "type": "trace-create",
            "timestamp": turn.timestamp,
            "body": {
                "id": trace_id,
                "name": f"Gemini: {prompt_snippet}...",
                "sessionId": session.session_id,
                "userId": os.getenv("USER", "gemini-user"),
                "tags": ["gemini-cli"],
                "input": {"prompt": turn.user_prompt},
                "metadata": {
                    "project_hash": session.project_hash,
                    "user_message_id": turn.user_message_id,
                    "timestamp": turn.timestamp,
                }
            }
        })

        # 2. Generations & Tool Spans
        conversation_history = [
            {"role": "user", "content": turn.user_prompt}
        ]

        for gen in turn.generations:
            gen_id = f"gen_{gen.id}"
            usage_dict = {
                "input": gen.tokens.get("input", 0),
                "output": gen.tokens.get("output", 0),
                "total": gen.tokens.get("total", 0),
            }

            output_content = gen.content
            if not output_content and gen.tool_calls:
                tool_summaries = [f"{tc.name}({json.dumps(tc.args, ensure_ascii=False)})" for tc in gen.tool_calls]
                output_content = f"[Tool Call] " + "; ".join(tool_summaries)

            gen_input = list(conversation_history)

            batch_events.append({
                "id": f"event_{gen_id}",
                "type": "generation-create",
                "timestamp": gen.timestamp,
                "body": {
                    "id": gen_id,
                    "traceId": trace_id,
                    "name": f"Gemini ({gen.model})",
                    "model": gen.model,
                    "input": gen_input,
                    "output": output_content,
                    "startTime": gen.timestamp,
                    "endTime": gen.timestamp,
                    "usage": usage_dict,
                    "metadata": {
                        "thoughts": gen.thoughts,
                        "token_details": gen.tokens
                    }
                }
            })

            for tc in gen.tool_calls:
                tool_id = f"tool_{tc.id}"
                batch_events.append({
                    "id": f"event_{tool_id}",
                    "type": "span-create",
                    "timestamp": gen.timestamp,
                    "body": {
                        "id": tool_id,
                        "traceId": trace_id,
                        "parentObservationId": gen_id,
                        "name": f"Tool: {tc.name}",
                        "startTime": gen.timestamp,
                        "endTime": gen.timestamp,
                        "input": tc.args,
                        "output": tc.output,
                        "metadata": {"status": tc.status}
                    }
                })

            asst_msg = {
                "role": "assistant",
                "content": gen.content or ""
            }
            if gen.tool_calls:
                asst_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.args}}
                    for tc in gen.tool_calls
                ]
            conversation_history.append(asst_msg)

            for tc in gen.tool_calls:
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": str(tc.output) if tc.output is not None else ""
                })

        # Send HTTP request
        url = f"{self.host.rstrip('/')}/api/public/ingestion"
        auth_str = f"{self.public_key}:{self.secret_key}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

        req = urllib.request.Request(
            url,
            data=json.dumps({"batch": batch_events}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {b64_auth}",
                "User-Agent": "gemini-cli-langfuse/1.0"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            if self.debug:
                print(f"[Tracer] HTTP Ingestion Response: {resp.status}", file=sys.stderr)
