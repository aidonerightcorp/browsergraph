"""The one place the version is written.

Deliberately a module containing nothing but a literal. `pyproject.toml` reads
it with setuptools' `attr:` directive, which parses the file statically — it
only falls back to *importing* the package when the value is not a plain
literal, and importing `browsergraph` inside an isolated build environment
fails, silently yielding 0.0.0.

Before this, the version lived in both `pyproject.toml` and `__init__.py`; they
drifted, and the wheel built as 0.1.0 while `browsergraph.__version__` said
0.2.0.
"""

__version__ = "0.3.0"
