import os
import asyncio

from anthropic import Anthropic, InternalServerError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
    )
model = os.getenv("MODEL")


def is_retryable(exception) -> bool:
    return isinstance(exception, (InternalServerError, APIConnectionError, asyncio.TimeoutError))

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(is_retryable)
)
async def call_anthropic(messages: list, system: str = "") -> str:
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(client.messages.create, messages=messages, model=model, max_tokens=1024),
            timeout=10.0
        )    
        for block in response.content:
            if block.type == "text":
                return block.text
        raise ValueError("No text block in response")
        
    except asyncio.TimeoutError:
        raise
