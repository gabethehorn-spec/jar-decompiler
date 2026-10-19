# JAR Decompiler Discord Bot

Slash command `/decompile jar:<file>` → replies with a `.zip` of the decompiled Java source (via [Vineflower](https://github.com/Vineflower/vineflower)).

Options:
- `yarn` (default **on**): Fabric mods are remapped from Intermediary (`class_XXXX`, `method_XXXX`) to **Yarn 1.21.1+build.3** names before decompiling, with 4-space indentation.
- `clean` (default off): Claude rewrites each file to look hand-written: named constants instead of inlined values, guard clauses, `@Override`, no redundant casts or `this.`, without changing behavior. Needs `ANTHROPIC_API_KEY` in `.env`; slower and costs API credits (capped at `CLEANUP_MAX_FILES` files per jar).

## Setup
1. Install **Python 3.10+** and **Java 17+** (`java -version` should work).
2. Create a bot at https://discord.com/developers/applications → Bot → copy the token.
3. Invite it: OAuth2 → URL Generator → scopes `bot` + `applications.commands`, permissions *Send Messages* + *Attach Files*.
4. In this folder:
   ```
   pip install -r requirements.txt
   copy .env.example .env   # then paste your token into .env
   python bot.py
   ```
   Vineflower, tiny-remapper and the Yarn mappings are downloaded into `tools/` on first run.

## Notes
- Output over 10 MB can't be uploaded (Discord limit) — the bot tells you instead.
- A few names that Yarn itself doesn't map (synthetic fields, some mixin targets) stay as Intermediary.
- Jobs run one at a time; each has a timeout (`DECOMPILE_TIMEOUT`).
- Only decompile jars you have the right to inspect.

## Deploy on Railway
The included `Dockerfile` installs Python + Java and pre-downloads the tools; `railway.json` tells Railway to use it.

1. Push this folder to a GitHub repo (`.env` and `tools/` are git-ignored, so your token stays private).
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
3. Open the service → **Variables** and add `DISCORD_TOKEN` (and optionally `ANTHROPIC_API_KEY`, `MAX_JAR_MB`, `DECOMPILE_TIMEOUT`, `CLEANUP_MAX_FILES`).
4. Deploy. The logs should show `Logged in as … — /decompile is ready.`

No port or domain is needed; it's a worker. Stop the bot on your PC first so two copies aren't running with the same token.
