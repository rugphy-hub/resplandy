"""One function, complete(), that sends a prompt using whichever API key you pasted.

The key decides where it goes:
    sk-or-...   OpenRouter (the key the workshop gives you)
    AIza...     Google AI Studio (has a free tier)
    sk-ant-...  Anthropic
    other       OpenAI
"""
from __future__ import annotations

from .config import Settings

TIMEOUT_SECONDS = 90.0


class LLMError(Exception):
    """A problem worth showing to the user, such as a wrong key or no credit."""


def complete(settings: Settings, system: str, user: str, max_tokens: int = 8000) -> str:
    if not settings.llm_api_key:
        raise LLMError("No API key is set. Run: python setup_workshop.py (in Colab: run the key cell).")
    if settings.provider == "anthropic":
        return _anthropic(settings, system, user, max_tokens)
    return _openai_compatible(settings, system, user, max_tokens)


def _anthropic(settings: Settings, system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.llm_api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
    try:
        response = client.messages.create(
            model=settings.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError:
        raise LLMError("Anthropic rejected the API key. Check it with: python setup_workshop.py") from None
    except anthropic.NotFoundError:
        raise LLMError(f"Anthropic does not recognise the model '{settings.model}'. Change LLM_MODEL in .env.") from None
    except anthropic.RateLimitError:
        raise LLMError("Anthropic rate limit reached. Wait a minute and try again.") from None
    except anthropic.APIStatusError as err:
        raise LLMError(f"Anthropic returned an error (HTTP {err.status_code}). Check your account credit.") from None
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach Anthropic. Check your internet connection.") from None
    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _openai_compatible(settings: Settings, system: str, user: str, max_tokens: int) -> str:
    """OpenAI itself, plus OpenRouter and Google, which speak the same API."""
    import openai

    name = settings.provider_name
    client = openai.OpenAI(api_key=settings.llm_api_key, base_url=settings.base_url,
                           timeout=TIMEOUT_SECONDS, max_retries=1)
    try:
        if settings.base_url:      # OpenRouter, Google or another compatible server
            response = client.chat.completions.create(
                model=settings.model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return (response.choices[0].message.content or "").strip()
        response = client.responses.create(
            model=settings.model, instructions=system, input=user, max_output_tokens=max_tokens,
        )
        return (response.output_text or "").strip()
    except openai.AuthenticationError:
        raise LLMError(f"{name} rejected the API key. Check it with: python setup_workshop.py") from None
    except openai.NotFoundError:
        raise LLMError(f"{name} does not recognise the model '{settings.model}'. Change LLM_MODEL in .env.") from None
    except openai.RateLimitError:
        raise LLMError(f"{name} rate limit or quota reached. Check your credit.") from None
    except openai.APIStatusError as err:
        raise LLMError(f"{name} returned an error (HTTP {err.status_code}).") from None
    except openai.APIConnectionError:
        raise LLMError(f"Could not reach {name}. Check your internet connection.") from None
