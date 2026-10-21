# Third-Party Notices

XYRON does not copy or adapt source code from the repositories reviewed in `docs/repository-audit.md`. The candidates informed architecture and feature boundaries only. No candidate repository's source files, assets, or schemas are distributed in this project.

XYRON depends on upstream packages listed and version-pinned in `pyproject.toml`. Their licenses remain those of their respective authors. Review the package metadata/licenses of the installed distribution when producing a binary or container image; this file is not a complete bill of materials for transitive dependencies.

| Dependency | Version | License/notice |
| --- | --- | --- |
| python-telegram-bot | 22.6 | LGPL-3.0-or-later; see upstream project and installed package metadata |
| SQLAlchemy | 2.0.44 | MIT; see upstream project |
| aiosqlite | 0.21.0 | MIT; see upstream project |
| asyncpg | 0.30.0 | Apache-2.0; see upstream project |
| Alembic | 1.16.5 | MIT; see upstream project |
| python-dotenv | 1.1.1 | BSD-3-Clause; see upstream project |
| HTTPX | 0.28.1 | BSD-3-Clause; see upstream project |

Development-only packages (`pytest`, `pytest-asyncio`, and `ruff`) are declared as optional dependencies and are not required in a production install.

The project is distributed under the MIT License in `LICENSE`. That license applies to XYRON-authored code only and does not override third-party dependency terms.