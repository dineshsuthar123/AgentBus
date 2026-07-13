"""Run an opt-in, credential-safe Azure OpenAI smoke test.

The endpoint, deployment, and key are read from the environment. This script
does not provide credentials or run as part of the test suite.
"""

import os

from openai import OpenAI


def main() -> None:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEFAULT_DEPLOYMENT", "").strip()
    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_ENDPOINT", endpoint),
            ("AZURE_OPENAI_API_KEY", api_key),
            ("AZURE_OPENAI_DEFAULT_DEPLOYMENT", deployment),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment variable(s): {', '.join(missing)}")

    client = OpenAI(base_url=endpoint.rstrip("/") + "/", api_key=api_key)
    response = client.responses.create(
        model=deployment,
        input="Reply with exactly: AZURE_OK",
    )
    if response.output_text.strip() != "AZURE_OK":
        raise RuntimeError("Azure minimal smoke test returned an unexpected response")
    print("Azure minimal smoke test passed")


if __name__ == "__main__":
    main()
