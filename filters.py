"""
Two independent filters, per your scoping — do not merge these into one
function, they answer different questions:

  1. is_open_online(event)  -> online + registration open to anyone
  2. is_offline_india(event) -> onsite + happening in India

An event can only ever match one of the two (onsite is boolean), so a
caller runs both checks and routes to the right notification path.
"""

INDIA_MARKERS = ["india", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad",
                  "chennai", "pune", "kolkata", "ahmedabad"]  # crude, see note below


def is_open_online(event: dict) -> bool:
    if event["onsite"]:
        return False
    restrictions = (event.get("restrictions") or "").strip().lower()
    # CTFtime uses "Open" for anyone-can-join; academic/invitation-only are excluded.
    return restrictions == "open" or restrictions == ""


def is_offline_india(event: dict) -> bool:
    if not event["onsite"]:
        return False
    location = (event.get("location") or "").lower()
    if not location:
        return False
    # NOTE: CTFtime's `location` field is free text (e.g. "Bengaluru, India" or
    # just "India"), not a structured country code. A simple substring match on
    # "india" catches most cases cleanly. The city-name list above is a fallback
    # for events that list a city without the country -- verify a few real
    # entries against this before relying on it, free-text fields are messy.
    return any(marker in location for marker in INDIA_MARKERS)


def days_until_start(event, now) -> int:
    delta = event["start_time"] - now
    return delta.days