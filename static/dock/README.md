# Dock (vendored)

A copy of `src/` and `css/` of the Dock library (`fab-ioc/dock`, private), at the commit named in `VERSION`.
It is a copy, not a submodule, because this repository is public and Dock is not.

**Do not change these files here.** A change goes into Dock itself; a need Ensemble has of it is written down
in the Dock project's `ENSEMBLE-NEEDS.md`. To take a newer Dock, copy its `src/` and `css/` over these and
update `VERSION`.

Ensemble uses it for a project's PO screen (`index.html`, "PO screen as panels"). The hub serves these files
from `/static/dock/` and lists them in `PAGE_FILES`, so an open tab reloads onto a new copy by itself.
`css/theme.css` is not loaded: `index.html` maps the `--dk-*` tokens onto its own.
