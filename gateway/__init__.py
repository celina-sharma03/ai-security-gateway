__version__ = "0.4.0"
"""The one place the version is written. The CLI, the API and its
documentation page all read it from here.

It tracks the phase: 0.4.0 is Phase 4. The copy in pyproject.toml has to be
changed to match -- nothing reads that one at runtime, which is exactly why it
is the one that silently falls behind.
"""
