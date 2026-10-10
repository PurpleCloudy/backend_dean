# Original dean-agent runtime

Source: https://github.com/Ethenkam/dean-agent
Pinned commit: d92bf5fbfc5b37c3236a6454aeffa5374fe88b11
License: MIT; see LICENSE.

The twelve Python modules in dean_agent, LICENSE and upstream pyproject.toml are retained byte-for-byte. Repository history, example databases, documentation, development scripts, tests and the nested backend_dean copy are omitted because the application does not read them. The current backend is maintained separately; the nested copy predates its chat history, catalogs and OLAP. The local HTTP/security adapter lives separately in integrations/dean_agent_adapter.

The new image/PDF dependencies have their own licenses: Pillow uses MIT-CMU;
PyMuPDF declares AGPL-3.0 or an Artifex commercial license. The agent's MIT license
does not relicense those dependencies. Commercial distribution is outside this
local verification; review the dependency terms for the intended deployment.
