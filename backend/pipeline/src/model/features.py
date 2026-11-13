from pyspark.ml import Pipeline
from pyspark.ml.classification import LogisticRegression
from pyspark.ml.feature import OneHotEncoder, StandardScaler, StringIndexer, VectorAssembler
from pyspark.sql import functions as F

from src.config.settings import METADATA_COLUMNS

SEED = 42
LABEL = "churn_flag"

CATEGORICAL = [
    "gender", "contract", "internet_service", "payment_method", "multiple_lines",
    "online_security", "online_backup", "device_protection", "tech_support",
    "streaming_tv", "streaming_movies",
]
BOOLEAN = ["senior_citizen", "partner", "dependents", "phone_service", "paperless_billing"]
NUMERIC = ["tenure_months", "monthly_charges"]
FEATURES = CATEGORICAL + BOOLEAN + NUMERIC

# Deliberately left out of the model:
# - total_charges: roughly tenure_months x monthly_charges, so it is collinear with both and
#   would muddle which of them is the reason.
# - tenure_band and addon_service_count: derived from included columns (tenure_months and the
#   add-on columns), so they add no information and split one driver into several.
EXCLUDED = ["total_charges", "tenure_band", "addon_service_count"]
# Never features: the label (and its boolean twin), the key, ingest metadata, and the imputation
# flag (it is true only for tenure-0 rows, a data-handling artefact rather than customer behaviour).
FORBIDDEN = ["churn", "churn_flag", "customer_id", "total_charges_imputed", *METADATA_COLUMNS]


def prepare(df):
    """Silver -> id, label and model inputs; booleans become 0.0/1.0."""
    booleans = [F.col(c).cast("double").alias(c) for c in BOOLEAN]
    others = [c for c in FEATURES if c not in BOOLEAN]
    return df.select("customer_id", LABEL, *others, *booleans)


def stratified_split(df, train_fraction=0.8, seed=SEED):
    """Stratified by churn_flag. Returns (train, test); no customer is in both."""
    train = df.sampleBy(LABEL, {0: train_fraction, 1: train_fraction}, seed=seed)
    test = df.join(train.select("customer_id"), "customer_id", "left_anti")
    return train, test


def build_pipeline(categorical=CATEGORICAL, boolean=BOOLEAN, numeric=NUMERIC):
    indexed = [f"{c}_idx" for c in categorical]
    encoded = [f"{c}_ohe" for c in categorical]
    return Pipeline(stages=[
        StringIndexer(inputCols=categorical, outputCols=indexed, handleInvalid="keep"),
        OneHotEncoder(inputCols=indexed, outputCols=encoded, dropLast=True, handleInvalid="keep"),
        VectorAssembler(inputCols=encoded + boolean + numeric, outputCol="features"),
        StandardScaler(inputCol="features", outputCol="scaled", withStd=True, withMean=False),
        LogisticRegression(featuresCol="scaled", labelCol=LABEL, regParam=0.01,
                           elasticNetParam=0.0, maxIter=100),
    ])


def feature_slots(model, categorical=CATEGORICAL, boolean=BOOLEAN, numeric=NUMERIC):
    """One descriptor per slot of the scaled vector, in assembler order."""
    labels = model.stages[0].labelsArray
    size = len(model.stages[-1].coefficients)
    slots = []
    for column, column_labels in zip(categorical, labels):
        # Each column also gets a slot for labels unseen in training (always 0 here), because
        # StringIndexer and OneHotEncoder both use handleInvalid="keep".
        for label in [*column_labels, "<unseen>"]:
            slots.append({"feature": column, "kind": "category", "label": label})
    slots += [{"feature": c, "kind": "boolean"} for c in boolean]
    slots += [{"feature": c, "kind": "numeric"} for c in numeric]
    # Fail loudly if the slot layout ever stops matching the model's vector.
    if len(slots) != size:
        raise ValueError(f"Feature slots ({len(slots)}) do not match the model's vector size ({size})")
    return slots


def slot_name(slot):
    if slot["kind"] == "category":
        return f"{slot['feature']} = {slot['label']}"
    return slot["feature"]


def churn_probability(probability_col="probability"):
    """P(churn) from the probability vector, via the JVM function (no Python UDF).
    pyspark.ml.functions.vector_to_array needs pandas at import time, which we do not install."""
    from pyspark import SparkContext
    from pyspark.sql.column import Column, _to_java_column

    jvm_function = SparkContext._active_spark_context._jvm.org.apache.spark.ml.functions.vector_to_array
    return Column(jvm_function(_to_java_column(F.col(probability_col)), "float64"))[1]
