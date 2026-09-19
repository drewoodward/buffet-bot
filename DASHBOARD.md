# Dashboard

URL: https://claude.ai/artifact/6cVmJzutgsZTaikTKpiWma

The page renders the NEWEST document in the `reports` collection of that
artifact's database and re-renders when a new one lands. You do not republish
the page HTML each cycle -- you write a row. That is the whole update path.

## Writing a cycle report

Use the `ArtifactData` tool (load it with ToolSearch if it is not in your tool
list). One document per cycle, doc id = the cycle id:

```
ArtifactData  action:"set"
  url:        "https://claude.ai/artifact/6cVmJzutgsZTaikTKpiWma"
  collection: "reports"
  doc_id:     "<cycle_id>"
  data:       { ...the shape below... }
```

## Document shape

```jsonc
{
  "cycle_id": "premarket-2026-09-18T11:06:00Z",
  "phase": "premarket",              // or "postclose", "alpha", "manual"
  "generated_at": "<ISO 8601 UTC>",  // drives the freshness bar -- must be real
  "execution_mode": "paper",

  // One entry per source you health-checked. status: ok | degraded | down.
  // This is what makes a degraded cycle visible instead of silent.
  "sources": [ {"source":"Webull","status":"ok"},
               {"source":"Alpha Vantage","status":"degraded","detail":"free tier, 25/day"} ],

  // The five lines Andre reads first. tone: omit, or "warn" / "neg" to colour it.
  "matters": [ {"text":"...", "tone":"warn"}, {"text":"..."} ],

  "account": { "net_liquidation_value": 271.48, "cash": 271.48,
               "day_profit_loss": 0, "buying_power": 271.48 },
  "risk":    { "per_trade": 27.15, "per_ticker": 54.30, "daily_halt": -21.72 },

  "positions": [ {"ticker":"SOFI","quantity":10,"cost_basis":12.10,
                  "market_value":124.00,"unrealized_pl":3.00,
                  "thesis_age_days": 4} ],   // null -> renders red "no thesis"

  "signals": [ {"ticker":"QQQ","signal":"hold","timeframe":"daily",
                "confidence":"medium","entry_zone":[601.5,604.0],"stop":596.0,
                "targets":[612.0],
                "observed":["..."], "inferred":["..."],
                "invalidation":"a daily close below 596",
                "chart_url":"<asset url>"} ],

  "calls": [ {"ticker":"SOFI","direction":"long","raw_excerpt":"<Kevin's words>",
              "our_verdict":"agree",          // agree | disagree | mixed
              "our_reasoning":"<our independent read>"} ],
  "kevin_record": { "scored_calls": 12, "hit_rate_pct": 58.3 },

  "flags": [ {"kind":"no thesis","text":"NIO held since 8 Sep with no written thesis",
              "severity":"high"} ]
}
```

## Charts

**Upload PNG, never SVG.** An uploaded SVG is sanitized, which can strip an
embedded `<style>` block; an SVG without its own styles renders as a solid
black rectangle, because an SVG in an `<img>` cannot see the page's CSS. This
already happened once. PNG cannot fail this way.

When capture works, TradingView's `capture_screenshot` writes a PNG on mochi.
When it does not, `lib/charts.py` draws one from TradingView's bar data and
`cairosvg` converts it to PNG. Either way the dashboard gets a PNG.

To get a captured file onto the page:

1. Stage it into the cloud container: `device_stage_files` with the PNG's path.
2. Upload it as an artifact asset:
   `Artifact  action:"publish"  url:"<dashboard url>"  file_path:"<staged path>"  asset:true`
3. Put the returned `url` into that signal's `chart_url`, exactly as returned.

Several charts upload in one call via `file_paths`. Do this once per cycle rather
than per ticker.

## Rules

- `generated_at` is the real generation time. The page computes staleness from
  it and changes state when a cycle is old. Backdating or reusing a timestamp
  defeats the one guard that stops Andre reading yesterday's numbers as today's.
- Write the report row BEFORE sending the Telegram ping, so the link in the ping
  leads to a page that already has the content.
- A source that failed goes in `sources` with `status:"down"` and the error in
  `detail`. Never omit a source because it failed -- an omitted source looks
  like a source that was never needed.

## Capacity

The artifact database holds 5,000 documents. At one report per cycle and two
cycles a day that is roughly four years, but the post-close cycle should prune
`reports` documents older than 90 days so it never becomes a problem. Keep the
month-end report of each month as a record.

## The subscription contract, because it fails silently

The page subscribes with `onSnapshot`, which delivers a QuerySnapshot object --
NOT an array -- and each entry's body comes from calling `data()`, not reading
it as a property. Treating the snapshot as an array does not throw. The callback
simply finds nothing it recognises and the page keeps showing its example view
while real cycles write rows nobody ever sees. If the dashboard ever looks
frozen on old content while `reports` has new rows, check that first.
