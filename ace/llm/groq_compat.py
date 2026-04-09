"""
GroqCompat: extends browser-use's ChatGroq with json_object fallback + image stripping.

For models that don't support json_schema (e.g. qwen/qwen3-32b):
  - Injects the schema as a system prompt instruction and uses json_object mode.
  - Strips image content from messages (Qwen on Groq is text-only).

browser-use receives a valid JSON response and parses it normally.
"""
import json
from typing import TypeVar

from groq.types.chat import ChatCompletion
from pydantic import BaseModel

from browser_use.llm.groq.chat import ChatGroq, JsonSchemaModels

T = TypeVar('T', bound=BaseModel)

# Models on Groq that accept image content in messages
_VISION_MODELS = {
    'meta-llama/llama-4-scout-17b-16e-instruct',
    'meta-llama/llama-4-maverick-17b-128e-instruct',
    'openai/gpt-oss-20b',
    'openai/gpt-oss-120b',
}

_SCHEMA_INSTRUCTION = (
    "\n\n---\nYou MUST respond with a single JSON object that strictly matches "
    "this schema (no extra keys, no markdown, no explanation):\n{schema}"
)


class GroqCompat(ChatGroq):
    """
    Drop-in replacement for ChatGroq that:
    - Falls back to json_object mode for models without json_schema support.
    - Strips image content for models without vision support (e.g. qwen/qwen3-32b).
    """

    async def _invoke_with_json_schema(self, groq_messages, output_format: type[T], schema) -> ChatCompletion:
        if self.model in JsonSchemaModels:
            return await super()._invoke_with_json_schema(groq_messages, output_format, schema)

        messages = groq_messages
        if self.model not in _VISION_MODELS:
            messages = _strip_images(messages)

        messages = _inject_schema(messages, schema)

        return await self.get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            seed=self.seed,
            response_format={"type": "json_object"},
            service_tier=self.service_tier,
        )

    async def _invoke_regular_completion(self, groq_messages) -> ChatCompletion:
        messages = groq_messages
        if self.model not in _VISION_MODELS:
            messages = _strip_images(messages)
        return await super()._invoke_regular_completion(messages)


def _strip_images(groq_messages: list) -> list:
    """
    Convert any list-content messages to string by keeping only text parts.
    This makes messages compatible with text-only models like qwen/qwen3-32b.
    """
    result = []
    for msg in groq_messages:
        content = msg.get('content')
        if isinstance(content, list):
            text_parts = [
                part.get('text', '')
                for part in content
                if isinstance(part, dict) and part.get('type') == 'text'
            ]
            msg = dict(msg, content='\n'.join(text_parts))
        result.append(msg)
    return result


def _inject_schema(groq_messages: list, schema: dict) -> list:
    """
    Append the JSON schema instruction to the system message.
    If there's no system message, prepend one.
    """
    instruction = _SCHEMA_INSTRUCTION.format(schema=json.dumps(schema, separators=(',', ':')))
    messages = list(groq_messages)

    for i, msg in enumerate(messages):
        if msg.get('role') == 'system':
            messages[i] = dict(msg, content=str(msg.get('content', '')) + instruction)
            return messages

    messages.insert(0, {'role': 'system', 'content': instruction.lstrip()})
    return messages
