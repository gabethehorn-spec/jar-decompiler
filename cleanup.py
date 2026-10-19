"""Optional Claude pass that tidies decompiled Fabric mod source."""
import asyncio
import os
from pathlib import Path

import anthropic

MODEL = "claude-opus-5-5"
MAX_FILES = int(os.getenv("CLEANUP_MAX_FILES", "40"))
MAX_FILE_CHARS = 200_000

SYSTEM_PROMPT = """You are given one Java source file decompiled from a Fabric mod jar for \
Minecraft 1.21.1. Intermediary names have already been remapped to Yarn 1.21.1+build.3 \
where possible; fix any remaining class_XXXX / method_XXXX / field_XXXX names to their \
Yarn equivalents and correct the imports to match.

Reconstruct source as close as possible to the original developer source:
1. Replace inlined constant values with the named constants declared in the class \
(e.g. INTERVAL_TICKS instead of 20).
2. Remove casts the decompiler added that aren't needed, such as (EndTick) before a lambda.
3. Turn decompiler-style nested ifs into early-return / continue guard clauses where that \
reads more naturally.
4. Add @Override where a method implements or overrides a supertype method.
5. Use field names directly (ticks instead of this.ticks) unless that would be ambiguous.
6. Use 4-space indentation and standard Java formatting.
7. Do not change behavior. The result must compile with minecraft 1.21.1, \
yarn 1.21.1+build.3:v2, fabric-loader 0.16.5, fabric-api 0.104.0+1.21.1, Java 21.

Output only the final .java file contents: no explanation and no markdown fences."""

client = anthropic.AsyncAnthropic() if os.getenv("ANTHROPIC_API_KEY") else None


def available() -> bool:
    return client is not None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip() + "\n"


async def clean_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8", errors="replace")
    if len(source) > MAX_FILE_CHARS:
        return False
    async with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": f"File: {path.name}\n\n{source}"}],
    ) as stream:
        message = await stream.get_final_message()
    if message.stop_reason != "end_turn":
        return False
    text = "".join(b.text for b in message.content if b.type == "text")
    if "class " not in text and "interface " not in text and "record " not in text and "enum " not in text:
        return False
    path.write_text(_strip_fences(text), encoding="utf-8")
    return True


async def clean_sources(src_dir: Path, concurrency: int = 4) -> tuple[int, int]:
    """Clean up to MAX_FILES .java files in place. Returns (cleaned, skipped)."""
    files = sorted(src_dir.rglob("*.java"))
    todo, skipped = files[:MAX_FILES], max(0, len(files) - MAX_FILES)
    sem = asyncio.Semaphore(concurrency)

    async def run(p: Path) -> bool:
        async with sem:
            try:
                return await clean_file(p)
            except anthropic.APIError as e:
                print(f"Cleanup failed for {p.name}: {e}")
                return False

    results = await asyncio.gather(*(run(p) for p in todo))
    cleaned = sum(results)
    return cleaned, skipped + (len(todo) - cleaned)
