# Exotel setup

This page gets a real phone call running through Convox on Exotel. It covers the five values Convox needs, where each one comes from in the Exotel dashboard, and how to check them without spending credits. API details are from [developer.exotel.com/docs/voice](https://developer.exotel.com/docs/voice) and the [connect-to-flow reference](https://developer.exotel.com/docs/voice-v1/api-reference/connect-to-flow).

## How a Convox call uses Exotel

Convox places **two** outbound calls, one per leg, with `POST /v1/Accounts/{sid}/Calls/connect`. Each call connects its person to a call flow whose only applet is a **Voicebot** applet. The applet opens a websocket to Convox, and Convox joins the two legs itself. See [telephony integration](../architecture/telephony-integration.md).

The applet is configured with Convox's **dynamic URL endpoint**: `https://<public host>/telephony/exotel/stream`. Exotel calls it at answer time, and Convox returns `{"url": "wss://<public host>/ws/leg?call=…&side=…&sample-rate=16000"}`. Convox identifies the leg from `CustomField=<call_id>:<side>`, which it sends on every `Calls/connect`. If that is missing, it falls back to looking up the CallSid.

## Values

| `.env` | Where it comes from | Status |
|---|---|---|
| `EXOTEL_ACCOUNT_SID` | Dashboard → API Settings → Account SID | set (`rohan28`) |
| `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN` | API Settings → Create API key | set; auth verified 21 Oct |
| `EXOTEL_API_HOST` | Region: Singapore = `api.exotel.com` (default), India = `api.in.exotel.com` | default (Singapore) |
| `EXOTEL_EXOPHONE` | ExoPhones page: the virtual number used as `CallerId` | **needed**. The API lists none yet |
| `EXOTEL_FLOW_URL` | App Bazaar → create an app (flow) with one **Voicebot** applet → its app id → `http://my.exotel.com/rohan28/exoml/start_voice/<app_id>` | **needed** |
| `PUBLIC_BASE_URL`, `EDGE_WSS_BASE` | A public HTTPS host for convox. Exotel cannot reach localhost: use a tunnel (e.g. `cloudflared tunnel --url http://localhost:8000`) or the ap-south-1 VM | **needed** for real calls |
| `STT_IMPL`, `MT_IMPL`, `TTS_IMPL` | Set to `real` for live calls (default `fake`) | set when ready |

Optional: `EXOTEL_WS_BASIC_USER` / `EXOTEL_WS_BASIC_PASS` (Basic auth Exotel sends on the websocket) or `EXOTEL_ALLOWED_IPS`.

## Voicebot applet settings

- **URL:** `https://<public host>/telephony/exotel/stream` (dynamic endpoint, HTTPS).
- **Sample rate:** 16000 if the applet offers it. Convox reads the real rate from the `start` message and resamples 8 kHz if needed (open question 1).
- The Voicebot applet may need to be enabled on the account by Exotel (ask the sponsor channel or hello@exotel.com).

## Check without spending credits

```bash
uv run python tools/exotel_check.py
```

This checks the env values, auth (Balance.json), that the ExoPhone belongs to the account, the flow URL shape, and that `PUBLIC_BASE_URL/healthz` is reachable. A real test call comes after every line shows `ok`.

## Notes

- **Region and residency:** this account is in the **Singapore** region. The docs promise India-region hosting (see [security and privacy](../tech-spec/security-privacy.md)), and the Singapore media path may also add round-trip time. Ask Exotel whether the account can move to the India cluster (`api.in.exotel.com`).
- **Trial accounts** often only call verified numbers and play a trial message. Check the dashboard before the stage demo.
- `CallType=trans`, `TimeOut=25` (ring seconds, the 25 s no-answer rule) and `TimeLimit=3600` go on every leg.

## Related

- [Telephony integration](../architecture/telephony-integration.md)
- [Leg transports](../tech-spec/leg-transports.md)
- [Dev setup](dev-setup.md)
- [Deployment](deployment.md)
- [Open questions](../plan/open-questions.md)
