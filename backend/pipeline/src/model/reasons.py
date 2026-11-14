from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StringType

REASON_COUNT = 3
NAME_MAX = 40  # a column name repeats in every scored row; long ones made scores ~21x the upload (audit #7)


def _short(text):
    text = str(text)
    return text if len(text) <= NAME_MAX else text[:NAME_MAX - 1] + "…"


def _number(value):
    return str(int(round(value))) if abs(value - round(value)) < 1e-6 else f"{value:.2f}"


def top_reasons(values, meta, k=REASON_COUNT):
    """Top-k features pushing this customer's risk up, as readable strings.

    contribution_i = coefficient_i * (scaled_x_i - training_mean_i); only positive ones count.
    A category slot is only a reason when the customer has that category: "not Two year" reads
    badly, and the customer's own category is what a retention agent can see and act on.
    """
    candidates = []
    for i, x in enumerate(values):
        contribution = meta["coefficients"][i] * (x - meta["means"][i])
        if contribution <= 0:
            continue
        slot = meta["slots"][i]
        feature = _short(slot["feature"])
        if slot["kind"] == "category":
            if x <= 0:
                continue
            text = f"{feature} = {_short(slot['label'])}"
        elif slot["kind"] == "boolean":
            text = f"{feature} = {'Yes' if x * meta['std'][i] > 0.5 else 'No'}"
        else:
            level = "high" if x > meta["means"][i] else "low"
            text = f"{feature} = {_number(x * meta['std'][i])} ({level})"
        candidates.append((contribution, text))
    candidates.sort(key=lambda c: -c[0])
    texts = [text for _, text in candidates[:k]]
    return texts + [None] * (k - len(texts))


def add_reasons(df, meta, scaled_col="scaled", k=REASON_COUNT):
    """Adds reason_1..reason_k with a Python UDF over the scaled vector (no collect)."""
    udf = F.udf(lambda v: top_reasons(v.toArray(), meta, k), ArrayType(StringType()))
    reasons = udf(F.col(scaled_col))
    return df.select("*", *[reasons[i].alias(f"reason_{i + 1}") for i in range(k)])
