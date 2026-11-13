"""Automatic data exploration of a canonical file (all columns read as strings, named c0..cN).

Three Spark jobs whatever the column count: one aggregate for per-column statistics, one exploded
group-by for the top values, one exploded group-by for numeric histograms.
"""
from pathlib import Path

from pyspark.sql import functions as F

NUMBER_SHARE = 0.95   # a column is numeric when this share of its non-blank values parse as numbers
DATE_SHARE = 0.95
TOP_N = 10
BINS = 10
DATE_RE = r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T].*)?$"


def read_canonical(spark, path):
    return (spark.read.option("header", True).option("inferSchema", False)
            .csv(Path(path).resolve().as_posix()))


def number(c):
    """Numeric value of a canonical column; NaN and Infinity count as unparsed (they break stats and JSON)."""
    x = f"try_cast(trim(`{c}`) AS DOUBLE)"
    return F.expr(f"CASE WHEN isnan({x}) OR abs({x}) = double('inf') THEN NULL ELSE {x} END")


_num = number


def _blank(c):
    return F.col(c).isNull() | (F.trim(F.col(c)) == "")


def column_stats(df, keys):
    aggs = [F.count(F.lit(1)).alias("_rows")]
    for c in keys:
        v = F.when(~_blank(c), F.trim(F.col(c)))
        n = _num(c)
        aggs += [
            F.sum(_blank(c).cast("int")).alias(f"{c}__blank"),
            F.countDistinct(v).alias(f"{c}__distinct"),
            F.count(n).alias(f"{c}__numeric"),
            F.sum((n == F.floor(n)).cast("int")).alias(f"{c}__integer"),
            F.sum(F.trim(F.col(c)).rlike(DATE_RE).cast("int")).alias(f"{c}__date"),
            F.min(n).alias(f"{c}__min"), F.max(n).alias(f"{c}__max"), F.avg(n).alias(f"{c}__mean"),
            F.percentile_approx(n, [0.25, 0.5, 0.75], 10000).alias(f"{c}__q"),
        ]
    row = df.agg(*aggs).first().asDict()
    rows = row["_rows"]
    out = {}
    for c in keys:
        filled = rows - row[f"{c}__blank"]
        numeric = row[f"{c}__numeric"]
        is_num = filled > 0 and numeric >= NUMBER_SHARE * filled
        is_date = (not is_num) and filled > 0 and row[f"{c}__date"] >= DATE_SHARE * filled
        s = {
            "kind": "number" if is_num else "date" if is_date else "text" if filled else "empty",
            "filled": filled, "blank": row[f"{c}__blank"],
            "missing_pct": round(100.0 * row[f"{c}__blank"] / rows, 2) if rows else 0.0,
            "distinct": row[f"{c}__distinct"],
        }
        if is_num:
            q = row[f"{c}__q"] or [None] * 3
            s.update(integer=row[f"{c}__integer"] == numeric, unparsed=filled - numeric,
                     min=row[f"{c}__min"], q1=q[0], median=q[1], q3=q[2], max=row[f"{c}__max"],
                     mean=None if row[f"{c}__mean"] is None else round(row[f"{c}__mean"], 4))
        out[c] = s
    return rows, out


def top_values(df, keys, n=TOP_N):
    """{key: [(value, count), ...]} for the n most frequent non-blank values of every column."""
    pairs = F.explode(F.array(*[F.struct(F.lit(c).alias("k"), F.trim(F.col(c)).alias("v")) for c in keys]))
    counted = (df.select(pairs.alias("p")).select("p.k", "p.v")
               .where(F.col("v").isNotNull() & (F.col("v") != ""))
               .groupBy("k", "v").count())
    out = {c: [] for c in keys}
    for r in counted.collect():
        out[r["k"]].append((r["v"], r["count"]))
    return {c: sorted(vs, key=lambda x: (-x[1], x[0]))[:n] for c, vs in out.items()}


def histograms(df, stats, bins=BINS):
    """{key: [{"from", "to", "count"}, ...]} with equal-width bins between min and max."""
    numeric = [c for c, s in stats.items() if s["kind"] == "number" and s["min"] is not None]
    if not numeric:
        return {}
    exprs = []
    for c in numeric:
        lo, hi = stats[c]["min"], stats[c]["max"]
        width = (hi - lo) / bins if hi > lo else 1.0
        # when(): least() skips nulls, so least(null, 9) would put every blank in the last bin.
        b = F.when(_num(c).isNotNull(),
                   F.least(F.floor((_num(c) - F.lit(lo)) / F.lit(width)), F.lit(bins - 1))).cast("int")
        exprs.append(F.struct(F.lit(c).alias("k"), b.alias("b")))
    counted = (df.select(F.explode(F.array(*exprs)).alias("p")).select("p.k", "p.b")
               .where(F.col("b").isNotNull()).groupBy("k", "b").count().collect())
    out = {}
    for c in numeric:
        lo, hi = stats[c]["min"], stats[c]["max"]
        width = (hi - lo) / bins if hi > lo else 1.0
        n_bins = bins if hi > lo else 1
        out[c] = [{"from": lo + i * width, "to": lo + (i + 1) * width if hi > lo else hi, "count": 0}
                  for i in range(n_bins)]
    for r in counted:
        if r["b"] < len(out[r["k"]]):
            out[r["k"]][r["b"]]["count"] += r["count"]
    return out


def profile_table(spark, canonical_csv, columns):
    """columns: display map from reader.write_canonical. Returns the JSON-ready profile."""
    df = read_canonical(spark, canonical_csv).cache()
    try:
        keys = [c["key"] for c in columns]
        rows, stats = column_stats(df, keys)
        tops = top_values(df, keys)
        hists = histograms(df, stats)
    finally:
        df.unpersist()
    cols = []
    for c in columns:
        s = stats[c["key"]]
        cols.append({**c, **s, "top": tops[c["key"]], "histogram": hists.get(c["key"])})
    return {"rows": rows, "columns": cols}
