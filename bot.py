"""Discord bot that decompiles .jar files into Java source using Vineflower.

Fabric mods are remapped from Intermediary to Yarn names first, and can
optionally be tidied up by Claude (see cleanup.py)."""
import asyncio
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

import cleanup  # noqa: E402  (needs .env loaded first)

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_JAR_MB = int(os.getenv("MAX_JAR_MB", "25"))
TIMEOUT_SECONDS = int(os.getenv("DECOMPILE_TIMEOUT", "300"))
# Discord's default upload limit for bots (non-boosted servers).
UPLOAD_LIMIT = 10 * 1024 * 1024

VINEFLOWER_VERSION = "1.10.1"
VINEFLOWER_URL = (
    "https://repo1.maven.org/maven2/org/vineflower/vineflower/"
    f"{VINEFLOWER_VERSION}/vineflower-{VINEFLOWER_VERSION}.jar"
)
TOOLS_DIR = Path(__file__).parent / "tools"
VINEFLOWER_JAR = TOOLS_DIR / f"vineflower-{VINEFLOWER_VERSION}.jar"

TINY_REMAPPER_VERSION = "0.10.4"
TINY_REMAPPER_URL = (
    "https://maven.fabricmc.net/net/fabricmc/tiny-remapper/"
    f"{TINY_REMAPPER_VERSION}/tiny-remapper-{TINY_REMAPPER_VERSION}-fat.jar"
)
TINY_REMAPPER_JAR = TOOLS_DIR / f"tiny-remapper-{TINY_REMAPPER_VERSION}-fat.jar"

YARN_VERSION = "1.21.1+build.3"
YARN_URL = (
    "https://maven.fabricmc.net/net/fabricmc/yarn/"
    f"{YARN_VERSION.replace('+', '%2B')}/yarn-{YARN_VERSION.replace('+', '%2B')}-v2.jar"
)
YARN_TINY = TOOLS_DIR / f"yarn-{YARN_VERSION}.tiny"

def find_java() -> str | None:
    """Use java from PATH, else fall back to a standard Windows install folder."""
    found = shutil.which("java")
    if found:
        return found
    for base in (r"C:\Program Files\Eclipse Adoptium", r"C:\Program Files\Java"):
        for exe in sorted(Path(base).glob("*/bin/java.exe"), reverse=True):
            return str(exe)
    return None


JAVA = find_java()

# One decompile at a time so the host doesn't get hammered.
decompile_lock = asyncio.Semaphore(1)


def ensure_tools() -> None:
    TOOLS_DIR.mkdir(exist_ok=True)
    for name, url, dest in (
        (f"Vineflower {VINEFLOWER_VERSION}", VINEFLOWER_URL, VINEFLOWER_JAR),
        (f"tiny-remapper {TINY_REMAPPER_VERSION}", TINY_REMAPPER_URL, TINY_REMAPPER_JAR),
    ):
        if not dest.exists():
            print(f"Downloading {name}...")
            urllib.request.urlretrieve(url, dest)
    if not YARN_TINY.exists():
        print(f"Downloading Yarn {YARN_VERSION} mappings...")
        yarn_jar = TOOLS_DIR / "yarn.jar"
        urllib.request.urlretrieve(YARN_URL, yarn_jar)
        with zipfile.ZipFile(yarn_jar) as zf:
            YARN_TINY.write_bytes(zf.read("mappings/mappings.tiny"))
        yarn_jar.unlink()


def is_fabric_mod(jar_path: Path) -> bool:
    with zipfile.ZipFile(jar_path) as zf:
        return "fabric.mod.json" in zf.namelist()


async def run_java(*args: str) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        JAVA, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"Timed out after {TIMEOUT_SECONDS}s."
    return proc.returncode == 0, output.decode(errors="replace")[-1500:]


async def remap_to_yarn(jar_path: Path, out_jar: Path) -> tuple[bool, str]:
    return await run_java(
        "-jar", str(TINY_REMAPPER_JAR), str(jar_path), str(out_jar),
        str(YARN_TINY), "intermediary", "named",
    )


_yarn_names: dict[str, str] | None = None
INTERMEDIARY_NAME = re.compile(r"\b(?:method|field|comp)_\d+\b")


def yarn_names() -> dict[str, str]:
    """Intermediary -> Yarn names for methods and fields (they're globally unique)."""
    global _yarn_names
    if _yarn_names is None:
        _yarn_names = {}
        for line in YARN_TINY.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("\t")
            if parts[0] in ("m", "f") and len(parts) == 4:
                _yarn_names[parts[2]] = parts[3]
    return _yarn_names


def fix_inherited_names(src_dir: Path) -> int:
    """tiny-remapper can't see Minecraft's class hierarchy, so calls to inherited
    methods (e.g. chunk.method_12214()) stay Intermediary. Rename them here."""
    names = yarn_names()
    fixed = 0
    for file in src_dir.rglob("*.java"):
        text = file.read_text(encoding="utf-8", errors="replace")
        new = INTERMEDIARY_NAME.sub(lambda m: names.get(m.group(0), m.group(0)), text)
        if new != text:
            file.write_text(new, encoding="utf-8")
            fixed += 1
    return fixed


async def decompile(jar_path: Path, out_dir: Path) -> tuple[bool, str]:
    return await run_java(
        # Not --silent: Vineflower ignores any options that come after it.
        "-jar", str(VINEFLOWER_JAR), "--indent-string=    ", "--log-level=error",
        str(jar_path), str(out_dir),
    )


def zip_sources(src_dir: Path, zip_path: Path) -> int:
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in src_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(src_dir))
                count += file.suffix == ".java"
    return count


def flatten_output(out_dir: Path) -> Path:
    """Vineflower writes either a folder of sources or a single source jar."""
    jars = list(out_dir.glob("*.jar"))
    if jars:
        extracted = out_dir / "_extracted"
        with zipfile.ZipFile(jars[0]) as zf:
            zf.extractall(extracted)
        return extracted
    return out_dir


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(name="decompile", description="Decompile a .jar file into Java source")
@app_commands.describe(
    jar="The .jar file to decompile",
    yarn="Remap Fabric mods to Yarn 1.21.1 names (default: on)",
    clean="Have Claude tidy the source to look hand-written (slower; needs ANTHROPIC_API_KEY)",
)
async def decompile_cmd(
    interaction: discord.Interaction,
    jar: discord.Attachment,
    yarn: bool = True,
    clean: bool = False,
):
    if not jar.filename.lower().endswith(".jar"):
        await interaction.response.send_message("Please attach a `.jar` file.", ephemeral=True)
        return
    if jar.size > MAX_JAR_MB * 1024 * 1024:
        await interaction.response.send_message(
            f"That jar is too big (limit {MAX_JAR_MB} MB).", ephemeral=True
        )
        return
    if clean and not cleanup.available():
        await interaction.response.send_message(
            "Cleanup isn't set up on this bot (no `ANTHROPIC_API_KEY`).", ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    async with decompile_lock:
        work = Path(tempfile.mkdtemp(prefix="jarbot-"))
        try:
            jar_path = work / "input.jar"
            out_dir = work / "out"
            out_dir.mkdir()
            await jar.save(jar_path)

            if not zipfile.is_zipfile(jar_path):
                await interaction.followup.send("That file isn't a valid jar/zip.")
                return

            notes = []
            if yarn and is_fabric_mod(jar_path):
                remapped = work / "remapped.jar"
                ok, log = await remap_to_yarn(jar_path, remapped)
                if ok:
                    jar_path = remapped
                    notes.append(f"remapped to Yarn {YARN_VERSION}")
                else:
                    notes.append("Yarn remap failed, kept Intermediary names")

            ok, log = await decompile(jar_path, out_dir)
            if not ok:
                await interaction.followup.send(f"Decompilation failed:\n```\n{log}\n```")
                return

            src_dir = flatten_output(out_dir)
            if jar_path.name == "remapped.jar":
                await asyncio.to_thread(fix_inherited_names, src_dir)
            if clean:
                await interaction.edit_original_response(
                    content="Decompiled — Claude is cleaning up the source..."
                )
                cleaned, skipped = await cleanup.clean_sources(src_dir)
                notes.append(
                    f"Claude cleaned {cleaned} files" + (f", {skipped} left as-is" if skipped else "")
                )
            stem = Path(jar.filename).stem
            zip_path = work / f"{stem}-sources.zip"
            count = await asyncio.to_thread(zip_sources, src_dir, zip_path)

            if zip_path.stat().st_size > UPLOAD_LIMIT:
                await interaction.followup.send(
                    f"Decompiled {count} classes, but the source zip is "
                    f"{zip_path.stat().st_size / 1e6:.1f} MB — too big to upload to Discord."
                )
                return

            await interaction.followup.send(
                f"Decompiled **{jar.filename}** → {count} Java files."
                + (f" ({'; '.join(notes)})" if notes else ""),
                file=discord.File(zip_path),
            )
        except Exception as e:
            await interaction.followup.send(f"Something went wrong: `{e}`")
        finally:
            shutil.rmtree(work, ignore_errors=True)


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user} — /decompile is ready.")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN (in .env locally, or as a Railway variable).")
    if JAVA is None:
        raise SystemExit("Java is not installed or not on PATH (Java 17+ required).")
    ensure_tools()
    client.run(TOKEN)
