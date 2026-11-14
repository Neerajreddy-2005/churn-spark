"""Automatic data exploration of a canonical file (all columns read as strings, named c0..cN).

Three Spark jobs whatever the column count: one exploded group-by for the exact distinct counts and
top values, one aggregate for the other per-column statistics, one exploded group-by for numeric
histograms. (100 countDistinct in one aggregate took minutes per small file: audit finding #6.)
"""
from pathlib import Path

from pyspark.sql import Window
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


def column_stats(df, keys, distinct):
    """distinct: {key: exact count of different non-blank values}, from value_counts."""
    aggs = [F.count(F.lit(1)).alias("_rows")]
    for c in keys:
        n = _num(c)
        aggs += [
            F.sum(_blank(c).cast("int")).alias(f"{c}__blank"),
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
            "distinct": distinct[c],
        }
        if is_num:
            q = row[f"{c}__q"] or [None] * 3
            s.update(integer=row[f"{c}__integer"] == numeric, unparsed=filled - numeric,
                     min=row[f"{c}__min"], q1=q[0], median=q[1], q3=q[2], max=row[f"{c}__max"],
                     mean=None if row[f"{c}__mean"] is None else round(row[f"{c}__mean"], 4))
        out[c] = s
    return rows, out


def value_counts(df, keys, n=TOP_N):
    """({key: distinct}, {key: [(value, count), ...]}): the exact number of different non-blank values
    and the n most frequent of them, for every column, from one group-by. Only the top n per column
    reach the driver."""
    pairs = F.explode(F.array(*[F.struct(F.lit(c).alias("k"), F.trim(F.col(c)).alias("v")) for c in keys]))
    counted = (df.select(pairs.alias("p")).select("p.k", "p.v")
               .where(F.col("v").isNotNull() & (F.col("v") != ""))
               .groupBy("k", "v").count())
    rank = F.row_number().over(Window.partitionBy("k").orderBy(F.desc("count"), F.asc("v")))
    size = F.count(F.lit(1)).over(Window.partitionBy("k"))
    ranked = counted.select("k", "v", "count", rank.alias("rank"), size.alias("distinct"))
    distinct, top = {c: 0 for c in keys}, {c: [] for c in keys}
    for r in sorted(ranked.where(F.col("rank") <= n).collect(), key=lambda r: r["rank"]):
        distinct[r["k"]] = r["distinct"]
        top[r["k"]].append((r["v"], r["count"]))
    return distinct, top


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
        distinct, tops = value_counts(df, keys)
        rows, stats = column_stats(df, keys, distinct)
        hists = histograms(df, stats)
    finally:
        df.unpersist()
    cols = []
    for c in columns:
        s = stats[c["key"]]
        cols.append({**c, **s, "top": tops[c["key"]], "histogram": hists.get(c["key"])})
    return {"rows": rows, "columns": cols}
