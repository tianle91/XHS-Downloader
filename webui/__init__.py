"""XHS-Downloader Web UI package.

A batch-download web interface built on top of ``source.XHS`` that packs
results into a single ZIP file. All feature code is contained in this folder.

The FastAPI instance lives in :mod:`webui.app` (import as ``webui.app:app``).
It is intentionally *not* re-exported here, so the ``webui.app`` submodule is
never shadowed by the FastAPI object of the same name.
"""

__all__: list[str] = []
