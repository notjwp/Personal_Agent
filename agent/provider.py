"""The model adapter (NFR-702).

This module exists because there are now TWO implementations behind one seam —
Anthropic's native API and any OpenAI-compatible endpoint (NVIDIA NIM by default).
CE-01 requires two callers or two implementations before a module is justified;
until the second provider arrived, this code correctly lived inside the act node.

The rest of the system speaks ONE message shape: Anthropic-style content blocks.
That shape is fixed by the state contract, and every node, test and trace already
uses it. The OpenAI-compatible provider translates at its own boundary and nowhere
else, so no other file learns that a second provider exists.
"""
import json
from dataclasses import dataclass

from agent import config as settings


@dataclass(frozen=True)
class Reply:
    """One model turn, normalised."""
    blocks: list[dict]          # Anthropic-shaped: {"type": "text"|"tool_use", ...}
    billed_tokens: int
    cache_read_tokens: int
    stop_reason: str | None


class MalformedToolCall(RuntimeError):
    """The model emitted a tool call whose arguments are not valid JSON.

    Raised loudly rather than patched over: native tool calling is a hard
    requirement, and the spec forbids parsing calls out of free text. A model that
    cannot do this reliably is the wrong model, and silently coercing bad arguments
    would hide that behind a mysteriously low pass rate.
    """


class ProviderUnavailable(RuntimeError):
    """The provider could not answer, for reasons unrelated to the agent: a rate
    limit, a timeout, a dropped connection, or a 5xx.

    Deliberately distinct from MalformedToolCall, where the model DID answer and
    answered badly - that is a result and must be scored. A run lost to this one
    measured nothing, so scoring it as a failed case would understate the agent
    and quietly corrupt every comparison made against that baseline.
    """


class ProviderMisconfigured(RuntimeError):
    """No key, a rejected key, or a forbidden model. Retrying cannot help, and
    fifteen of these in a row look exactly like an agent that can do nothing."""


# Both SDKs expose identical exception names, verified against the installed
# releases, so one table serves both without importing both to compare classes.
RETRYABLE = ("RateLimitError", "APITimeoutError", "APIConnectionError",
             "InternalServerError",
             # A 404 on the model endpoint. Measured mid-cycle: a run died at turn 0
             # with 0 tokens on NotFoundError, and the same model answered a probe
             # minutes later - so the endpoint had blinked, not vanished. Left
             # unclassified it fell through to "a crashed agent is a real result"
             # and was scored as a failed case, which is exactly what the standing
             # rule forbids: a run that never reached the model measured nothing.
             #
             # Retryable rather than fatal, deliberately. A genuinely wrong model
             # name fails every attempt, so the retries cost three quick 404s and
             # the run is then excluded as blocked - visible, and not counted. A
             # transient blink recovers on the first retry. Treating it as fatal
             # would abort a whole scored suite over one hiccup.
             "NotFoundError")
FATAL = ("AuthenticationError", "PermissionDeniedError")


def _reraise_classified(exc: Exception) -> None:
    """Re-raise as our taxonomy if this belongs to it; return otherwise.

    Returning (rather than raising something generic) is the important half: a
    BadRequestError is OUR bug and a MalformedToolCall is a real result, and
    excusing either as "the network" would hide a defect behind an excluded run.
    """
    name = type(exc).__name__
    if name in FATAL:
        raise ProviderMisconfigured(f"{name}: {exc}") from exc
    if name in RETRYABLE:
        raise ProviderUnavailable(f"{name}: {exc}") from exc


def call_model(messages: list[dict], system: str, tools: list[dict],
               on_text=None) -> Reply:
    """Run one model turn. The only place a provider SDK is imported."""
    provider = settings.PROVIDER
    if provider == "anthropic":
        return _call_anthropic(messages, system, tools, on_text)
    if provider in ("nvidia", "openai"):
        return _call_openai_compatible(messages, system, tools, on_text)
    raise ValueError(
        f"unknown AGENT_PROVIDER {provider!r}; expected anthropic, nvidia or openai")


# ------------------------------------------------------------------ anthropic

def _call_anthropic(messages, system, tools, on_text) -> Reply:
    import anthropic

    try:
        client = anthropic.Anthropic(
            timeout=settings.REQUEST_TIMEOUT,
            max_retries=settings.MAX_ATTEMPTS - 1,
        )
        with client.messages.stream(
            model=settings.MODEL,
            max_tokens=settings.MAX_TOKENS,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": settings.EFFORT},
            tools=tools,
            messages=messages,
        ) as stream:
            if on_text:
                for delta in stream.text_stream:
                    on_text(delta)
            response = stream.get_final_message()
    except Exception as exc:
        _reraise_classified(exc)   # rate limit / auth / 5xx become our taxonomy
        raise                      # anything else is ours, and stays loud

    usage = response.usage
    return Reply(
        # model_dump() keeps thinking blocks (signature included) intact for replay.
        blocks=[b.model_dump() for b in response.content],
        billed_tokens=usage.input_tokens + usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        stop_reason=getattr(response, "stop_reason", None),
    )


# ---------------------------------------------------- openai-compatible (NIM)

def _call_openai_compatible(messages, system, tools, on_text) -> Reply:
    import openai

    try:
        api_key = settings.openai_api_key()
    except RuntimeError as exc:
        # A missing key is configuration, not a failed run. Surfaced as such so the
        # suite aborts once instead of recording fifteen identical "failures".
        raise ProviderMisconfigured(str(exc)) from exc

    try:
        client = openai.OpenAI(
            base_url=settings.OPENAI_BASE_URL,
            api_key=api_key,
            # Without these the SDK waits 10 minutes per attempt and retries twice, so a
            # stalled endpoint costs half an hour of silence before anything is visible.
            timeout=settings.REQUEST_TIMEOUT,
            max_retries=settings.MAX_ATTEMPTS - 1,
        )
        # Deliberately no thinking, no effort, no cache_control: those are Anthropic-only
        # and sending them here produces confusing 400s rather than a clear failure.
        # `tools` is omitted rather than sent empty: several OpenAI-compatible
        # endpoints reject `tools: []` with a 400, and the planning phase's last
        # turn deliberately exposes none.
        request = dict(model=settings.OPENAI_MODEL,
                       max_tokens=settings.MAX_TOKENS,
                       messages=to_openai_messages(system, messages))
        if tools:
            request["tools"] = to_openai_tools(tools)
        response = client.chat.completions.create(**request)
    except Exception as exc:
        _reraise_classified(exc)
        raise
    choice = response.choices[0]
    blocks = from_openai_message(choice.message)

    if on_text:
        for block in blocks:
            if block["type"] == "text":
                on_text(block["text"])

    usage = response.usage
    return Reply(
        blocks=blocks,
        billed_tokens=(usage.prompt_tokens + usage.completion_tokens) if usage else 0,
        cache_read_tokens=0,        # no prompt caching on this path
        stop_reason=choice.finish_reason,
    )


# ------------------------------------------------------------- translation

def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Anthropic {name, description, input_schema} -> OpenAI function tool."""
    return [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t["input_schema"],
        },
    } for t in tools]


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Anthropic content blocks -> OpenAI chat messages.

    The asymmetry that matters: our single user message carrying N tool_result
    blocks must fan out into N separate `role: "tool"` messages, in order, each
    carrying its own tool_call_id. Collapsing them is rejected by the provider.
    """
    out: list[dict] = [{"role": "system", "content": system}]

    for message in messages:
        role, content = message["role"], message["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "".join(b.get("text", "") for b in content
                           if b.get("type") == "text")
            calls = [{
                "id": b["id"],
                "type": "function",
                "function": {"name": b["name"],
                             "arguments": json.dumps(b.get("input", {}))},
            } for b in content if b.get("type") == "tool_use"]
            # Unknown block types (thinking, redacted_thinking) are dropped: they
            # are Anthropic-only and meaningless to this provider.
            assistant = {"role": "assistant", "content": text or None}
            if calls:
                assistant["tool_calls"] = calls
            out.append(assistant)
            continue

        # user turn: tool results fan out, plain text stays one message
        for block in content:
            if block.get("type") == "tool_result":
                out.append({"role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": str(block.get("content", ""))})
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})

    return out


def from_openai_message(message) -> list[dict]:
    """OpenAI reply -> Anthropic content blocks."""
    blocks: list[dict] = []

    text = getattr(message, "content", None)
    if text:
        blocks.append({"type": "text", "text": text})

    for call in (getattr(message, "tool_calls", None) or []):
        raw = call.function.arguments or "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedToolCall(
                f"{settings.OPENAI_MODEL} returned unparseable arguments for "
                f"{call.function.name}: {raw!r}"
            ) from exc
        if not isinstance(arguments, dict):
            raise MalformedToolCall(
                f"{settings.OPENAI_MODEL} returned non-object arguments for "
                f"{call.function.name}: {raw!r}")
        blocks.append({"type": "tool_use", "id": call.id,
                       "name": call.function.name, "input": arguments})

    return blocks
