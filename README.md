# ⚖️ Small Claims

**Put any sentence from the internet on trial.**

Paste a claim or a link. Three open-weight models take the stand: the
**prosecution** argues it's false, the **defense** argues it's true, both bring
evidence from the web, and the **judge** rules. About a minute, a few cents,
and you get a verdict card you can share.

Built for MozFest **Developers Wilding: Online** on the hosted
[Otari](https://otari.ai) gateway. Hackathon branch: `devs-wilding`.

## Every Otari feature is part of the trial

| In the courtroom | Otari feature |
|---|---|
| Prosecution, defense and judge are three open-weight models from different families, named on the bench | Multiple models serving distinct roles |
| Each side "submits evidence" it found on the web; a link you paste is read into the record as Exhibit A | Web search (`otari_web_search`), web fetch (`POST /fetch`) |
| Every exhibit passes a prompt-injection check before it's admitted. One that tries to give the judge orders is **excluded for tampering with the court** | Guardrails (`prompt-injection`, block mode) |
| When the sides fight about numbers, the **clerk** settles it in a sandbox and shows the code | Code execution (`otari_code_execution`) |
| For claims about software, the judge **consults the case files** | MCP (DeepWiki, `mcp_servers`) |
| If counsel's model doesn't show up, **substitute counsel takes the floor**, and the page says who | Routing and fallbacks (a substitute list per role, or an Otari routing policy; `Otari-Model` / `Otari-Attempt-Count` say who answered) |
| Each trial has a **filing fee** bar from Otari's own per-request cost. At 80% the defense gets a **public defender** (a cheaper model); at 100%, or when Otari answers `budget_exceeded`, court adjourns with a provisional ruling | Budgets and spending limits (`usage.cost_usd`, `403 budget_exceeded`) |
| Each visitor is an Otari end user (`user` on every call) with their own daily budget; when one runs out, others keep going | Per-user budgets (service key + end-user budgets) |

Every verdict card has a **court record**: the list of Otari calls behind the
trial, with the model, the tools and the cost of each.

## Run it

```bash
python -m smallclaims --mock      # demo mode: scripted court, no key, no cost
```

Open http://localhost:8812 and try the three example cases. Demo mode shows a
yellow banner, and its verdict cards and court records say DEMO: nothing in it
is a real model or a real Otari call.

```bash
export OTARI_API_KEY=otk_...      # from otari.ai → API keys; read from the environment only
python -m smallclaims             # live on otari.ai
```

At startup it reads your model catalog and keeps the configured cast
(`smallclaims/court.py`, `DEFAULT_CAST`), filling any gap with a model from the
right family. Override the cast, or use your own routing policy names, in
`config.local.json`:

```json
{"cast": {"defense": ["court-defense"]}, "end_user_budget": null}
```

Options: `--port 8812`, `--daily-trials 3`, `--fee-cap 0.05`.

Standard library only, Python 3.11+. Tests: `python -m unittest discover -s tests -t .`

## License

Apache-2.0
