# AgentBoard

A landscape, always-on-top Windows panel for all your coding agents.

- **Usage page**: one card per agent type, with a bar for each limit window: the 5-hour window, the last 7 days and, for Claude, Fable's own weekly window.
- **Columns**: small buttons in the toolbar lay out either page in 3, 2 or 1 columns.
- **Agents page**: a tile for **All agents** and one for each agent type. Click a tile to show its agents below in a five-row scrolling list; the window grows to fit it. Each agent shows its status and where it runs: Claude Code CLI in Windows Terminal, the Claude desktop app, Codex CLI, Codex in a VS Code terminal, and so on.
- **Click an agent** to open it exactly where it runs:
  - a Claude desktop session opens in the Claude app;
  - a terminal agent's own tab is selected, even when several tabs have the same title.

It works with Claude Code, Codex, Gemini CLI, Grok, Qwen Code, opencode, Aider and local models (Ollama). You can add any other CLI agent in the settings file.

![Usage page in 2 columns: a card per agent type with 5h, 7d and Fable bars](docs/usage-light.png)

| Agents, tiles only | Agents in 3 columns, All agents open | Agents, Claude Code open (dark) |
| --- | --- | --- |
| ![Agents page with a tile per agent type](docs/agents-folded-light.png) | ![Every agent listed three per row in a five-row scrolling panel](docs/agents-light.png) | ![Claude Code's agents listed two per row](docs/agents-dark.png) |

These screenshots use the built-in demo data (`--demo`), so the names are made up.

## Run it

1. Install Python 3.10 or newer if you don't have it.
2. Double-click **`run.bat`**.
   - On the first run it installs `psutil` and `pywebview`.
   - The window is drawn by Microsoft Edge WebView2, which comes with Windows 11.

The window opens in the bottom-right corner and stays on top of other windows. Its width is fixed. Its height follows what is shown, and in the lower half of the screen it grows upwards, so it stays clear of the taskbar.

In the title bar:
- the moon or sun icon switches between light and dark (it follows Windows until you choose);
- the pin keeps the window on top or lets it go behind others;
- the gear opens the settings file;
- minimise, maximise and close. Maximise fills the screen the window is on, above the taskbar, and the cards spread into one row. Click it again, or double-click the title bar, to go back.

Under the title bar, the three small buttons on the right set the columns: 3, 2 or 1. Each page keeps its own choice, and on the Agents page it applies to both the tiles and the agent list.

Press **Ctrl+1** for the Usage page and **Ctrl+2** for the Agents page. Drag the bare title bar to move the window.

Other ways to run it:

| Command | What it does |
| --- | --- |
| `python -m agentboard --demo` | Opens the window with made-up agents, so you can try it without running any. |
| `python -m agentboard --once` | Prints one text snapshot, to check your setup. |
| `python -m agentboard --tabs` | Lists the Windows Terminal tabs the app can see. |

## Usage page: 5-hour, 7-day and Fable bars

There is one card per agent type that is running now or was used recently. Each bar shows the tokens used in one window, and how full that window is:
- **`5h`**: the current 5-hour window.
- **`7d`**: the last 7 days.
- **`Fable`** (Claude only): tokens used with Claude Fable models in the last 7 days. Claude plans meter Fable on a weekly limit of its own.
- **Next line**: when the windows reset (`resets 5h 2h 14m · 7d 3d 3h · Fable 4d 1h`), or, without official limits, when the current 5-hour block ends.
- **Bottom line**: today's tokens, cache reads, and, where relevant, requests or loaded local models.

Where the percentage comes from depends on what is available:

| Shown as | When | Meaning |
| --- | --- | --- |
| `42% used` | The CLI reports its own limits: Codex in its logs, and Claude Code once you enable the optional status line (see below). | The official percentage of your 5-hour or weekly limit. Tokens are counted over the same window. |
| `42% of 3.0M` | You set your own limits in the settings file (`limit_tokens` for 5 hours, `limit_tokens_7d` for 7 days, `limit_tokens_fable` for Fable). | Tokens against your own limit. |
| `72% of peak`, or `busiest` | Neither of the above | Tokens compared with your busiest such window in the last 4 weeks. `busiest` means this window is the busiest so far. These bars are drawn lighter, because they are not a limit. |
| `not used` | No tokens in that window | |

For Fable, Claude Code shows the official weekly percentage on its `/usage` screen, but doesn't pass it to the status line yet. Until it does, the Fable bar compares with your own limit or your busiest Fable week. AgentBoard already reads the field Claude Code uses for per-model windows (`model_scoped`), so the bar switches to the official figure once the status line includes it.

Claude's limits are per account, so one Claude Code CLI session with the status line enabled is enough. Its percentages then cover your desktop app sessions too. The figures are kept for up to 6 hours after the last CLI update.

A bar and its percentage turn amber at 80% and red at 95%, but only for official limits and your own limits. Hover over a bar to see the details.

"Tokens" means new input (including cache writes) plus output. Cache reads, meaning reused context, are shown separately, because they are much larger and cheaper. Anthropic and OpenAI don't publish their limits in tokens, so the token counts are a guide. The official percentage is the real measure.

## Agents page: where each agent runs

The page starts with tiles only: one for **All agents** and one for each agent type. Each tile shows its agent count, and how many need approval (red), are waiting for your turn (amber) and are working (green).

Click a tile to list its agents below the tiles, and the window grows to fit the list. The list is always five rows tall, with 3, 2 or 1 agents per row depending on the column setting; any more scroll inside it. Click the tile again to hide the list, and the window shrinks back. Only one tile is open at a time. Each agent shows:
- the session title;
- a status dot:
  - red: it stopped on a tool call, probably waiting for approval;
  - amber: your turn, because it finished and is waiting for you;
  - green, pulsing: working;
  - grey: idle;
- its tokens in this session;
- **where it runs**, with an icon:

| Label | Icon | Meaning |
| --- | --- | --- |
| `Claude Code CLI · Windows Terminal`, `Codex CLI · Windows Terminal`, `Gemini CLI · Console window` | terminal | the CLI, in a terminal tab or a classic console window |
| `Claude desktop app` | app window | a session inside the Claude desktop app |
| `Codex · ChatGPT app` | app window | Codex inside the ChatGPT desktop app |
| `Codex · VS Code terminal`, `Claude Code · Cursor terminal` | code | the CLI in an IDE's built-in terminal |

Hover over an agent to see the model, folder and process ID. Click **need you** at the top to open **All agents**.

## How the jump works

| Where the agent runs | What a click does |
| --- | --- |
| Windows Terminal tab | Switches to **that exact tab** and brings the window to the front. |
| Classic console window (cmd or PowerShell) | Brings that console window to the front. |
| VS Code, Cursor, Windsurf, Kiro or JetBrains terminal | Brings the IDE window to the front. The IDE's own terminal panel can't be selected from outside. |
| Claude desktop app | Opens **that exact session** in the Claude app and brings the app to the front. |
| ChatGPT (Codex) app | Brings the app to the front. Select the thread there. |

For a Claude desktop session, AgentBoard:
1. looks up the app's own record of the session, in `%APPDATA%\Claude\claude-code-sessions`, which links the app's session id to the Claude Code session it runs;
2. asks the app to open it with the app's own link, `claude://code/continue?session=local_…`. The app's taskbar jump list uses the same link.

Tested on Windows 11 with Claude app 3.1623: AgentBoard opened one session and then another, and each was focused, judging by the app's record of when it was last focused.

To find the exact terminal tab, AgentBoard:
1. briefly gives the agent's console a unique title, such as `AgentBoard 1234 a1b2c3`;
2. selects the tab showing that title through Windows UI Automation, which is built into Windows;
3. puts the agent's own title back straight away.

This works even when two tabs have the same title, and even while an agent animates its title with a spinner. You may see the marker flash in the tab for a fraction of a second.

If the marker can't be set, AgentBoard falls back to matching the tab title. In that case, clicking again moves to the next tab with the same title.

Tested on Windows 11 with two stand-in agents in one Windows Terminal window, both with the same title:
- With fixed titles, 8 of 8 jumps landed on the right tab, about 0.3 seconds each, and both titles were restored.
- With titles repainted ten times a second, 8 of 8 jumps also landed on the right tab.

## Add another agent, including local-model agents

Click the gear. This creates and opens `%USERPROFILE%\.agentboard\config.json`. Add an entry, then restart AgentBoard:

```json
{
  "agents": [
    {"id": "my-agent", "name": "My local agent", "color": "#0ea5e9", "local": true,
     "match": {"cmd_regex": "\\bmy-agent\\b"},
     "reader": "generic", "logs": ["~/.my-agent/**/*.jsonl"],
     "quota": {"kind": "unlimited"}}
  ]
}
```

- **`match`** is either a process name (`"exe": ["my-agent.exe"]`) or a regular expression over the command line.
- **`reader: generic`** reads any JSON or JSONL log with token counts in a common format: Anthropic, OpenAI, Gemini or Ollama (`prompt_eval_count` and `eval_count`). If the agent keeps no usage log, use `none`. It is still listed, with its status based on CPU activity.
- **To change a built-in agent**, give its `id`. For example, to set your own Claude token limits per 5 hours and per 7 days, and a Gemini daily request budget:

```json
{"agents": [{"id": "claude", "quota": {"fallback": {"limit_tokens": 3000000, "limit_tokens_7d": 30000000, "limit_tokens_fable": 10000000}}},
            {"id": "gemini", "quota": {"limit_requests": 1000}},
            {"id": "grok", "logs": ["D:/somewhere/grok-sessions/**/*.jsonl"]}]}
```

Built-in log locations:

| Agent | Logs |
| --- | --- |
| Claude Code | `~/.claude/projects` |
| Codex | `~/.codex/sessions`, plus thread titles from its state database, opened read-only |
| Gemini CLI | `~/.gemini/tmp/*/chats` |
| Qwen Code | `~/.qwen/tmp/*/chats` |
| opencode | `~/.local/share/opencode/storage` |
| Grok | `~/.grok`. This is a guess, because Grok Build doesn't document where it stores sessions. Point `logs` at the right folder if yours differs. |

## Optional: exact Claude limits in the CLI

Claude Code passes its 5-hour and 7-day usage to the status-line command. To let AgentBoard show it:
1. Copy `hooks/claude_statusline.py` to `%USERPROFILE%\.agentboard\`.
2. Add this to `~/.claude/settings.json`, adjusting the paths to your Python and user folder:

```json
"statusLine": {"type": "command", "command": "\"C:/Program Files/Python313/python.exe\" \"C:/Users/you/.agentboard/claude_statusline.py\""}
```

Claude Code includes the 5h and 7d figures once it has had a reply from the API, so they appear after your next message in a CLI session.

Your status line will then read like `Opus · 5h 42% · 7d 12%`, and the Claude 5h and 7d bars switch to the official limits. Sessions inside the Claude desktop app don't run a status line, but because the limits are per account, one CLI session is enough to show them.

The command must point at a folder that stays put. If the file moves, the status line just stays empty.

## Privacy

AgentBoard only reads files on this PC:
- agent logs: token counts, timestamps, models and session titles, never the conversation text;
- Codex's state database, in read-only mode;
- the Claude app's session records: only the ids, title, archive flag and last activity time.

It never logs in anywhere, never uses your saved credentials, and never sends data anywhere. Its one network call goes to your own Ollama server at `127.0.0.1`, to list the loaded models.

The window's page is served from a local web server that only this PC can reach.

## Known limits

- **The official Fable percentage isn't available yet.** Claude Code shows it only on its `/usage` screen, so the Fable bar uses your own limit or your busiest Fable week (see the Usage page section).
- **Official Claude limits need a CLI session.** Claude doesn't store the 5h and 7d percentages on disk for desktop app sessions, so without a Claude Code CLI session with the status line, the bars compare with your own limits or your busiest window.
- **The Claude session link** is the app's own, but it isn't documented, so a future app version could change it. If it stops working, the click still brings the app to the front.
- **Codex inside the ChatGPT app** runs as one background process for all its threads, so it appears as one agent, and a click only brings the app to the front. History that Codex imported from other agents is not counted as Codex usage. On the test PC, Codex logged no rate limits, so its bars compare with its busiest window.
- **Not tested live**: Gemini CLI, Grok, Qwen Code and opencode weren't installed on the test PC. Their log readers are tested with sample logs in each tool's format (`tests/test_readers.py`).
- **Exact tab jump** was tested with stand-in agents, not with live Claude Code or Codex CLI tabs. It relies on the same console-title mechanism those CLIs use.
- **Ollama** doesn't log tokens per request. Local tokens appear only when the agent using Ollama logs them, as opencode does.
- **Status is inferred from logs.** "approve?" (needs approval) means the agent stopped on a tool call with no activity for 45 seconds, which is usually an approval prompt. A long, silent command can look the same.

## Files

```
run.bat                          start the panel (no console window)
agentboard/app.py                the window (pywebview) and the calls the page can make
agentboard/web/                  the page: index.html, styles.css, app.js
agentboard/web/vendor/           Geist fonts and Phosphor icons (both open licences), stored locally
agentboard/core/engine.py        refresh loop and the snapshot the page draws
agentboard/core/procs.py         finds agent processes, their host and their surface label
agentboard/core/usage.py         reads logs: tokens, limits, 5h, 7d and per-model windows, session status
agentboard/core/focus.py         jump to the window, the exact Windows Terminal tab or Claude app session
agentboard/core/claude_app.py    Claude app session records and its session link
agentboard/core/console_probe.py reads or temporarily marks a console's title
agentboard/core/wt_tabs.ps1      UI Automation helper for Windows Terminal tabs
agentboard/core/config.py        built-in agent types and your settings
agentboard/core/demo.py          made-up data for --demo
hooks/claude_statusline.py       optional Claude Code status line
tools/vendor.py                  re-downloads the fonts and icons
tests/test_readers.py            reader tests with sample logs
```
