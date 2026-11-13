"""Mode 2: train, check for leakage, score and explain a churn model for any reviewed dataset.

Same model family as Mode 1 (Spark MLlib logistic regression on scaled one-hot + numeric features),
so every score has per-customer reasons. Everything learned from data (medians, category lists,
coefficients) comes from the 80% training split only; all reported metrics are on the 20% holdout.
"""
import csv
import math

from pyspark.ml import Pipeline
from pyspark.ml.classification import LogisticRegression
from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.ml.feature import Imputer, OneHotEncoder, StandardScaler, StringIndexer, VectorAssembler
from pyspark.ml.stat import Summarizer
from pyspark.sql import functions as F

from src.generic.checks import MIN_MINORITY
from src.generic.profile import number as _num, read_canonical
from src.model.features import churn_probability
from src.model.reasons import add_reasons
from src.model.score import risk_band
from src.model.train import capture_stats

SEED = 42
TRAIN_FRACTION = 0.8
TOP_CATEGORIES = 20
LEAK_AUC = 0.99        # one column alone separates churners almost perfectly -> leakage, removed
WARN_AUC = 0.90        # one column alone is very strong -> kept, but the user is asked to check it
                       # (IBM's "Churn Score", a model output, scores 0.948 on its own)
SUSPICIOUS_AUC = 0.99  # the whole model is "too good to be true" -> flagged, never sold as a good model
MIN_SEGMENT_SHARE = 0.01
DISCRETE_MAX = 12      # whole-number columns with at most this many values are shown value by value
CAPTURE_PCTS = (10, 20, 30)
MISSING = "(missing)"
OTHER = "(other)"


class TrainingError(ValueError):
    """Training cannot go ahead; the message is shown to the user."""




def build_frame(df, profile, review):
    """Canonical strings -> row_id, label, n_* (numbers), m_* (missing flags), t_* (text)."""
    cols = {c["key"]: c for c in profile["columns"]}
    feats = review["features"]
    num_keys = [k for k in feats if cols[k]["kind"] == "number"]
    cat_keys = [k for k in feats if cols[k]["kind"] != "number"]
    flag_keys = [k for k in num_keys if cols[k]["blank"] > 0 or cols[k].get("unparsed")]
    id_expr = F.trim(F.col(review["id"])) if review.get("id") else F.col("row").cast("string")
    sel = [id_expr.alias("row_id"),
           (F.trim(F.col(review["target"])) == F.lit(review["positive"])).cast("double").alias("label")]
    sel += [_num(k).alias(f"n_{k}") for k in num_keys]
    sel += [_num(k).isNull().cast("double").alias(f"m_{k}") for k in flag_keys]
    sel += [F.coalesce(F.when(F.trim(F.col(k)) != "", F.trim(F.col(k))), F.lit(MISSING)).alias(f"t_{k}")
            for k in cat_keys]
    return df.select(*sel), num_keys, cat_keys, flag_keys


def stratified_split(frame, seed=SEED):
    train = frame.sampleBy("label", {0.0: TRAIN_FRACTION, 1.0: TRAIN_FRACTION}, seed=seed)
    test = frame.join(train.select("row_id"), "row_id", "left_anti")
    return train, test


def category_caps(train, cat_keys, n=TOP_CATEGORIES):
    """Per text column, its n most frequent values in the TRAINING split; the rest become (other)."""
    caps = {}
    for k in cat_keys:
        rows = train.groupBy(f"t_{k}").count().orderBy(F.desc("count"), f"t_{k}").limit(n).collect()
        caps[k] = [r[0] for r in rows]
    return caps


def apply_caps(frame, caps):
    for k, keep in caps.items():
        c = f"t_{k}"
        frame = frame.withColumn(c, F.when(F.col(c).isin(keep), F.col(c)).otherwise(F.lit(OTHER)))
    return frame


def build_pipeline(num_keys, cat_keys, flag_keys):
    stages, vector = [], []
    if cat_keys:
        stages += [StringIndexer(inputCols=[f"t_{k}" for k in cat_keys], outputCols=[f"x_{k}" for k in cat_keys],
                                 handleInvalid="keep"),
                   OneHotEncoder(inputCols=[f"x_{k}" for k in cat_keys], outputCols=[f"o_{k}" for k in cat_keys],
                                 dropLast=True, handleInvalid="keep")]
        vector += [f"o_{k}" for k in cat_keys]
    if num_keys:
        stages.append(Imputer(strategy="median", inputCols=[f"n_{k}" for k in num_keys],
                              outputCols=[f"i_{k}" for k in num_keys]))
        vector += [f"i_{k}" for k in num_keys]
    vector += [f"m_{k}" for k in flag_keys]
    stages += [VectorAssembler(inputCols=vector, outputCol="features"),
               StandardScaler(inputCol="features", outputCol="scaled", withStd=True, withMean=False),
               LogisticRegression(featuresCol="scaled", labelCol="label", regParam=0.01, maxIter=100)]
    return Pipeline(stages=stages)


MISSING_SUFFIX = " is missing"


def readable_reasons(df):
    """'Tenure is missing = Yes' -> 'Tenure is missing'; '= No' -> 'Tenure is filled in'."""
    for i in (1, 2, 3):
        c = F.col(f"reason_{i}")
        c = F.regexp_replace(c, f"{MISSING_SUFFIX} = Yes$", MISSING_SUFFIX)
        c = F.regexp_replace(c, f"{MISSING_SUFFIX} = No$", " is filled in")
        df = df.withColumn(f"reason_{i}", c)
    return df


def model_meta(model, train, names, num_keys, cat_keys, flag_keys, yes_no=frozenset()):
    """Slot descriptors in assembler order, as Mode 1's reason code expects them.
    yes_no: numeric 0/1 columns, explained as "Complain = Yes" rather than "Complain = 1 (high)"."""
    scaler, lr = model.stages[-2], model.stages[-1]
    slots = []
    if cat_keys:
        for k, labels in zip(cat_keys, model.stages[0].labelsArray):
            slots += [{"feature": names[k], "kind": "category", "label": lab} for lab in [*labels, "<unseen>"]]
    slots += [{"feature": names[k], "kind": "boolean" if k in yes_no else "numeric"} for k in num_keys]
    slots += [{"feature": f"{names[k]}{MISSING_SUFFIX}", "kind": "boolean"} for k in flag_keys]
    size = len(lr.coefficients)
    if len(slots) != size:
        raise ValueError(f"Feature slots ({len(slots)}) do not match the model's vector size ({size})")
    means = (model.transform(train).select(Summarizer.metrics("mean").summary(F.col("scaled")).alias("s"))
             .first()["s"]["mean"].toArray().tolist())
    return {"slots": slots, "coefficients": lr.coefficients.toArray().tolist(), "intercept": lr.intercept,
            "means": means, "std": scaler.std.toArray().tolist()}


def _auc(df, score_col):
    ev = BinaryClassificationEvaluator(labelCol="label", rawPredictionCol=score_col, metricName="areaUnderROC")
    a = ev.evaluate(df.select("label", F.col(score_col).cast("double").alias(score_col)))
    return max(a, 1 - a)  # direction does not matter for "does this column alone reveal the answer"


def single_column_auc(train, test, num_keys, cat_keys):
    """AUC of each column on its own, measured on the holdout (numbers raw, text as its training churn rate)."""
    out = {}
    for k in num_keys:
        med = train.agg(F.percentile_approx(f"n_{k}", 0.5)).first()[0]
        scored = test.select("label", F.coalesce(F.col(f"n_{k}"), F.lit(med if med is not None else 0.0)).alias("s"))
        out[k] = round(_auc(scored, "s"), 4)
    for k in cat_keys:
        rates = train.groupBy(f"t_{k}").agg(F.avg("label").alias("r"))
        base = train.agg(F.avg("label")).first()[0]
        scored = (test.join(rates, f"t_{k}", "left")
                  .select("label", F.coalesce("r", F.lit(base)).alias("s")))
        out[k] = round(_auc(scored, "s"), 4)
    return out


def _ranked_labels(scored):
    rows = scored.select("label", "row_id", churn_probability().alias("p")).orderBy(F.desc("p"), "row_id").collect()
    return [int(r["label"]) for r in rows]


def segment_insights(frame, profile, names, num_keys, cat_keys):
    """Churn rate per category / numeric bin for every feature, biggest spread first (all labelled rows)."""
    cols = {c["key"]: c for c in profile["columns"]}
    total = frame.count()
    exprs, bin_labels, discrete = [], {}, set()
    for k in cat_keys:
        exprs.append(F.struct(F.lit(k).alias("k"), F.col(f"t_{k}").alias("seg")))
    for k in num_keys:
        hist = cols[k].get("histogram") or []
        if cols[k].get("integer") and cols[k]["distinct"] <= DISCRETE_MAX:
            # a few whole-number values (1-4 products, 0/1 flags): one group per value, not ranges
            seg = F.when(F.col(f"n_{k}").isNull(), F.lit(MISSING)).otherwise(F.col(f"n_{k}").cast("long").cast("string"))
            exprs.append(F.struct(F.lit(k).alias("k"), seg.alias("seg")))
            discrete.add(k)
            continue
        if not hist:
            continue
        lo, hi = hist[0]["from"], hist[-1]["to"]
        width = (hi - lo) / len(hist) if hi > lo else 1.0
        b = F.when(F.col(f"n_{k}").isNull(), F.lit(MISSING)).otherwise(
            F.least(F.floor((F.col(f"n_{k}") - F.lit(lo)) / F.lit(width)), F.lit(len(hist) - 1)).cast("string"))
        exprs.append(F.struct(F.lit(k).alias("k"), b.alias("seg")))
        bin_labels[k] = {str(i): _bin_label(h, i == len(hist) - 1, cols[k].get("integer")) for i, h in enumerate(hist)}
    if not exprs:
        return []
    counted = (frame.select("label", F.explode(F.array(*exprs)).alias("p"))
               .groupBy("p.k", "p.seg").agg(F.count(F.lit(1)).alias("n"), F.sum("label").alias("churned")).collect())
    by_key = {}
    for r in counted:
        label = bin_labels.get(r["k"], {}).get(r["seg"], r["seg"])
        numeric = r["k"] in bin_labels or r["k"] in discrete
        order = int(r["seg"]) if numeric and r["seg"] != MISSING else 10 ** 9
        by_key.setdefault(r["k"], []).append({"segment": label, "customers": r["n"], "churned": int(r["churned"]),
                                              "churn_rate_pct": round(100.0 * r["churned"] / r["n"], 2),
                                              "_order": order})
    out = []
    for k, segs in by_key.items():
        numeric = k in bin_labels or k in discrete
        segs.sort(key=lambda s: (s["_order"], -s["customers"]) if numeric else (-s["customers"], s["segment"]))
        for s in segs:
            s.pop("_order")
        big = [s["churn_rate_pct"] for s in segs if s["customers"] >= MIN_SEGMENT_SHARE * total]
        spread = round(max(big) - min(big), 2) if len(big) >= 2 else 0.0
        out.append({"key": k, "name": names[k], "kind": "number" if numeric else "text",
                    "spread_pct_points": spread, "segments": segs})
    out.sort(key=lambda f: -f["spread_pct_points"])
    return out


def _fmt(x):
    """Compact number for a label: 250898 -> 250.9k, 1.5e6 -> 1.5M, 12.345 -> 12.35."""
    ax = abs(x)
    if ax >= 1e6:
        return f"{x / 1e6:.1f}M"
    if ax >= 1e4:
        return f"{x / 1e3:.1f}k"
    return f"{x:,.0f}" if ax >= 100 or float(x).is_integer() else f"{x:,.2f}"


def _bin_label(h, last, integer):
    """'18 to 25' for whole-number columns (bins [from, to), the last one closed), else compact ranges."""
    if not integer:
        return f"{_fmt(h['from'])} to {_fmt(h['to'])}"
    lo = math.ceil(h["from"])
    hi = math.floor(h["to"]) if last else math.ceil(h["to"]) - 1
    return _fmt(lo) if hi <= lo else f"{_fmt(lo)} to {_fmt(hi)}"


def source_counts(canonical_csv, review):
    """Independent of Spark: rows, positives and distinct IDs straight from the canonical file."""
    rows = positives = 0
    ids = set()
    with open(canonical_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows += 1
            positives += r[review["target"]].strip() == review["positive"]
            ids.add(r[review["id"]].strip() if review.get("id") else r["row"])
    return {"rows": rows, "positives": positives, "distinct_ids": len(ids)}


def _check(check_id, description, expected, actual):
    return {"check_id": check_id, "description": description, "expected": str(expected), "actual": str(actual),
            "status": "pass" if expected == actual else "fail"}


def train_dataset(spark, canonical_csv, profile, review):
    """-> (result dict, scores DataFrame). review: id, target, positive, features (keys)."""
    names = {c["key"]: c["name"] for c in profile["columns"]}
    raw = read_canonical(spark, canonical_csv)
    frame, num_keys, cat_keys, flag_keys = build_frame(raw, profile, review)
    frame = frame.cache()
    counts = frame.agg(F.sum("label").alias("pos"), F.count(F.lit(1)).alias("n")).first()
    smaller = min(counts["pos"] or 0, counts["n"] - (counts["pos"] or 0))
    if smaller < MIN_MINORITY:  # same limit as check g05, enforced here too (defence in depth)
        frame.unpersist()
        raise TrainingError(f"Only {int(smaller)} customers in the smaller outcome group; at least {MIN_MINORITY} are needed.")
    train, test = stratified_split(frame)
    train, test = train.cache(), test.cache()
    try:
        caps = category_caps(train, cat_keys)
        train_c, test_c, frame_c = apply_caps(train, caps), apply_caps(test, caps), apply_caps(frame, caps)
        if test_c.select("label").distinct().count() < 2:
            raise TrainingError("The holdout sample has only one outcome; the smaller group is too small to test a model.")

        # Leakage guard: drop every column that alone reveals the outcome, then train without them.
        single = single_column_auc(train_c, test_c, num_keys, cat_keys)
        leaked = sorted(k for k, a in single.items() if a >= LEAK_AUC)
        if leaked:
            num_keys = [k for k in num_keys if k not in leaked]
            cat_keys = [k for k in cat_keys if k not in leaked]
            flag_keys = [k for k in flag_keys if k not in leaked]
        if not (num_keys or cat_keys):
            raise TrainingError("Every feature column reveals the outcome on its own; nothing honest is left to learn from.")

        model = build_pipeline(num_keys, cat_keys, flag_keys).fit(train_c)
        scored_test, scored_train = model.transform(test_c), model.transform(train_c)
        auc_test = round(_auc(scored_test.withColumn("p", churn_probability()), "p"), 4)
        auc_train = round(_auc(scored_train.withColumn("p", churn_probability()), "p"), 4)
        pr = BinaryClassificationEvaluator(labelCol="label", rawPredictionCol="rawPrediction",
                                           metricName="areaUnderPR").evaluate(scored_test)
        labels = _ranked_labels(scored_test)
        cols = {c["key"]: c for c in profile["columns"]}
        yes_no = {k for k in num_keys if cols[k]["distinct"] == 2 and cols[k].get("integer")
                  and cols[k].get("min") == 0 and cols[k].get("max") == 1}
        meta = model_meta(model, train_c, names, num_keys, cat_keys, flag_keys, yes_no)
        ranked = sorted(({"slot": s, "coefficient": round(c, 4)} for s, c in zip(meta["slots"], meta["coefficients"])
                         if s.get("label") != "<unseen>"),
                        key=lambda d: -abs(d["coefficient"]))
        seen, drivers = set(), []
        for d in ranked:  # one entry per feature: "Dependents = No" and "= Yes" say the same thing
            if d["slot"]["feature"] not in seen:
                seen.add(d["slot"]["feature"])
                drivers.append(d)
        drivers = drivers[:12]

        scores = readable_reasons(add_reasons(model.transform(frame_c), meta))
        scores = scores.withColumn("risk_score", F.round(churn_probability(), 4))
        train_ids = train.select("row_id", F.lit(True).alias("in_training_set"))
        scores = (scores.join(train_ids, "row_id", "left")
                  .select("row_id", F.col("risk_score").cast("decimal(5,4)").alias("risk_score"),
                          risk_band(F.col("risk_score")).alias("risk_band"), "reason_1", "reason_2", "reason_3",
                          (F.col("label") == 1.0).alias("churned"),
                          F.coalesce("in_training_set", F.lit(False)).alias("in_training_set"))).cache()

        insights = segment_insights(frame_c, profile, names, num_keys, cat_keys)
        n_train, n_test, n_frame = train.count(), test.count(), frame.count()
        src = source_counts(canonical_csv, review)
        positives = int(frame.agg(F.sum("label")).first()[0] or 0)
        recon = [
            _check("gr01", "Source rows = rows read by Spark", src["rows"], n_frame),
            _check("gr02", "Source rows = customers scored", src["rows"], scores.count()),
            _check("gr03", "Every customer scored exactly once (distinct IDs = rows)", src["rows"],
                   scores.select("row_id").distinct().count()),
            _check("gr04", "Source churners = churners in Spark", src["positives"], positives),
            _check("gr05", "Training + holdout rows = all rows", n_frame, n_train + n_test),
            _check("gr06", "Holdout and training share no customer", 0,
                   train.join(test, "row_id", "inner").count()),
        ]
        for f in insights:
            recon.append(_check(f"gr07.{f['key']}", f"Insight {f['name']}: customers add up to all rows",
                                n_frame, sum(s["customers"] for s in f["segments"])))

        base = positives / n_frame
        capture = {}
        for p in CAPTURE_PCTS:
            # When churn is common, even a perfect ranking cannot reach every churner in the top p%.
            capture[f"top_{p}"] = {**capture_stats(labels, p),
                                   "max_possible_pct": round(min(100.0, 100.0 * p / 100 / base), 2) if base else 0.0}
        result = {
            "rows": {"total": n_frame, "train": n_train, "test": n_test},
            "base_churn_rate_pct": round(100.0 * base, 2),
            "features": {"numeric": [names[k] for k in num_keys], "text": [names[k] for k in cat_keys],
                         "missing_flags": [names[k] for k in flag_keys]},
            "metrics": {"auc_roc_test": auc_test, "auc_roc_train": auc_train, "auc_pr_test": round(pr, 4),
                        "capture": capture},
            "suspicious": auc_test >= SUSPICIOUS_AUC,
            "leakage": {"removed": [{"key": k, "name": names[k], "single_column_auc": single[k]} for k in leaked],
                        "warned": [{"key": k, "name": names[k], "single_column_auc": a}
                                   for k, a in sorted(single.items(), key=lambda x: -x[1]) if WARN_AUC <= a < LEAK_AUC],
                        "single_column_auc": {names[k]: a for k, a in sorted(single.items(), key=lambda x: -x[1])}},
            "drivers": [{"feature": d["slot"]["feature"], "value": d["slot"].get("label"),
                         "coefficient": d["coefficient"], "direction": "raises risk" if d["coefficient"] > 0 else "lowers risk"}
                        for d in drivers],
            "insights": insights,
            "reconciliation": recon,
            "reconciled": all(r["status"] == "pass" for r in recon),
            "category_caps": {names[k]: v for k, v in caps.items()},
        }
        return result, scores
    finally:
        train.unpersist()
        test.unpersist()
        frame.unpersist()
