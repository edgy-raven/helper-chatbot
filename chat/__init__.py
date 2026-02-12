import json
import logging

from openai import OpenAI
from .judges import PERSONA, PersonaRewriteJudge, SummaryRewriteJudge
from .query import Querier, run_required_tool_call
from .rag import lookup_key_text_context

CLIENT = None
logger = logging.getLogger("ibis.chat")

TOOLS = []
TOOL_HANDLERS = {}

SUMMARY_JUDGE = SummaryRewriteJudge()
PERSONA_REWRITE_JUDGE = PersonaRewriteJudge()
RESPOND_NORMALLY_QUERIER = Querier(
    instructions=(
        "Respond naturally to the user's latest message.\n"
        "Use recent_messages, user.conversation_summary, and global_memory when relevant.\n"
        "When retrieved_context contains directly relevant retrieved evidence, treat it as the factual source "
        "and prioritize it over stale summary/global_memory if they conflict.\n"
        "Base claims on concrete retrieved evidence when possible.\n"
        "Avoid repeating things from the conversation or global_memory when possible. \n"
        "Make the conversation feel natural in the context provided."
    ),
    persona=PERSONA,
)


def initialize_connection(keyring):
    global CLIENT
    CLIENT = OpenAI(api_key=keyring["openai_api_key"])


def register_tool(description, parameters, name=None):
    def decorator(fn):
        tool_name = name or fn.__name__
        TOOLS.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
        TOOL_HANDLERS[tool_name] = fn
        return fn

    return decorator


class ConversationContext:
    def __init__(
        self,
        current_time,
        user,
        discord_username,
        input_text,
        discord_id,
        global_memory="",
        recent_messages=None,
    ):
        self.current_time = current_time
        self.user = user
        self.discord_username = discord_username
        self.input_text = input_text
        self.discord_id = discord_id
        self.global_memory = global_memory
        self.recent_messages = recent_messages
        self.retrieved_context = {}

    def to_system_context(self):
        return {
            "current_time": self.current_time,
            "user": self.user,
            "discord_username": self.discord_username,
            "input_text": self.input_text,
            "discord_id": self.discord_id,
            "global_memory": self.global_memory,
            "recent_messages": self.recent_messages,
            "retrieved_context": self.retrieved_context,
        }

    def chat(self):
        self.retrieved_context = lookup_key_text_context(CLIENT, self.to_system_context())

        context_payload = self.to_system_context()
        context_json = json.dumps(context_payload, ensure_ascii=False, indent=2, sort_keys=True)
        logger.info(
            "prechat_retrieved_context\n%s",
            json.dumps(self.retrieved_context, ensure_ascii=False, indent=2, sort_keys=True),
        )
        logger.info("Received chat message.\n%s", context_json)
        msg = run_required_tool_call(
            client=CLIENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Context JSON: {context_json}\n\n"
                        "Use registered tools when they apply to the user's request. "
                        "If no tool applies, call the respond_normally tool."
                        "You MUST call a tool."
                    ),
                },
                {
                    "role": "user",
                    "content": self.input_text,
                },
            ],
            tools=TOOLS,
            temperature=0.4,
        )
        actions = []
        for call in msg.tool_calls:
            raw_args = call.function.arguments or "{}"
            args = json.loads(raw_args)
            logger.info(
                "tool_call %s\n%s",
                call.function.name,
                json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True),
            )
            actions.append(TOOL_HANDLERS[call.function.name](self, **args))
        response = "\n".join(actions)
        reply = PERSONA_REWRITE_JUDGE.revise(CLIENT, response, context_payload)

        turn_text = f"{self.discord_username}: {self.input_text}\nXander: {reply}"
        summarize_context = {
            "prior_summary": self.user["conversation_summary"],
            "prior_profile": self.user["profile"],
            "prior_global_memory": self.global_memory,
            "turn_text": turn_text,
        }
        payload = SUMMARY_JUDGE.revise(CLIENT, None, summarize_context)
        prev_summary = self.user["conversation_summary"]
        prev_global = self.global_memory
        prev_profile = dict(self.user["profile"])
        logger.info(
            "context_update payload\n%s",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )
        self.user["conversation_summary"] = payload.get("summary") or self.user["conversation_summary"]
        self.user["profile"].update(payload.get("profile_updates") or {})
        self.global_memory = payload.get("global_memory") or self.global_memory

        logger.info(
            "context_update applied\nsummary_before=%s\nsummary_after=%s\nglobal_before=%s\nglobal_after=%s\nprofile_before=%s\nprofile_after=%s",
            prev_summary,
            self.user["conversation_summary"],
            prev_global,
            self.global_memory,
            json.dumps(prev_profile, ensure_ascii=False, sort_keys=True),
            json.dumps(self.user["profile"], ensure_ascii=False, sort_keys=True),
        )
        return reply


@register_tool(
    description="Return a natural language reply when no tool action is needed.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def respond_normally(context):
    result = RESPOND_NORMALLY_QUERIER.run(
        CLIENT,
        system_context=context.to_system_context(),
        input=context.input_text,
    )
    return (result.response or "").strip()


__all__ = [
    "ConversationContext",
    "initialize_connection",
    "register_tool",
]
