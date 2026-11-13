"""Suggest the ID column, the churn target and the columns to leave out of the model.

Pure Python over the profile, so every rule is unit-testable. Each suggestion carries a plain reason
because a person confirms it on the review page. Target detection is name-based on purpose: a column
with two values is not automatically a churn label (Sales Channel = Online/Offline is not).
"""
import re

TARGET_WORDS = {"churn", "churned", "exited", "exit", "attrition", "left", "cancelled", "canceled",
                "cancel", "churn flag", "is churn", "target"}
OUTCOME_WORD = re.compile(r"\b(churn\w*|exited|attrition|cancel\w*)\b")
ID_WORDS = re.compile(r"\b(id|key|number|no|code|row)\b")
CODE_WORDS = re.compile(r"\b(zip|zip code|postal|postcode|pin ?code|area code|code)\b")
PERSONAL = re.compile(r"\b(surname|first name|last name|full name|customer name|name|e ?mail|"
                      r"phone number|mobile number|phone no|street|address line|postcode|ssn|passport|"
                      r"date of birth|dob)\b")
POSITIVE = ["yes", "1", "true", "y", "churned", "exited", "left", "cancelled", "canceled"]
MAX_CATEGORIES = 50
UNIQUE_SHARE = 0.95


def words(name):
    """'CustomerId' -> 'customer id', 'Usage Frequency' -> 'usage frequency'."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return re.sub(r"[^a-z0-9]+", " ", spaced.lower()).strip()


def is_target_name(name):
    w = words(name)
    return w in TARGET_WORDS or w.replace(" ", "") in {t.replace(" ", "") for t in TARGET_WORDS} \
        or w in {"churn label", "churn value", "churn status"}


def positive_value(values):
    """Which of the two target values means 'left'. values: the two distinct raw values."""
    low = {str(v).strip().lower(): v for v in values}
    for p in POSITIVE:
        if p in low:
            return low[p]
    return None


def _unique(col, rows):
    return col["blank"] == 0 and col["distinct"] == rows and rows > 0


def suggest(profile):
    rows, cols = profile["rows"], profile["columns"]
    by_key = {c["key"]: c for c in cols}

    # Target: outcome name + exactly two values. Prefer 0/1 or Yes/No style, then the shortest name.
    candidates = [c for c in cols if is_target_name(c["name"]) and c["distinct"] == 2 and c["blank"] == 0]
    candidates.sort(key=lambda c: (positive_value([v for v, _ in c["top"]]) is None, len(c["name"])))
    target = candidates[0] if candidates else None

    # ID: unique on every row; prefer names with id/key/number, then "customer" in the name.
    ids = [c for c in cols if _unique(c, rows) and c["kind"] in ("text", "number")
           and (c["kind"] == "text" or c.get("integer"))]
    ids.sort(key=lambda c: (not ID_WORDS.search(words(c["name"])),
                            "customer" not in words(c["name"]), c["key"]))
    id_col = ids[0] if ids else None

    exclude = {}

    def leave_out(c, kind, reason):
        if c["key"] not in exclude and c is not target and c is not id_col:
            exclude[c["key"]] = {"kind": kind, "reason": reason}

    for c in cols:
        w = words(c["name"])
        if c["kind"] == "empty" or c["distinct"] <= 1:
            leave_out(c, "constant", "Has only one value, so it cannot explain anything.")
        elif target is not None and OUTCOME_WORD.search(w):
            leave_out(c, "leakage", "Its name refers to the outcome, so it is probably recorded after or "
                                    "because the customer left. Using it would fake a perfect model.")
        elif c["kind"] == "text" and c["distinct"] > 20 and PERSONAL.search(w):
            leave_out(c, "personal", "Looks like personal information. It is not used and its values are not shown.")
        elif c["kind"] == "date":
            leave_out(c, "date", "Dates are shown in the exploration but not used by the model.")
        elif (c["distinct"] >= UNIQUE_SHARE * rows and rows >= 20
              and (c["kind"] == "text" or (c.get("integer") and ID_WORDS.search(w)))):
            leave_out(c, "identifier", "Different on (almost) every row, like an ID. It cannot generalise to new customers.")
        elif (c["kind"] == "number" and c["distinct"] > MAX_CATEGORIES
              and (CODE_WORDS.search(w) or (c.get("integer") and ID_WORDS.search(w)))):
            leave_out(c, "code", "A code (like a ZIP code or account number), not a quantity; its size means nothing.")
        elif c["kind"] == "text" and c["distinct"] > MAX_CATEGORIES:
            leave_out(c, "categories", f"Text with {c['distinct']:,} different values; too many categories to learn from.")

    if id_col is None:
        id_note = "No column is unique on every row, so customers are identified by row number."
    else:
        id_note = f"{id_col['name']} is filled and unique on all {rows:,} rows."
    if target is None:
        target_note = ("No column looks like a churn outcome (a name like Churn, Exited or Attrition with two values). "
                       "You can still pick one, or use the exploration only.")
        positive = None
    else:
        positive = positive_value([v for v, _ in target["top"]])
        if positive is None:  # neither value reads as 'left': suggest the smaller group
            positive = min(target["top"], key=lambda t: t[1])[0]
        target_note = f"{target['name']} has two values; {positive!r} is taken to mean the customer left."

    hidden = {k for k, e in exclude.items() if e["kind"] in ("personal", "identifier")}
    if id_col is not None:
        hidden.add(id_col["key"])
    return {
        "target": target and target["key"], "positive": positive, "target_note": target_note,
        "id": id_col and id_col["key"], "id_note": id_note,
        "exclude": exclude,
        # Values of these columns are never stored or shown (privacy); counts still are.
        "hide_values": sorted(hidden),
        "is_churn_data": target is not None,
        # Columns the model would use with these suggestions.
        "features": [k for k in by_key if k not in exclude and k not in {target and target["key"], id_col and id_col["key"]}],
    }


def redact(profile, hidden):
    """Drop sample values of hidden columns before the profile is stored."""
    for c in profile["columns"]:
        if c["key"] in hidden:
            c["top"] = []
            c["values_hidden"] = True
    return profile
