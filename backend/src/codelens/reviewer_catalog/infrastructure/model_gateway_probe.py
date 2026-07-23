"""Infrastructure adapter that probes gateway reachability and LLM availability."""

import asyncio
import time
from urllib.parse import urlsplit

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from codelens.reviewer_catalog.domain.provider_config import (
    GatewayAvailabilityResult,
    GatewayConnectivityResult,
    ModelGatewayProbePort,
    ModelProviderConfig,
)

_CONNECTIVITY_TIMEOUT_SECONDS = 5
_AVAILABILITY_TIMEOUT_SECONDS = 15


class OpenAIModelGatewayProbeAdapter(ModelGatewayProbePort):
    """Test gateway TCP reachability and LLM availability without persisting state."""

    async def test_connectivity(self, base_url: str) -> GatewayConnectivityResult:
        """Attempt a bare TCP connection to the host and port parsed from ``base_url``."""

        parsed = urlsplit(base_url)
        host = parsed.hostname
        if not host:
            return GatewayConnectivityResult(
                ok=False,
                latency_ms=None,
                detail="The base URL does not contain a valid hostname.",
            )
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        start = time.monotonic()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=False),
                timeout=_CONNECTIVITY_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayConnectivityResult(
                ok=False,
                latency_ms=elapsed,
                detail=f"TCP connection to {host}:{port} timed out.",
            )
        except OSError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayConnectivityResult(
                ok=False,
                latency_ms=elapsed,
                detail=f"TCP connection to {host}:{port} failed: {exc.__class__.__name__}.",
            )
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        elapsed = int((time.monotonic() - start) * 1000)
        return GatewayConnectivityResult(
            ok=True,
            latency_ms=elapsed,
            detail=f"TCP connection to {host}:{port} succeeded.",
        )

    async def test_availability(
        self, config: ModelProviderConfig
    ) -> GatewayAvailabilityResult:
        """Send a minimal chat completion to verify the LLM can respond."""

        client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        start = time.monotonic()
        try:
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=config.model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ),
                timeout=_AVAILABILITY_TIMEOUT_SECONDS,
            )
        except APITimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayAvailabilityResult(
                ok=False,
                latency_ms=elapsed,
                detail="Request to the LLM timed out.",
            )
        except APIConnectionError:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayAvailabilityResult(
                ok=False,
                latency_ms=elapsed,
                detail="Connection to the LLM endpoint failed.",
            )
        except APIStatusError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            status = exc.status_code
            if status == 401:
                detail = "Authentication failed — verify the API key."
            elif status == 404:
                detail = "Model not found — verify the model identifier."
            elif status == 429:
                detail = "Rate limit exceeded — try again later."
            else:
                detail = f"LLM endpoint returned HTTP {status}."
            return GatewayAvailabilityResult(
                ok=False,
                latency_ms=elapsed,
                detail=detail,
            )
        except TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayAvailabilityResult(
                ok=False,
                latency_ms=elapsed,
                detail="Request to the LLM timed out.",
            )
        except Exception:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayAvailabilityResult(
                ok=False,
                latency_ms=elapsed,
                detail="An unexpected error occurred while contacting the LLM.",
            )
        else:
            elapsed = int((time.monotonic() - start) * 1000)
            return GatewayAvailabilityResult(
                ok=True,
                latency_ms=elapsed,
                detail="LLM responded successfully.",
            )
        finally:
            await client.close()
