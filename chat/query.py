import json
from types import SimpleNamespace


def run_required_tool_call(client, messages, tools, temperature=0.4, token_budgets=None):
    for max_tokens in token_budgets or [200, 320]:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="required",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        msg = completion.choices[0].message
        if msg.tool_calls:
            return msg
    raise RuntimeError("Expected tool call but model did not return one.")


class Querier:
    def __init__(
        self,
        instructions,
        persona=None,
        tool=None,
        temperature=0.4,
        token_budgets=None,
    ):
        self.persona = persona
        self.tool = tool
        self.temperature = temperature
        self.token_budgets = token_budgets or [200, 320]
        self.instructions = instructions
        if persona:
            self.instructions = f"{instructions}\n" "Follow the persona provided in background_information."

    def run(self, client, input, system_context=None, token_budgets=None):
        prompt_parts = []
        if self.persona is not None or system_context is not None:
            background = {}
            if self.persona is not None:
                background["persona"] = self.persona
            if system_context is not None:
                background["system_context"] = system_context
            prompt_parts.extend(
                [
                    "<background_information>",
                    json.dumps(background, ensure_ascii=False),
                    "</background_information>",
                ]
            )
        prompt_parts.extend(
            [
                "<instructions>",
                self.instructions,
                "</instructions>",
                "## Tool guidance",
                "Use the given tool when appropriate; if a tool is configured, call it.",
                "## Output description",
                "Return a concise reply or required tool arguments.",
            ]
        )
        messages = [
            {"role": "system", "content": "\n".join(prompt_parts)},
            {"role": "user", "content": input},
        ]
        budgets = token_budgets or self.token_budgets
        for max_tokens in budgets:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=[self.tool] if self.tool else None,
                tool_choice=(
                    None
                    if not self.tool
                    else (
                        {
                            "type": "function",
                            "function": {"name": self.tool["function"]["name"]},
                        }
                        if self.tool.get("type") == "function"
                        else "auto"
                    )
                ),
                temperature=self.temperature,
                max_tokens=max_tokens,
            )
            msg = completion.choices[0].message
            if msg.tool_calls:
                call = msg.tool_calls[0]
                try:
                    args = json.loads(call.function.arguments or "{}")
                    if isinstance(args, dict):
                        return SimpleNamespace(arguments=args, response=None)
                except Exception:
                    continue
            if not self.tool:
                return SimpleNamespace(arguments=None, response=msg.content)
        if self.tool:
            raise RuntimeError("Expected tool call but model did not return one.")
        return SimpleNamespace(arguments=None, response="")
