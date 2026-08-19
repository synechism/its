"""Actual-game Slay the Spire benchmark for language-model agents."""

__version__ = "0.1.0"
__all__ = ["__version__"]

# Verifiers discovers third-party plugins from the installed package's exported
# classes. Keep the core package importable on Python 3.14, where the pinned
# training stack is intentionally unavailable.
try:
    from sts_bench.environment import StsBenchEnv, StsBenchHarness, StsBenchTaskset
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("verifiers"):
        raise
else:
    __all__ += ["StsBenchEnv", "StsBenchHarness", "StsBenchTaskset"]
