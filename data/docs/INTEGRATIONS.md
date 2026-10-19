# Integrations

Outgoing: notifications (V-041, first part below). Replies from Discord, Slack and ntfy: commands from your chat (V-050, second part).
Incoming work: GitHub, Jira and Linear work as Bounty Board proposals and CI as an extra check line (V-044, third part).

# Notifications (Discord, Slack, ntfy, webhook) - V-041

Vanilla Agents can tell you on your phone or in a chat when something needs you. Open **Options > Notifications** (or Help (F1) >
Notifications), switch on the channels you use, tick what each one should tell you, press **Send test**, then **Save**.

## What can notify
| Tick box | Sent when | Example |
|---|---|---|
| Needs you | a helper waits for a permission, a question or your reply (Claude Code's Notification / PermissionRequest hooks) | `Varn needs you: permission for Bash` |
| Quest ready for your yes | a quest is handed in (`task_ready`) | `Quest ready for your approval: Fix the login` |
| Quest done | the checker verified it, or it was marked done in another session | `Quest done: Fix the login` |
| A check failed | the checker failed a quest | `Check failed: Fix the login` |
| A helper is stunned | Claude's service stopped a turn (rate limit, overload, billing) | `Varn is stunned: rate_limit` |
| Party plan agreed | everyone in your party agreed and the plan locked | `Party plan agreed: Raid night (4 items)` |

New channels start with *Needs you*, *Quest ready* and *A check failed* ticked.

## Channels
- **Discord**: in the channel's settings, Integrations > Webhooks > New webhook > Copy webhook URL. Paste it as the Link
  (`https://discord.com/api/webhooks/...`). Mentions are switched off in what we send.
- **Slack**: make a Slack app with an Incoming Webhook (api.slack.com/apps), paste its `https://hooks.slack.com/...` link.
- **ntfy**: install the ntfy app, subscribe to a topic with a hard-to-guess name (anyone who knows the name can read it) and type
  the same topic here. Server defaults to `https://ntfy.sh`; your own ntfy server works too (https only).
- **Webhook (JSON)**: any `https://` (or `http://`, e.g. Home Assistant on your network) address. It receives a POST with
  `{"source": "vanilla-agents", "event": "<tick box name>", "text": "<the sentence>", "ts": <unix seconds>}`.
  Event names: `needs_you`, `quest_ready`, `quest_done`, `check_failed`, `stunned`, `plan_agreed` (`test` for Send test).

## What is sent, and what never is
Only the short sentences above: a helper's hero name, a quest title (cut at 80 characters), a tool NAME, a short reason word.
Never code, file contents, file paths, commands, tool output, whispers, keys or the window link.

## How it behaves
- Settings live in your own settings file (`%USERPROFILE%\.vanilla-agents\work\settings.json`, key `notify`), never in the repository.
- The server reads the event feed every 2 seconds on its own thread; only events after the window started notify (no flood of old ones).
- Sending happens on a separate background thread (6 s timeout, at most 2 more tries for network errors, 429 and 5xx; a 4xx is not retried),
  so nothing ever waits for Discord or Slack. The same sentence goes to the same channel at most once a minute.
- Nothing is sent while Vanilla Agents is closed.
- Code: `vanilla_agents/app/notify.py` (sentences in `message_for`), window page `app/web/js/vasettings.js`, tests `tests/test_notify.py`.

## Phone (V-040)
For pressing Approve / Pause / Verify from your phone rather than just hearing about it, see **Options > Phone**: switch on Phone access,
scan the QR code. Your phone must be on the same home network. Anyone with that link can approve, so keep it to yourself; it changes each
time Vanilla Agents starts. Details: docs/LIMITATIONS.md (Phone).

# Chat replies (Discord, Slack, ntfy) - V-050

Answer from your phone: open **Options > Chat replies**, switch on a service, fill it in, press **Test** (it reads the channel once
and posts a hello there), then **Save**. Vanilla Agents reads the channel every few seconds (no public server, no webhook into your
PC: it polls the service's normal API) and answers in the same channel.

## Commands (plain text in the channel)
| Type | What happens |
|---|---|
| `@Varn please add a test` or `/w Varn please add a test` | whispers that helper (hero name or id, any case; names with spaces work, the longest match wins). The helper gets `[Discord] please add a test` through the same whisper channel as the window's chat line. Reply: `Whispered to Varn.` |
| `/p stop and save` or `@everyone stop and save` (Slack: `@channel` / `@here` too) | whispers every helper |
| `status` | `5 working (Varn, Tess, ...), 1 idle, 1 needs you (Mira). Waiting for your approval: Fix the login. The checker is paused.` Helper names and quest titles only (titles with paths or file names are cut out). |
| `pause` / `resume` | the checker's Pause (same as the action bar) - **only if you ticked Allow pause / resume from chat** for that service (off by default); otherwise `Pause from chat is switched off`. |
| `help` | the list of commands |
| `approve ...`, `yes`, `accept` | nothing happens; reply `Approvals are only in the window or on the phone link.` |
| `verify`, `done`, `pick`, `resurrect`, `settings`, `merge`, `veto`, `hearth`... | nothing happens; reply `That can only be done in the Vanilla Agents window.` |

Approving, Verify, marking done, picking a competing fix, Resurrect and every setting are never possible from chat (there is no code
path from chat replies to them; test `tests/test_chat_replies.py`). When a helper answers a whisper that came from chat (its
`message_answered`), the answer goes back to that channel as `Varn: <answer>` for an hour, or until you whisper that helper from the
window; code blocks, `code`, file names, paths, links and command lines are taken out (`Varn answered with code or file details; see
the window.` when nothing is left). At most 10 commands are acted on per read; more are skipped.

## Who may command
- **Discord / Slack:** only messages from the user ids on **Who may command** (comma separated, up to 20). Everyone else is ignored and
  only counted in the status line (`Ignored 3 from people not on your list.`); bots (including our own replies) and system messages
  (joins, pins) are skipped.
- **ntfy** has no accounts: a command must start with your **passphrase** and a colon, e.g. `pw123: status` (compared in constant time).
  Our own replies on the topic are recognised and skipped. Pick a hard-to-guess reply topic too: anyone who knows it can read the replies.

## Setting up
- **Discord bot:** discord.com/developers/applications > New Application > **Bot**: Reset Token and copy it (that is the bot token);
  switch on **Message Content Intent** (without it messages arrive without text; the status line says so). Invite it: OAuth2 > URL
  Generator, scope `bot`, permissions View Channels, Read Message History, Send Messages; open the link and pick your server.
  Channel id: Discord settings > Advanced > Developer Mode on, then right-click the channel > Copy Channel ID. Your user id: right-click
  yourself > Copy User ID. Calls: `GET https://discord.com/api/v10/channels/{id}/messages?after={last id}&limit=50` and
  `POST .../channels/{id}/messages` (mentions switched off) with `Authorization: Bot <token>`; `GET .../channels/{id}` once for the name.
- **Slack app:** api.slack.com/apps > Create New App > From scratch > OAuth & Permissions > Bot Token Scopes `channels:history` and
  `chat:write` (`groups:history` for a private channel) > Install to Workspace > copy the **Bot User OAuth Token** (`xoxb-...`). In the
  channel type `/invite @your-app`. Channel id: channel name > About, at the bottom (`C0123ABCD`). Member id: your profile > More >
  Copy member ID (`U0123ABCD`). Calls: `GET https://slack.com/api/conversations.history?channel=&oldest=&limit=100` and
  `POST https://slack.com/api/chat.postMessage` (text escaped, so a reply can never be an @channel mention or a link).
- **ntfy:** in the ntfy app subscribe to a NEW topic for replies (not your notification topic), e.g. `my-inn-replies-7f3k2`; type it as
  **Reply topic** and set a passphrase. Server defaults to `https://ntfy.sh` (your own server works, https only). Calls:
  `GET <server>/<topic>/json?poll=1&since=<last id>` and `POST <server>/<topic>` (title "Vanilla Agents").

## How it behaves
- **Read every** 5 to 120 seconds (default 10) per service. A `429` waits as long as the service says (Discord's `retry_after`, Slack's
  and ntfy's `Retry-After`, at most 10 minutes) and Discord's `X-RateLimit-Remaining: 0` waits `X-RateLimit-Reset-After`. Every call
  has an 8 s timeout; a reply gets at most one more try (network trouble, 5xx). Garbled answers are plain errors in the status line.
- One background thread, started only when a service is switched on (at start and on Save); it stops when all are off or Vanilla
  Agents closes. Nothing is read while Vanilla Agents is closed. Messages written in the last 10 minutes before it starts again are acted on;
  older ones (an hours-old "pause" or whisper) are skipped and counted as ignored (the last message id per channel is kept in
  `%USERPROFILE%\.vanilla-agents\work\chat-replies.json`). Switching a service on for the first time, or changing the channel or
  topic, starts from now: history is never acted on.
- **Status line** per service: `Listening in #vanilla-agents. Last command 2 min ago. Ignored 1 from people not on your list.` or the
  problem in plain words (`discord.com answered 403: access was refused...`, `the Slack app is not in that channel (type /invite...)`).
- Secrets (bot tokens, the passphrase) live in your settings file (key `chat`) and nowhere else: the window gets only "saved, ends in
  1a2b", an empty field keeps the saved one, **Forget** removes it; never in a GET answer, an error, a log line or Help > Save
  diagnostics (which only says on/off and the last error).
- Code: `vanilla_agents/integrations/chatsvc.py` (the three services), `chatcfg.py` (settings, cursors), `vanilla_agents/app/chatcommands.py` (what a message means, who may
  give it, the status line), `vanilla_agents/app/chatreplies.py` (the poller, the reply sentences in `R`, server calls GET/POST `/api/chat`, POST `/api/chat/test`), window
  `app/web/js/chatreplies.js`; tests `tests/test_chat_replies.py` (fake opener only, no network).

## Developer fake services (dev only)
`python scripts/demo_chat_fake.py --port 8972`, then start the server with `VANILLA_CHAT_FAKE=http://127.0.0.1:8972`: every chat-replies
call goes to that local fake Discord / Slack / ntfy (only 127.0.0.1 / localhost is accepted) and Options > Chat replies shows a warning.
`POST /say {"service": "discord", "user": "<id>", "text": "@Varn hi"}` on the fake writes a message as you; `GET /sent` lists the replies.
Never set it for real use.

# Incoming work: GitHub, Jira, Linear and CI - V-044

Open **Options > Integrations** (or the **Integrations** button on the Bounty Board's Imported tab). Everything is off until you switch it on.

## The rule: imports are proposals
Imported issues and pull requests appear on the **Bounty Board (H) > Imported** tab with a source badge (GitHub cat, Jira sapphire,
Linear amethyst), their key (`#128`, `INN-12`), a short excerpt and **Open link**. Helpers cannot see or take them: they are not in
the event feed, not in the quest list the Vanilla Agents tools (MCP) return, and no tool can import, refresh or make a quest from one
(test: `tests/test_mcp.py`). Only your click on **Make it a quest** (POST `/api/bounty/quest`, window key) creates a quest: a normal,
unapproved quest titled like the item, whose why starts with the source and the link, e.g.
`GitHub #128: https://github.com/o/r/issues/128 - Steps: buy a Light Ale...` (PRs: `GitHub PR #140: ...`, `Jira INN-12: ...`,
`Linear ENG-4: ...`; a CSV row without a link says `from an imported file`). You approve it in the quest log as usual. **Hide** takes a
proposal off the board for good (nothing changes upstream).

- **Dedupe:** one bounty per link (an item without a link, e.g. a Jira CSV row with no site set, is keyed `jira:INN-12`). A link that
  already has a quest (its why names the link) never shows again, also if the quest was made in another window.
- **Closed upstream:** every refresh replaces that source's list; an issue closed (or no longer matching your filter) leaves the board.
  The quest made from it stays. A failed refresh keeps the last list.

## GitHub
- **Which repository:** read from the open project's `.git/config` (no git command): remote `origin`, then `upstream`, then any other
  remote on github.com or a host whose name contains `github` (GitHub Enterprise); for another Enterprise name fill in *Enterprise host*.
  Linked worktrees read the main checkout's config. Remote forms: `https://`, `ssh://`, `git@host:owner/repo.git`, `git://`.
- **Sign in with the gh command** (recommended): install GitHub CLI and run `gh auth login` once. Vanilla Agents calls
  `gh issue list -R owner/repo --state open --json number,title,body,labels,url --limit N [--label X]`, `gh pr list ... [--search
  review-requested:@me | --assignee @me | --author @me]`, `gh run list -R owner/repo --branch <branch> --json ...` and `gh api user`,
  always as an argument list with a timeout, prompts off. No token is stored by Vanilla Agents in this mode.
- **Or a token:** a fine-grained personal access token with read-only *Issues*, *Pull requests* and *Actions* for the repository.
  REST calls go to `https://api.github.com` (Enterprise: `https://<host>/api/v3`) with `Authorization: Bearer <token>`.
  The token is only sent when the remote is on github.com or on the *Enterprise host* you typed (V-048): a remote that merely has `github` in its name gets an error, not your token.
- **What:** open issues (all, or only those with one of up to 5 labels; one call per label) and pull requests (none / waiting for my
  review / assigned to me / opened by me / all open), at most `app.json#integrations.max_items` each.
- **When:** *Refresh now*, and every *Refresh every* minutes (default 10, `app.json#integrations.refresh_minutes`) while switched on.
- **Status line:** "Signed in as octo via gh. 4 on the Bounty Board." or the plain problem (gh missing, not signed in, timeout,
  no GitHub remote, token refused...). *Test* signs in with what is typed (before Save) and imports nothing.

## CI as a check (read-only)
When GitHub import is on and the project has `.github/workflows/*.yml`, the newest run of each workflow on the current branch (from
`.git/HEAD`) is read every 2 minutes (`app.json#integrations.ci_seconds`). Summary: any failed -> **failed**, else any running ->
**running**, else **passed** (cancelled-only -> cancelled). It is shown as `CI: build passed 4 min ago` under quests in progress or
ready in the quest log (red with a red edge when failed) and as a **CI** row in the system monitor. It is extra evidence only: Verify
still runs your own checker and the verify contract is unchanged (snapshot field `ci`; `null` when off).

## Jira and Linear
- **Import from file** (no keys): Jira *Filters > Export > CSV (all fields or current fields)*; Linear *Settings > Import / Export >
  Export CSV*. Recognised columns: Jira `Summary`, `Issue key`, `Description`, `Status`, `Status Category`, `Resolution`, `Labels`
  (repeated); Linear `ID`, `Title`, `Description`, `Status`, `Labels`, `Completed`, `Canceled`; a `URL` / `Link` column is used as the
  link when present, and Jira rows get `<site>/browse/<key>` when the Jira site is filled in. Done / closed / resolved / canceled rows
  are skipped. Quoted line breaks, a byte order mark, `;` or tab separators are fine. Files over 2 MB
  (`app.json#integrations.max_file_bytes`) are refused. A new file from the same tool replaces the last file's items.
- **Linear by API key** (optional): personal API key (Linear > Settings > Security & access). GraphQL `https://api.linear.app/graphql`,
  open issues (not completed / canceled), optionally one team key and only yours.
- **Jira Cloud by API token** (optional): site `https://<team>.atlassian.net`, your e-mail and an API token from id.atlassian.com;
  REST `/rest/api/3/search/jql` with your JQL (default: assigned to you and not done), basic auth.

## Secrets
Tokens and keys are kept in your settings file (`%USERPROFILE%\.vanilla-agents\work\settings.json`, key `integrations`) and nowhere
else. The window never gets them back (only "saved, ends in 1a2b"); an empty field keeps the saved one, *Forget* removes it. They are
sent only to their own service, never logged, never in error messages (scrubbed), never in Help > Save diagnostics (which only says
on/off, how, item counts and the last error). Imported items (titles, excerpts, links) are kept per project in
`%USERPROFILE%\.vanilla-agents\work\integrations\<hash>.json`.

## Developer demo data (dev only)
`VANILLA_INTEGRATIONS_FAKE=scripts/demo_integrations.json python -m vanilla_agents.app.server --feed <f> --fake-verify --show-key`:
GitHub answers from that JSON file (issues, PRs, runs; repository and branch used when the project has no GitHub remote) instead of gh
or the network, and Options > Integrations shows a warning. For screenshots and trying the flow; never set it for real use.

Code: `vanilla_agents/integrations/` (`gitremote`, `github`, `httpjson`, `csvimport`, `linear`, `jira`, `isettings`, `board`, `hub`),
server calls `vanilla_agents/app/integrations_api.py`, window `app/web/js/integrations.js`, `panels/auction.js`, `ci.js`; tests
`tests/test_integrations_sources.py`, `tests/test_integrations_hub.py` (fake runner and fake HTTP only).
