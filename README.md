# Tour Radar

Tour Radar is an agent that plans a tour for an independent musician. You name a few acts you sound like, and it uses the [Qloo Taste AI API](https://docs.qloo.com/) to find where people with that taste are over-represented: which cities, and which neighborhoods inside them. It then builds a route and explains every stop.

> Spotify for Artists tells you where your fans are — if you already have them. Tour Radar tells you where they will be.

Work in progress for the Qloo Agentic Hackathon.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # add your keys, or keep QLOO_MOCK=1
uvicorn app.main:app --reload --port 7860
```

Open http://localhost:7860.

## License

MIT
