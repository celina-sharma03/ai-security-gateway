"""The whole product, in one file.

This is an ordinary OpenAI client talking to a real model. Exactly one line is
different from the version that talks straight to the provider:

    base_url="http://127.0.0.1:8080/v1"

Run it with the gateway running in enforce mode:

    $env:GROQ_API_KEY = "gsk_..."
    python examples/try_the_gateway.py

It sends a card number and an email address, and asks the model to repeat the
text back. The model can only repeat what it received -- so its answer is the
proof. Nothing else in this file is needed to believe the result.
"""

import contextlib
import os
import sys

from openai import APIConnectionError, APIStatusError, OpenAI

GATEWAY = "http://127.0.0.1:8080/v1"

# A fictional card -- the published Visa test number, which belongs to nobody
# -- and an example.com address. Even a demonstration of a thing that protects
# data should not use anybody's data.
SECRET = "my card is 4111 1111 1111 1111 and my email is priya@example.com"

# Asking the model to *repeat* the line gets refused: what reaches it reads as
# "my card is [CREDIT_CARD_1] and my email is [EMAIL_1]", and although there is
# no data left in it, the words around the placeholders are enough to trip a
# model's safety training. Asking it to list the bracketed tokens gets the same
# proof -- it can only list placeholders it actually received -- and asks it to
# repeat nothing that looks sensitive.
PROMPT = (
    "List every [BRACKETED_TOKEN] you can see in the line below, exactly as "
    f"written, separated by commas. If there are none, say 'none'.\n\n{SECRET}"
)


def use_utf8_output() -> None:
    """The same cure as gateway/console.py, written out again rather than
    imported.

    This file is meant to be copied. An example client that depends on the
    server's own package is not an example of anything a stranger can use --
    and the point of it is that nothing but `base_url` is special.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    use_utf8_output()

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("Set GROQ_API_KEY first:", file=sys.stderr)
        print('    $env:GROQ_API_KEY = "gsk_..."', file=sys.stderr)
        return 2

    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    # The one changed line. Everything else is how anyone writes this.
    client = OpenAI(api_key=key, base_url=GATEWAY)

    print(f"sending to {GATEWAY}, model {model}\n")
    print("what this script sent:")
    print(f"  {SECRET}\n")

    # with_raw_response so the gateway's own headers are visible. An ordinary
    # caller would just use client.chat.completions.create(...).
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
        )
    except APIStatusError as exc:
        # The provider's own error, forwarded by the gateway untouched. Worth
        # reading as what it is rather than as a crash: a 404 here means the
        # request reached the provider and the provider declined it.
        print(f"the provider answered {exc.status_code}:", file=sys.stderr)
        print(f"  {exc.message}\n", file=sys.stderr)

        if exc.status_code == 404:
            print("model names change. list the current ones with:", file=sys.stderr)
            print(
                '  $h = @{ Authorization = "Bearer $env:GROQ_API_KEY" }\n'
                '  (Invoke-RestMethod -Uri "https://api.groq.com/openai/v1/models"'
                " -Headers $h).data.id\n"
                '  $env:GROQ_MODEL = "the one you picked"',
                file=sys.stderr,
            )
        return 1
    except APIConnectionError:
        print(f"nothing is listening on {GATEWAY}.", file=sys.stderr)
        print("  start it with:  python -m gateway serve", file=sys.stderr)
        return 1

    completion = raw.parse()
    reply = completion.choices[0].message.content

    print("what the model says it received:")
    print(f"  {reply}\n")

    print("what the gateway did:")
    for header in (
        "x-gateway-mode",
        "x-gateway-decided",
        "x-gateway-action",
        "x-gateway-categories",
    ):
        print(f"  {header:<24}{raw.headers.get(header, '-')}")

    print()
    answer = reply or ""

    if "4111" in answer:
        print("  THE CARD NUMBER REACHED THE MODEL. Is the gateway in shadow mode?")
        return 1

    if "[CREDIT_CARD_1]" in answer:
        print("  The model listed the placeholder, because the placeholder is all")
        print("  it was given. It cannot name a card number it never received --")
        print("  which is the only proof that matters.")
        return 0

    print("  The card number did not reach the model, and the model did not")
    print("  quote the placeholders either -- they sometimes decline. The")
    print("  gateway's own verdict is above; /check shows the same without a")
    print("  provider involved at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
