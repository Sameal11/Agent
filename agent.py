"""Mini OpenClaw: LLM agent that uses linux-mcp as its tool server.

Two separate connections:
  1. Agent -> LLM        via OpenAI-compatible HTTP (vLLM/Qwen).
  2. Agent -> linux-mcp  via MCP over stdio: this script launches the
                          server as a subprocess and talks the MCP
                          protocol to it (list_tools / call_tool).

Run:
  pip install -r requirements.txt
  # put LLM_URL / LLM_KEY / LLM_MODEL in mcp/.env (see mcp/.env.example), or export them:
  export LLM_URL="https://your-ngrok-url.ngrok-free.app/v1"
  export LLM_KEY="your-vllm-api-key"
  export LLM_MODEL="Qwen/Qwen2.5-7B-Instruct-AWQ"
  python agent.py
"""
import asyncio
import json
import os
import re
import shlex
import sys
from pathlib import Path
import getpass
import platform
from datetime import datetime

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import Frame, TextArea
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

console = Console(theme=Theme({
    "prompt": "bold orange3", "error": "bold red",
    "agent": "bold green", "dim": "dim cyan",
}))

ROOT = Path(__file__).resolve().parent
MCP_ROOT = ROOT / "mcp"
MCP_SRC = MCP_ROOT / "src"
APPROVAL_PATH = ROOT / ".mcp_approvals.json"

# Resolved from this file's location. It used to be the relative path "mcp/.env", which was
# silently ignored whenever the agent was started from any other directory.
load_dotenv(MCP_ROOT / ".env")

# ---------- LLM connection ----------
BASE_URL = os.getenv("LLM_URL", "http://localhost:8000/v1")
MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct-AWQ")
llm = OpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_KEY", "x"),
             timeout=float(os.getenv("LLM_TIMEOUT", "120")),
             default_headers={"ngrok-skip-browser-warning": "1"})

# ---------- MCP server launch command ----------
MCP_CMD = shlex.split(os.getenv("MCP_SERVER_CMD") or f"{shlex.quote(sys.executable)} -m linux_mcp")
if MCP_CMD[0] in ("python", "python3"):
    # A bare "python" may not exist, or may be a different interpreter without `mcp` installed.
    MCP_CMD[0] = sys.executable

# Tools that only read state. EVERYTHING ELSE asks the human here on the client side. (The
# list used to be the opposite — the tools to confirm — so a newly added mutating tool ran
# silently until someone remembered to add it.) The MCP server can't prompt interactively
# over stdio, so the agent, which owns the terminal, is where "ask the user" happens.
READ_ONLY_TOOLS = {"system_info", "read_file", "list_processes", "search_packages",
                   "network_info", "list_applications","web_search"}

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
# Small models have small context windows (8192 tokens on your vLLM, and the tool schemas alone
# use ~2k). One big tool result (e.g. `curl` of a web page) used to be stored whole in the
# history, after which EVERY later request failed with "maximum context length" until restart.
MAX_TOOL_CHARS = int(os.getenv("LLM_MAX_TOOL_CHARS", "3000"))         # per tool result
MAX_HISTORY_CHARS = int(os.getenv("LLM_MAX_HISTORY_CHARS", "12000"))  # whole conversation


def shorten(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    """Keep the start and end of a long text, drop the middle."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n...[{len(text) - limit} chars omitted]...\n{text[-half:]}"

def mcp_tool_to_openai_schema(tool) -> dict:
    return {"type": "function", "function": {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.inputSchema or {"type": "object", "properties": {}},
    }}


def clean(text: str) -> str:
    """Make model-supplied text safe to show: escape control characters (incl. ESC), which
    could otherwise hide or rewrite part of a command in the terminal."""
    return "".join(c if (c.isprintable() or c == "\n") else f"\\x{ord(c):02x}" for c in str(text))


def describe_call(name: str, args: dict) -> str:
    """What the human is asked to approve. Must show everything that matters: it used to show
    only the unit for control_service (not start/stop/disable) and only the path for write_file."""
    if name == "shell":
        extra = f"   [cwd={args['cwd']}]" if args.get("cwd") else ""
        return f"shell: {args.get('command', '')}{extra}"
    if name == "write_file":
        content = str(args.get("content", ""))
        mode = "append" if args.get("append") else "overwrite"
        preview = content[:80].replace("\n", "\\n") + ("..." if len(content) > 80 else "")
        return f"write_file: {args.get('path')} ({mode}, {len(content)} chars) {preview!r}"
    return f"{name}: {json.dumps(args, ensure_ascii=False)}"


def set_approval(summary: str) -> None:
    approvals = []
    if APPROVAL_PATH.exists():
        try:
            with APPROVAL_PATH.open("r", encoding="utf-8") as fh:
                approvals = json.load(fh)
        except Exception:
            approvals = []
    if not isinstance(approvals, list):
        approvals = []
    summary_text = str(summary).strip()
    if summary_text and summary_text not in approvals:
        approvals.append(summary_text)
        with APPROVAL_PATH.open("w", encoding="utf-8") as fh:
            json.dump(approvals, fh)


def prompt_approval(summary: str) -> bool:
    # Text objects, not markup strings: Rich would otherwise interpret "[...]" inside the
    # command as styling (and `[conceal]` could hide part of it from the approver).
    console.print(Text.assemble(("Approval required: ", "yellow"), clean(summary)))
    try:
        response = input("Allow this action? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return response in {"y", "yes"}


def mcp_environment() -> dict:
    """Environment for the linux-mcp subprocess — and therefore for every command the model
    runs through it. Secrets are removed (it used to inherit LLM_KEY and everything else, so
    `printenv` handed them to the model); GUI/locale/PATH variables are kept."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LLM_") and not _SECRET_NAME.search(k)}
    env["MCP_APPROVAL_PATH"] = str(APPROVAL_PATH)
    env["MCP_TRUST_CLIENT_APPROVAL"] = "1"      # this client asks the human (see READ_ONLY_TOOLS)
    if MCP_SRC.exists():
        entries = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        if str(MCP_SRC) not in entries:
            entries.insert(0, str(MCP_SRC))
        env["PYTHONPATH"] = os.pathsep.join(entries)
    return env

SYSTEM_PROMPT = """You are Claw, an autonomous assistant that operates this Linux computer for the user through tools.

ENVIRONMENT
- Now: {now} | OS: {os} | User: {user} | Desktop: {desktop}
- Workspace = default folder for shell, read_file and write_file.
- Tools: {tools}

HOW YOU WORK
1. Act, don't narrate. When asked to do something, call the tools yourself; never answer with steps for the user to run.
2. Work in small steps: call a tool, read the result, decide the next step, stop when the goal is met.
3. Use the most specific tool: web_search then open_url (websites), read_file/write_file (files), list_processes then kill_process by pid (processes), control_service (services), search_packages then install_package (software). Use shell only when no specific tool fits.
4. Verify after changing something (e.g. list_processes after a kill, `which name` after an install).
5. Never invent package names, URLs, paths or command output. Look them up with a tool, or say you could not.

WEB
- You cannot read web pages yourself. To find a website or page (a college portal, a company, a service), call web_search first, then call open_url with a URL from its results. Open a URL you were not given only if web_search returned it.
- If web_search fails, call open_url with https://www.google.com/search?q=<url-encoded terms>.
- Never guess a URL; never curl/wget pages or print page contents. Never say you cannot find something before you have called web_search.

READING RESULTS
- "Error:" or "[exit code: N]" (N not 0) = it failed. Read the message, fix the cause, try a different way.
- "left running in the background" = a GUI app started fine. Do not retry it or kill it.
- "Blocked: user did not approve" = the user said no. Do not repeat it; ask what they want instead.
- "permission denied" on install or service actions = root is needed. Tell the user; do not loop.
- The same failure twice = stop, explain the problem and the options.

SAFETY
- Every change asks the user for approval; that is normal. Do exactly what was asked, nothing extra.
- Never delete data, format disks or stop system services unless clearly asked. Never print secrets or keys.
- Prefer kill_process with a pid you looked up over broad `pkill -f`.

STYLE
- Reply in the user's language, in 1-3 short sentences: what you did and the result. No tool ids or step recap unless asked. Ask the user only for things no tool can find (passwords, choices) - never for a URL you could search for."""


def build_system_prompt(tool_names: list[str]) -> str:
    """Built per request so the date and the tool list are always current."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    desktop = os.getenv("XDG_SESSION_TYPE") or ("x11" if os.getenv("DISPLAY") else "none (headless)")
    return SYSTEM_PROMPT.format(
        now=datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M %Z"),
        os=platform.platform(terse=True), user=user, desktop=desktop,
        tools=", ".join(tool_names) or "none",
    )

class Agent:
    MAX_TURNS_KEPT = 6
    MAX_MESSAGES_KEPT = 80

    def __init__(self, session: ClientSession, tool_schemas: list[dict]):
        self.session = session
        self.tools = tool_schemas
        self.history = []

    def llm_error(self, exc: Exception) -> RuntimeError:
        return RuntimeError(
            f"LLM connection failed for {BASE_URL}. Start the model server or set "
            f"LLM_URL/LLM_KEY/LLM_MODEL correctly. Original error: {exc}"
        )

    async def call_tool_with_confirmation(self, name: str, args: dict, ask) -> str:
        if name not in READ_ONLY_TOOLS:
            if not ask(describe_call(name, args)):
                return "Blocked: user did not approve this action."
        result = await self.session.call_tool(name, arguments=args)
        # MCP returns a list of content blocks; join any text ones.
        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
        if getattr(result, "isError", False) and not text.startswith("Error"):
            text = f"Error: {text}"
        return text

    @staticmethod
    def _show(name: str, arguments: str, out: str) -> None:
        # Text, not markup: tool output is arbitrary (`ls /dev` prints "[/dev/...]", which
        # Rich parsed as a closing tag and crashed mid-turn). It also swallowed "[shell]".
        console.print(Text(clean(f"[{name}] {arguments[:200]}\n  -> {str(out)[:300]}"), style="dim cyan"))

    async def _run_tool_call(self, tc, ask) -> str:
        try:
            args = json.loads(tc.function.arguments)
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be a JSON object")
            return await self.call_tool_with_confirmation(tc.function.name, args, ask)
        except Exception as e:
            return f"Error: {e}"
    def _shrink_history(self) -> bool:
        """Cut every stored tool result down hard. True if anything got smaller."""
        changed = False
        for m in self.history:
            if m["role"] == "tool" and len(m["content"]) > 500:
                m["content"] = shorten(m["content"], 500)
                changed = True
        return changed
    
    def _trim_history(self) -> None:
        """Keep the last few user turns, always starting on a user message. (It used to keep
        the last 24 raw messages and then drop everything before the first user message, which
        emptied the history after any long tool-using turn.)"""
        user_idx = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(user_idx) > self.MAX_TURNS_KEPT:
            del self.history[: user_idx[-self.MAX_TURNS_KEPT]]
        while len(self.history) > self.MAX_MESSAGES_KEPT:
            nxt = [i for i, m in enumerate(self.history) if m["role"] == "user" and i > 0]
            if not nxt:
                break
            del self.history[: nxt[0]]
                # Also keep the whole conversation inside a size budget (oldest whole turns go first).
        while sum(len(json.dumps(m, ensure_ascii=False)) for m in self.history) > MAX_HISTORY_CHARS:
            nxt = [i for i, m in enumerate(self.history) if m["role"] == "user" and i > 0]
            if not nxt:
                break
            del self.history[: nxt[0]]

    async def run(self, text: str, ask, max_steps=12) -> str:
        self.history.append({"role": "user", "content": text})
        system = build_system_prompt([t["function"]["name"] for t in self.tools])
        reply = "Stopped: step limit reached."
        for _ in range(max_steps):
            kwargs = {"tools": self.tools} if self.tools else {}   # [] is rejected by the API
            try:
                msg = llm.chat.completions.create(
                    model=MODEL, temperature=0.2,
                    messages=[{"role": "system", "content": system}] + self.history, **kwargs,
                ).choices[0].message
            except Exception as exc:
                if "context length" in str(exc).lower() and self._shrink_history():
                    continue  # retry with smaller tool results
                raise self.llm_error(exc) from exc
            self.history.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                reply = msg.content or ""
                break
            answered = set()
            try:
                for tc in msg.tool_calls:
                    out = await self._run_tool_call(tc, ask)
                    # Record the result BEFORE printing anything, and for every call: an
                    # assistant tool_call without a matching tool message makes the API
                    # reject every later request in this conversation.
                    self.history.append({"role": "tool", "tool_call_id": tc.id, "content": shorten(str(out))})
                    answered.add(tc.id)
                    self._show(tc.function.name, tc.function.arguments, out)
            finally:
                for tc in msg.tool_calls:
                    if tc.id not in answered:
                        self.history.append({"role": "tool", "tool_call_id": tc.id,
                                             "content": "Error: interrupted before this tool ran."})
        self._trim_history()
        return reply


# ---------- boxed prompt ----------
async def get_boxed_prompt(prompt_text="you> "):
    text_area = TextArea(multiline=False, prompt=HTML(f"<b><ansicyan>{prompt_text}</ansicyan></b>"))
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        event.app.exit(result=text_area.text)

    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):
        event.app.exit(result="exit")

    frame = Frame(text_area, style="fg:ansibrightblue")
    app = Application(layout=Layout(frame), key_bindings=kb, full_screen=False)
    return ((await app.run_async()) or "exit").strip()


async def main():
    console.print(Panel.fit(
        Text.assemble(("Agent CLI Ready\n", "bold bright_blue"),
                      f"LLM: {MODEL} @ {BASE_URL}\n",
                      f"MCP: {' '.join(MCP_CMD)}\n",
                      "(type 'exit' or 'quit' to leave)"),
        title="Claw Agent", style="bold green"))

    try:
        llm.models.list()
    except Exception as exc:
        console.print(Text(
            f"\nLLM server unreachable at {BASE_URL}. "
            f"Start the model backend or set LLM_URL/LLM_KEY/LLM_MODEL correctly.\n"
            f"Details: {exc}\n", style="error"))
        raise SystemExit(1) from None

    server_params = StdioServerParameters(
        command=MCP_CMD[0],
        args=MCP_CMD[1:],
        env=mcp_environment(),
        cwd=str(MCP_ROOT),
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_list = (await session.list_tools()).tools
            schemas = [mcp_tool_to_openai_schema(t) for t in tool_list]
            console.print(Text(f"Connected. {len(schemas)} tools available: "
                               f"{', '.join(t.name for t in tool_list)}\n", style="dim cyan"))

            agent = Agent(session, schemas)
            ask = prompt_approval

            while True:
                t = await get_boxed_prompt("you> ")
                if t.lower() in ("exit", "quit"):
                    console.print("[bold green]BYE![/bold green]")
                    break
                if not t:
                    continue
                try:
                    console.print("[bold yellow]Agent is thinking...[/bold yellow]")
                    response = await agent.run(t, ask)
                    console.print(Text.assemble(("Agent: ", "agent"), clean(response), "\n"))
                except Exception as err:
                    console.print(Text.assemble(("\nError running agent: ", "error"), f"{err}\n"))


if __name__ == "__main__":
    asyncio.run(main())
