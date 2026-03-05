"""Conversational agent — Claude-powered natural language interface to Atlas.

Handles @mentions and DMs in Slack, interprets user intent, calls pipeline
tools, manages suggest+confirm flow, and logs all interactions.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import aiosqlite
import structlog
from anthropic import AsyncAnthropic

from app.core.clients.ghl import GHLClient
from app.models.database import InteractionRepository
from app.modules.audit.rules import FIELD_NAMES
from app.modules.autonomy.confidence import record_approval, record_rejection, record_suggestion
from app.modules.conversation.tools import TOOL_DEFINITIONS, execute_tool

log = structlog.get_logger()

SYSTEM_PROMPT = """\
You are Atlas, a pipeline intelligence agent for Alternative Horizons Group (AHG), \
a payment processing company. You help the sales team manage their GHL (GoHighLevel) \
pipeline by answering questions about deal status, audit findings, and missing data.

Your capabilities:
- Answer pipeline questions: stale deals, missing fields, overdue tasks
- Show issues for specific opportunities or team members
- Suggest fixes for missing fields and confirm before applying
- Provide system health status and audit trends
- Undo recent auto-fixes if something was wrong
- Show confidence scores for fix types

What runs automatically (without being asked):
- Daily pipeline audit at 8 AM EST (weekdays) — results posted to Slack
- Calendly webhook processing — cancellations and no-shows update GHL fields
- Subscription health checks every 6 hours
- Graduated autonomy: when you approve enough fixes of a certain type (>90% \
  approval for 2+ weeks), Atlas auto-applies that fix type and reports in the \
  daily digest. You can undo any auto-fix by asking.

Communication style:
- Be concise and direct — this is a sales team, not a chatbot demo
- Use bullet points for lists
- When suggesting fixes, always ask for confirmation before applying
- Reference specific opportunity names and field names
- If you don't have data, say so and suggest running an audit
- When showing issues, group by opportunity — don't list every field separately

You ONLY respond to pipeline-related queries. If someone asks about something \
unrelated, politely redirect to pipeline topics.

When a user approves a suggested fix (says "yes", "approve", "do it", etc.), \
look for the most recent PENDING_FIX in the conversation and apply it.
"""


class ConversationAgent:
    """Manages Claude-powered conversations with tool use."""

    def __init__(
        self,
        anthropic_client: AsyncAnthropic,
        ghl_client: GHLClient,
        db: aiosqlite.Connection,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self.client = anthropic_client
        self.ghl_client = ghl_client
        self.db = db
        self.model = model
        self.interaction_repo = InteractionRepository(db)
        # Per-channel conversation history (last N messages)
        self._history: dict[str, list[dict]] = {}
        self._max_history = 20

    def _get_history(self, channel_id: str) -> list[dict]:
        """Get conversation history for a channel/DM."""
        if channel_id not in self._history:
            self._history[channel_id] = []
        return self._history[channel_id]

    def _add_to_history(self, channel_id: str, role: str, content: str) -> None:
        """Add a message to channel history."""
        history = self._get_history(channel_id)
        history.append({"role": role, "content": content})
        # Trim to max
        if len(history) > self._max_history:
            self._history[channel_id] = history[-self._max_history:]

    async def handle_message(
        self,
        text: str,
        user_id: str,
        channel_id: str,
    ) -> str:
        """Process a user message and return Atlas's response.

        Handles the full tool-use loop: Claude may call tools, we execute them,
        feed results back, until Claude produces a final text response.
        """
        # Strip @Atlas mention from text
        clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        if not clean_text:
            return "How can I help with the pipeline?"

        log.info(
            "conversation_message",
            user=user_id,
            channel=channel_id,
            text_length=len(clean_text),
        )

        # Check if this is an approval of a pending fix
        approval_response = await self._check_approval(clean_text, user_id, channel_id)
        if approval_response:
            return approval_response

        # Add user message to history
        self._add_to_history(channel_id, "user", clean_text)

        # Build messages for Claude
        messages = self._get_history(channel_id).copy()

        # Tool use loop
        max_iterations = 5
        for _ in range(max_iterations):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            log.info(
                "claude_conversation_response",
                stop_reason=response.stop_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            if response.stop_reason == "end_turn":
                # Extract text from response
                text_parts = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ]
                final_text = "\n".join(text_parts) if text_parts else "I couldn't generate a response."
                self._add_to_history(channel_id, "assistant", final_text)

                # Log interaction
                await self.interaction_repo.add(
                    interaction_type="conversation",
                    user_id=user_id,
                    channel_id=channel_id,
                    context=json.dumps({
                        "user_message": clean_text,
                        "atlas_response": final_text[:500],
                    }),
                )

                return final_text

            elif response.stop_reason == "tool_use":
                # Execute tool calls
                # Add assistant message with tool use blocks
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        log.info("tool_call", tool=block.name, input=block.input)
                        result = await execute_tool(
                            block.name,
                            block.input,
                            self.ghl_client,
                            self.db,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                        # If this is a suggest_fix, store pending fix and record suggestion
                        if block.name == "suggest_fix" and "PENDING_FIX:" in result:
                            self._store_pending_fix(channel_id, result, block.input)
                            await record_suggestion(self.db, block.input.get("field_name", ""))

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason
                log.warning("unexpected_stop_reason", reason=response.stop_reason)
                return "I encountered an issue processing your request. Please try again."

        return "I ran into a loop processing your request. Could you rephrase?"

    def _store_pending_fix(self, channel_id: str, result: str, tool_input: dict) -> None:
        """Store a pending fix suggestion for later approval."""
        if not hasattr(self, "_pending_fixes"):
            self._pending_fixes: dict[str, dict] = {}

        # Parse PENDING_FIX line
        for line in result.split("\n"):
            if line.startswith("PENDING_FIX:"):
                parts = line.split(":")
                if len(parts) >= 4:
                    self._pending_fixes[channel_id] = {
                        "opp_id": parts[1],
                        "field_id": parts[2],
                        "new_value": ":".join(parts[3:]),  # value may contain colons
                        "field_name": tool_input.get("field_name", ""),
                        "reason": tool_input.get("reason", ""),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                    log.info(
                        "pending_fix_stored",
                        channel=channel_id,
                        opp_id=parts[1],
                        field=tool_input.get("field_name"),
                    )

    async def _check_approval(self, text: str, user_id: str, channel_id: str) -> str | None:
        """Check if user is approving a pending fix. Returns response or None."""
        if not hasattr(self, "_pending_fixes"):
            return None

        pending = self._pending_fixes.get(channel_id)
        if not pending:
            return None

        lower = text.lower().strip()
        approve_words = {"yes", "y", "approve", "do it", "apply", "go ahead", "confirm", "ok", "yep", "yeah"}
        reject_words = {"no", "n", "skip", "cancel", "reject", "nah", "nope", "nevermind"}

        if lower in approve_words:
            await record_approval(self.db, pending["field_name"])
            return await self._apply_fix(pending, user_id, channel_id)
        elif lower in reject_words:
            await record_rejection(self.db, pending["field_name"])
            del self._pending_fixes[channel_id]
            await self.interaction_repo.add(
                interaction_type="fix_rejected",
                user_id=user_id,
                channel_id=channel_id,
                opportunity_id=pending["opp_id"],
                field_name=pending["field_name"],
                new_value=pending["new_value"],
                context=json.dumps({"reason": "User rejected suggestion"}),
            )
            return "Got it, skipping that fix."

        # Not a clear approval/rejection — clear pending and let conversation continue
        return None

    async def _apply_fix(self, pending: dict, user_id: str, channel_id: str) -> str:
        """Apply an approved fix to GHL."""
        opp_id = pending["opp_id"]
        field_id = pending["field_id"]
        new_value = pending["new_value"]
        field_name = pending["field_name"]

        try:
            # Read current value first
            opp = await self.ghl_client.get_opportunity(opp_id)
            opp_name = opp.get("name", "Unknown")

            # Get current value for logging
            old_value = None
            custom_fields = opp.get("customFields")
            if isinstance(custom_fields, list):
                for cf in custom_fields:
                    if isinstance(cf, dict) and cf.get("id") == field_id:
                        old_value = cf.get("value")

            # Apply the update
            await self.ghl_client.update_opportunity(
                opp_id,
                {"customFields": [{
                    "id": field_id,
                    "field_value": new_value,
                }]},
            )

            # Verify the write
            updated_opp = await self.ghl_client.get_opportunity(opp_id)
            verified = False
            updated_fields = updated_opp.get("customFields")
            if isinstance(updated_fields, list):
                for cf in updated_fields:
                    if isinstance(cf, dict) and cf.get("id") == field_id:
                        if str(cf.get("value", "")).strip() == new_value.strip():
                            verified = True

            # Log the interaction
            await self.interaction_repo.add(
                interaction_type="fix_approved",
                user_id=user_id,
                channel_id=channel_id,
                opportunity_id=opp_id,
                field_name=field_name,
                old_value=str(old_value) if old_value else None,
                new_value=new_value,
                context=json.dumps({
                    "reason": pending.get("reason", ""),
                    "verified": verified,
                    "opp_name": opp_name,
                }),
            )

            # Clean up pending
            del self._pending_fixes[channel_id]

            log.info(
                "fix_applied",
                opp_id=opp_id,
                field=field_name,
                verified=verified,
                user=user_id,
            )

            verify_str = " (verified)" if verified else " (verification pending)"
            return f"Done! Updated {field_name} to \"{new_value}\" on {opp_name}{verify_str}."

        except Exception as e:
            log.error("fix_apply_error", opp_id=opp_id, error=str(e))
            # Log the failed attempt
            await self.interaction_repo.add(
                interaction_type="fix_error",
                user_id=user_id,
                channel_id=channel_id,
                opportunity_id=opp_id,
                field_name=field_name,
                new_value=new_value,
                context=json.dumps({"error": str(e)}),
            )
            del self._pending_fixes[channel_id]
            return f"Failed to apply fix: {e}"
