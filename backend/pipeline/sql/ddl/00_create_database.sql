-- Run against master. Idempotent.
IF DB_ID('churn_db') IS NULL CREATE DATABASE churn_db;
