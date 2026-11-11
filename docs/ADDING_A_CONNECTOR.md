# Adding a connector

A connector gives Jarvis tools for one external service. It is **one folder with two files**:

```
services/brain/src/jarvis_brain/connectors/<your_id>/
  manifest.yaml   # what it is, how it authenticates, which tools it exposes and their side effects
  connector.py    # create(ctx) -> object with tools() (and optionally now())
```

Copy `connectors/_template/` (a working Home Assistant example — folders starting with `_` are not
loaded) and edit. Gmail (`connectors/gmail/`) and Google Calendar (`connectors/google_calendar/`) are
complete OAuth examples. Budget: ~20 minutes for a REST API with an API key.

## 1. manifest.yaml

```yaml
id: home-assistant            # lowercase; the OAuth token and admin page use it
name: Home Assistant
type: connector
description: One line for the admin page.
auth: api_key                 # none | api_key | oauth2
required_secrets: [HOME_ASSISTANT_URL, HOME_ASSISTANT_TOKEN]
enabled: true                 # default; can be toggled in admin
tools:
  - {name: ha_state, side_effect: none}
  - {name: ha_turn_on, side_effect: reversible}
  - {name: ha_unlock_door, side_effect: irreversible}
```

For OAuth add a provider and scopes (Google is built in; others go in `connectors/oauth.py`
`PROVIDERS`, which is three URLs and the names of the client-id/secret secrets):

```yaml
auth: oauth2
required_secrets: [GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]
oauth: {provider: google, scopes: [https://www.googleapis.com/auth/drive.readonly]}
```

### Side effects — choose carefully

| Level | Meaning | What Jarvis does |
|---|---|---|
| `none` | reads only | runs it |
| `reversible` | changes something you can undo (draft, note, light on) | runs it |
| `irreversible` | can't be undone or affects other people (send, delete, buy, post, unlock) | **reads back what it's about to do and waits for a spoken "yes"** |

The loader **refuses** a connector whose code and manifest disagree (an undeclared tool or a
different level), and admin overrides can only make a tool stricter, never looser.

## 2. connector.py

```python
from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field
from ...tools.registry import Tool, ToolContext
from ..base import ConnectorContext


class Args(BaseModel):  # becomes the JSON schema the model sees
    entity_id: str = Field(description="e.g. 'light.kitchen'")


class MyConnector:
    def __init__(self, ctx: ConnectorContext):
        self.ctx = ctx  # .secrets, .oauth, .config, .http(), await .bearer()

    async def turn_on(self, a: Args, _c: ToolContext) -> dict:
        async with self.ctx.http() as c:  # httpx.AsyncClient (mockable in tests)
            r = await c.post(..., headers=await self.ctx.bearer())  # OAuth: auto-refreshing token
            r.raise_for_status()
            return {"ok": True}  # anything JSON-able; keep it small

    async def now(self) -> dict:  # optional — fields of the UI "Now" card:
        return {}  # next_event_title/start/location, unread_important

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "ha_turn_on",
                "Turn on a light, switch or scene.",
                self.turn_on,
                params=Args,
                side_effect=SideEffect.REVERSIBLE,
                label="Home",
                icon="home",
                describe=lambda a: f"turn on {a['entity_id']}",
            )
        ]  # spoken read-back


def create(ctx: ConnectorContext) -> MyConnector:
    return MyConnector(ctx)
```

Guidelines:
- **Tool names** must match `^[a-zA-Z0-9_-]{1,64}$`; prefix them with the service (`ha_`, `gmail_`).
- **Descriptions** are for the model: say what it does and when to use it, in one sentence.
- **Inputs** are validated against your pydantic model before your handler runs; constrain them.
- **Outputs** go back to the model, which then speaks; return the facts, not prose, and trim large
  payloads (a few KB at most).
- **`describe(args)`** is required in practice for `irreversible` tools — it's what Jarvis reads
  back: "Just to confirm, I'm about to *unlock lock.front_door*. Shall I go ahead?"
- Raise on failure (`r.raise_for_status()`); the registry turns exceptions into a tool error the
  model can explain.

## 3. Secrets & connecting

1. Admin → **Secrets**: set every name in `required_secrets` (stored encrypted).
2. Admin → **Tools & Connectors**: enable it. API-key connectors become active as soon as their
   secrets exist; OAuth connectors show **Connect**.

### OAuth redirect URI (Google)

Google only accepts `https://` redirect URIs or loopback. Pick one:
- **Recommended:** expose admin over HTTPS on your tailnet with `tailscale serve --bg --https=443
  http://127.0.0.1:9000`, set Connectors → *Redirect URI* to
  `https://<host>.<tailnet>.ts.net/api/connectors/oauth/callback`, and add that URI to the Google
  OAuth client (type "Web application").
- **No HTTPS:** create a "Desktop app" OAuth client, leave the redirect URI as
  `http://127.0.0.1:9000/api/connectors/oauth/callback`. If your browser isn't on the brain host the
  final redirect page won't load — copy its URL from the address bar and paste it into
  *Complete sign-in* on the Connectors page.

Google Cloud console steps: create a project → enable the Gmail API and Google Calendar API → OAuth
consent screen (External, add yourself as a test user) → Credentials → OAuth client ID. Put the client
ID/secret into `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.

## 4. Test it

Mock the HTTP layer — the registry accepts an `http` factory:

```python
http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
connectors = ConnectorRegistry(config, secrets, oauth, registry, http=http)
```

See `services/brain/tests/test_connectors.py` (Gmail send with threading, calendar read/create, the
"what's on my calendar tomorrow?" flow end to end). Then restart the brain (`make restart`) —
connectors are discovered at start — and try it by voice.
