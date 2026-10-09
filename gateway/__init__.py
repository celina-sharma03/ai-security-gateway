__version__ = "1.0.0"
"""The one place the version is written. The CLI, the API and its
documentation page all read it from here.

It tracked the phase while the project was being built -- 0.4.0 was Phase 4 --
and settles at 1.0.0 with V1: a gateway that redacts, forwards, blocks and
records, with documentation that has been followed on a clean machine.

The copy in pyproject.toml has to be changed to match. Nothing reads that one
at runtime, which is exactly why it is the one that silently falls behind.
"""
