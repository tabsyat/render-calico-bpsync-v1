# Render ↔ Calico Tabbycat Sync

A per-round sync tool between a Render-hosted Tabbycat instance (used for
draw generation) and a Calico-hosted Tabbycat instance (the public
tournament judges/teams interact with).

## Deploy your own copy

1. Click **Deploy to Render**

   [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

2. Render will read `render.yaml` and ask you for four values before it
   builds anything:
   - `RENDER_URL` — your Render Tabbycat instance's tournament API URL
     (e.g. `https://your-instance.onrender.com/api/v1/tournaments/your-slug`)
   - `RENDER_TOKEN` — your API token for that instance
   - `CALICO_URL` — your Calico tournament's API URL
     (e.g. `https://your-instance.calicotab.com/api/v1/tournaments/your-slug`)
   - `CALICO_TOKEN` — your API token for that instance

   Ensure No trailing slash on the URLs. (mandatory)

3. Click deploy. Once the build finishes, Render gives you a URL which is your private sync tool.

## Using it, once per round

Open your deployed URL and use the sidebar to move through the sections
in order:

1. **Import Teams** — run once only, before Round 1.
2. **Pull Results → Render** — after results are confirmed on Calico for
   the round.
3. **Review Pause** — go review the generated draw on Render's own UI,
   then tick the confirmation box.
4. **Push Draw → Calico** — pushes the reviewed draw to Calico as a
   **Draft**

## Local development

```bash
cp .env.example .env   # fill in your real values, never commit this file
pip install -r requirements.txt
streamlit run app.py
```

## Notes

- `team_map.json` is created by Section 1 and used by Sections 2 and 4 to
  map team IDs between the two instances. It's excluded from git — it's
  local/per-instance data, not code.
