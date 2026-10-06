"""What the gateway records.

Three things: who the traffic belongs to, which key it arrived on, and what
happened to it.

**What is deliberately absent is the point of this file.** There is no column
for the prompt, none for the model's reply, and none for the values that were
redacted. The gateway exists so that secrets do not end up in a provider's
logs; a gateway that wrote every prompt into its own database would have moved
the problem rather than solved it, and one breach would leak everything anyone
had ever typed.

The redaction mapping is the sharpest case. `[CREDIT_CARD_1] -> 4111 1111 1111
1111` is not "data about a card number", it is a plaintext table of card
numbers with a label on each. `CheckResult.mapping` already says it: held for
the lifetime of a request, never stored.

What is here answers the questions that actually get asked -- how many cards
were caught this week, by which team, on which model, at what cost -- without
holding a single one of them.

If an operator ever does need full prompt logs for incident investigation,
that belongs in a separate table, off by default, with a retention limit and a
deliberate decision to switch it on. The schema here doesn't prevent that; it
just refuses to do it quietly.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    """UTC, always. A gateway and its operator are rarely in the same place."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """Whose traffic this is -- a team, a department, a customer.

    Every row in the database hangs off one of these, which is what makes
    "the billing team caught fourteen card numbers" a question with an answer.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    keys: Mapped[list["ApiKey"]] = relationship(back_populates="tenant")
    events: Mapped[list["Event"]] = relationship(back_populates="tenant")


class ApiKey(Base):
    """A key the gateway issued, stored as a hash it cannot reverse.

    The plaintext key is shown once, when it is created, and never again --
    not because that is inconvenient, but because it is the proof that a
    database breach does not hand someone a working key.

    `prefix` is the first few characters, kept in clear. It is how a key is
    found in a list and recognised in a log ("gw_live_8fa2..."), and it is
    short enough to be useless on its own.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)

    label: Mapped[str] = mapped_column(String(100))
    """What this key is for -- "billing team CI", "priya's laptop". Without it
    nobody can ever safely revoke one."""

    prefix: Mapped[str] = mapped_column(String(20), index=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    """SHA-256 of the whole key.

    Not bcrypt, on purpose. Bcrypt is deliberately slow, which is right for
    human passwords -- short, guessable, reused -- and wrong for 32 bytes of
    randomness that has to be verified on every single request. There is
    nothing to brute-force here, so the cost would buy nothing and be paid
    forever.
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    """Revoked, never deleted. A deleted key leaves its events pointing at
    nothing, and "which key did this come from" is exactly the question asked
    after something goes wrong."""

    tenant: Mapped[Tenant] = relationship(back_populates="keys")
    events: Mapped[list["Event"]] = relationship(back_populates="api_key")

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class Event(Base):
    """One request, as the gateway saw it. The verdict, never the content."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"), index=True)

    model: Mapped[str | None] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(10))
    """shadow or enforce, recorded per request. Reading a month-old log
    without it would leave you unable to say whether anything was enforced."""

    decided: Mapped[str] = mapped_column(String(10))
    """What the checks asked for."""

    action: Mapped[str] = mapped_column(String(10), index=True)
    """What was actually done. Differs from `decided` in shadow mode, and that
    difference is the whole argument for running shadow first."""

    blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    """Read from the provider's own `usage`, so the number is theirs and not
    an estimate of ours. Null when the request never reached them."""

    upstream_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    error: Mapped[str | None] = mapped_column(String(100))
    """The type of failure only -- "UpstreamTimeout" -- never a message. The
    same rule as the pipeline and UpstreamError: a message can quote the text
    it was handed, and the text is what all of this protects."""

    tenant: Mapped[Tenant] = relationship(back_populates="events")
    api_key: Mapped[ApiKey | None] = relationship(back_populates="events")
    categories: Mapped[list["EventCategory"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventCategory(Base):
    """What was found in one request, one row per category.

    A child table rather than a comma-separated column, because the questions
    this data exists to answer are "how many card numbers this month" and
    "which category does this team trip most often" -- and those are a GROUP
    BY, not a string search.
    """

    __tablename__ = "event_categories"
    __table_args__ = (UniqueConstraint("event_id", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)

    event: Mapped[Event] = relationship(back_populates="categories")
