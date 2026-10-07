# The rules file

`gateway/config/rules.yaml` is the one file an operator edits. It decides
**what happens** when the gateway finds something — never **how finding
works**, which lives in code where it is tested.

That division is deliberate. A rules file that could change detection would be
a rules file that could silently weaken it, and nobody would know which version
of the gateway they were actually running.

It holds no secrets, so it is safe to keep in your own version control.

## The four actions

```yaml
categories:
  credit_card: redact
  api_key: block
  email: log
  ip_address: off
```

| action | what happens |
| ------ | ------------ |
| `redact` | the value is replaced with `[CREDIT_CARD_1]`; the rest of the message goes on **(default)** |
| `block` | the whole request stops; nothing reaches the provider |
| `log` | the message is sent unchanged, and what was found is recorded |
| `off` | the gateway does not look for this at all |

When one message trips several categories, **the strongest action wins**. A
message with an email set to `log` and a card set to `block` is blocked.

### A category you leave out is redacted

Deleting a line cannot quietly switch detection off. The absent case is the
protective one, because the alternative is a file where a stray keystroke
silently stops the gateway looking for card numbers.

To stop looking, you have to say `off` — out loud, on purpose.

### In shadow mode, none of this applies

`GATEWAY_MODE=shadow` turns every `redact` and `block` into a `log`. The rules
file still decides what is *looked for* and what *would* happen, and
`python -m gateway events` shows both — but nothing is changed on the way
through. See [configuration.md](configuration.md).

## The nine built-in categories

```
credit_card   api_key    password
email         phone      ip_address
aadhaar       pan        upi_id
```

A name outside that list is an error rather than a typo nobody notices:

```
The rules file has a problem, and the gateway won't start until it's fixed.

rules.yaml has a problem:
  categories: unknown category creditcard -- the categories are: aadhaar,
  api_key, credit_card, email, ip_address, pan, password, phone, upi_id
```

## Your own patterns

For values only your organisation has — employee IDs, project codenames,
internal references:

```yaml
custom:
  - name: employee_id
    pattern: '\bEMP-\d{5}\b'
    action: redact
    needs_context: [employee, emp, staff]
```

| field | |
| ----- | --- |
| `name` | becomes the category, so matches read `[EMPLOYEE_ID_1]`. Lowercase, digits and underscores, starting with a letter. Cannot be one of the built-in names |
| `pattern` | a regular expression. Checked at startup — a broken one stops the gateway rather than failing on the thousandth request |
| `action` | `redact`, `block`, `log` or `off`. Defaults to `redact` |
| `needs_context` | optional. **Whole words**, at least one of which must appear shortly before the match |

### Write `\b` around your pattern

`EMP-\d{5}` matches inside `TEMP-12345`. `\bEMP-\d{5}\b` does not. Word
boundaries are the difference between a pattern that finds employee IDs and one
that redacts half your build logs.

### `needs_context` is words, not regex

You write ordinary words and the gateway escapes them for you, then matches
them the same way the built-in patterns do — **as whole words**. So `emp` will
not count inside `temp`, and you cannot accidentally write a regex that matches
everything.

Use it when your pattern is shaped like something ordinary. A five-digit number
is also a postcode, an order number and a page count; `needs_context:
[employee, staff]` is what separates them. A pattern with a distinctive shape
of its own — `EMP-12345` — needs no context at all.

## A mistake stops the gateway

```
The rules file has a problem, and the gateway won't start until it's fixed.

rules.yaml has a problem:
  custom.0.pattern: not a valid regular expression
  (missing ), unterminated subpattern at position 4)
```

An unknown category, an unknown action, a broken regex, an unexpected field —
all of them refuse to start, naming the problem.

The alternative is a gateway that runs while quietly checking less than its
operator believes, which is the worst state a security tool can be in: it looks
fine, the dashboard is green, and it is not doing the job.

## The `off` trap

YAML reads a bare `off` as the boolean **false** — and the same goes for `no`,
`yes` and `on`. So this:

```yaml
categories:
  phone: off
```

arrives in Python as `phone: False`, not as the word `"off"`.

The loader turns that back into `off`, because the most natural thing an
operator could write should not produce a confusing error about booleans.
Quoting it — `phone: "off"` — also works, but nobody should have to know that.
Written up in [FRICTION.md](../FRICTION.md).

### That example, actually run

```
$ python -m gateway check "employee EMP-12345 mailed priya@example.com" \
    --rules ./rules.local.yaml --mode enforce

sent onward:
  employee [EMPLOYEE_ID_1] mailed [EMAIL_1]

checks:
  redaction  redact   found 2 value(s): email, employee_id
```

And the two guards doing their job at once — no context word, and the match
sitting inside a longer word:

```
$ python -m gateway check "build TEMP-12345 failed" \
    --rules ./rules.local.yaml --mode enforce

found     allow    what the checks asked for

sent onward:
  build TEMP-12345 failed
```

## Trying a change without restarting production

```bash
python -m gateway check "EMP-12345 belongs to priya@example.com" \
  --rules ./rules.local.yaml --mode enforce
```

`check` contacts no provider and needs no key, so a rules change can be tested
against real-looking text before anything goes near live traffic.

The server reads its rules **once, at startup** — so editing `rules.yaml` while
it is running changes nothing until it restarts. That is the honest trade for
being predictable: a gateway that reloaded rules mid-flight could change its
behaviour between two requests with nothing to say why.
