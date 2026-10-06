# What this protects against, and what it doesn't

A security tool that only lists its strengths is a liability, because somebody
will deploy it believing the gaps aren't there. This page is the honest
version. Read the second half twice.

## What it protects against

**The accidental paste.** Somebody drops a card number, an API key, a
password, an email address, a phone number, an Aadhaar number, a PAN or a UPI
ID into a prompt. The gateway finds it and replaces it before the request
leaves your network.

**Secrets reaching a provider's logs.** The provider receives
`[CREDIT_CARD_1]`, so their logs, their support tooling and anything they
retain contain a placeholder rather than a card number.

**Not knowing.** Every request leaves a record: which team, which key, what was
found, what was done about it, what it cost. You can answer "has anyone pasted
a card number this month" without storing one.

**Developers routing around it.** The gateway holds the provider key, so there
is no credential on a developer's machine to bypass it with.

## What it does not protect against

### Shadow mode changes nothing — and it's the default

A fresh install runs in **shadow mode**: every check runs, every finding is
recorded, and the original text is sent to the provider untouched. That is
deliberate, so nobody puts an untested filter in front of production traffic —
but it means a gateway nobody has configured **protects nothing.**

`GATEWAY_MODE=enforce` is the line that turns it on. `/health` reports which
mode it is in, and so does the startup banner, precisely so this cannot be
assumed.

### Data it cannot recognise

Detection is patterns, checksums and nearby words. It does not understand
meaning, so it does not catch:

- **names and addresses** — there is no shape to match. [Microsoft Presidio
  can do this](presidio-comparison.md) with a language model, and it is the one
  thing it does that this gateway does not
- **free text that is confidential without looking it** — "we are acquiring
  Initech on Friday" is a paragraph, not a pattern
- **internal identifiers** — project codenames, customer references, anything
  specific to your company, unless you add a custom pattern in `rules.yaml`
- **medical, financial or legal detail in prose**

### A value with no words around it

Three categories — bare phone numbers, Aadhaar and PAN — cannot be identified
by shape alone. `9876543210` is a mobile number in one sentence and an invoice
reference in another; they are the same ten digits. Those patterns require a
nearby word like "phone", "aadhaar" or "pan".

**So a bare number pasted on its own is missed.** That is a deliberate trade:
missing one is better than redacting every invoice number in the company, which
is what the alternative does, and that gateway gets switched off within a week.

### A typo

Detection leans on the words around a value, so `adhar` is handled but
`adhaarr` is not. A misspelled context word means no match, and the text goes
through untouched.

The failure is in the safe direction — a typo makes the gateway **miss**, never
over-redact — but it is a real gap and not a theoretical one.

### Prompt injection and jailbreaks

Nothing in V1 looks at what a prompt is trying to *make the model do*. That is
Phase 7, and it is a different kind of detection entirely.

### What comes back

The gateway checks requests, not responses. If a model repeats something
sensitive from its own context or training, nothing here notices. Reversible
redaction — putting the real value back into the answer — is built into the
data shapes but not switched on until V2.

### Streaming

Not supported. A request with `"stream": true` is forwarded, but the reply is
read in full before being passed back, so it arrives as one lump rather than as
it is produced. Phase 8.

### The gateway itself

**It holds the provider key.** That is what makes it a control rather than
advice, and it means compromising the gateway host gets an attacker that key.
The key lives in an environment variable and never in the database, it is never
logged, and the whole thing is meant to run inside your own network — but the
exposure is real and worth naming.

**It is HTTP, not HTTPS.** Terminating TLS is the deployer's job, usually with
a reverse proxy in front. Running it over plain HTTP across an untrusted
network would expose every gateway key in transit.

**A failed authentication is recorded nowhere.** Someone guessing keys is
invisible, because authentication happens before the point where events are
written and a failed attempt has no tenant to attribute. Known gap.

**`/`, `/health` and `/check` need no key.** `/check` contacts no provider and
costs nothing, which is why it is open — it is how someone tries the thing
before trusting it. But anyone who can reach the gateway can use it, and
`/health` tells them which mode you are in.

**There is no rate limiting.** One client can send as many requests as it
likes, and the provider's bill is yours.

### What the database does hold

No prompts, no replies, no redacted values. It does hold **tenant names, key
labels and key prefixes** — "Billing", "priya's laptop" — which is organisation
structure, not content, but it is not nothing.

## What this is not

It is not a DLP suite, it is not compliance certification, and it does not make
anyone GDPR or DPDP compliant on its own. It is one control, for one route out
of your network, and it works best as part of a plan rather than as the plan.
