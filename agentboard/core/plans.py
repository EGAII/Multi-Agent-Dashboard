"""Subscription plans you can pick for a monitor, for an estimated 5h / 7d / daily percentage when the agent
does not report its own limit usage on this PC.

Only numbers the vendors publish are here, as of AS_OF. Where a vendor gives a range, both ends are kept.
Vendors change these often and some publish nothing, so every monitor also offers "Auto" (limits learned
from the official readings you had) and "Custom" (your own numbers). Units:
  tokens    new input plus output tokens
  requests  replies from the model (one prompt can take several)
  prompts   messages you typed
Windows: "5h", "7d" and "1d" (a day, reset at midnight Pacific time, as Google does).
"""

AS_OF = "October 2026"

PLANS = {
    "claude": {
        "note": "Anthropic publishes no token or message counts for Pro or Max, only that Max 5x and Max 20x give "
                "5x and 20x Pro per 5-hour session. Auto learns your limits from Claude Code's official "
                "readings (the status line); Custom takes your own numbers.",
        "plans": [],
    },
    "codex": {
        "note": "OpenAI publishes local-message ranges per 5 hours for Plus and Business, which differ by model. "
                "Weekly limits are not published.",
        "plans": [
            {"id": "plus", "label": "Plus", "desc": "15–160 messages per 5h (GPT-6.1 Sol)",
             "limits": {"5h": {"unit": "prompts", "range": [15, 160]}}},
            {"id": "business", "label": "Business", "desc": "Same as Plus: 15–160 messages per 5h",
             "limits": {"5h": {"unit": "prompts", "range": [15, 160]}}},
            {"id": "pro", "label": "Pro", "desc": "No 5-hour limit; weekly limit not published",
             "limits": {"5h": {"none": True}}},
        ],
    },
    "gemini": {
        "note": "Google publishes daily request quotas for Gemini CLI, shared with Code Assist agent mode and reset "
                "at midnight Pacific. One prompt can take several requests.",
        "plans": [
            {"id": "standard", "label": "Code Assist Standard", "short": "Standard", "desc": "1,500 requests a day",
             "limits": {"1d": {"unit": "requests", "value": 1500}}},
            {"id": "enterprise", "label": "Code Assist Enterprise", "short": "Enterprise", "desc": "2,000 requests a day",
             "limits": {"1d": {"unit": "requests", "value": 2000}}},
            {"id": "free", "label": "Free (Google login)", "short": "Free", "desc": "1,000 requests a day, where Google still serves it",
             "limits": {"1d": {"unit": "requests", "value": 1000}}},
        ],
    },
    "grok": {
        "note": "xAI publishes no numbers: Grok plans share one weekly pool across Chat, Imagine, Build and the API.",
        "plans": [],
    },
    "qwen": {
        "note": "The free Qwen OAuth tier closed on 15 April 2026, and Coding Plan limits are not published.",
        "plans": [],
    },
}


def for_type(type_id):
    """Plans and a note for one agent type (an empty list for agents without published plans)."""
    p = PLANS.get(type_id) or {"note": "", "plans": []}
    return {"as_of": AS_OF, "note": p["note"], "plans": p["plans"]}
