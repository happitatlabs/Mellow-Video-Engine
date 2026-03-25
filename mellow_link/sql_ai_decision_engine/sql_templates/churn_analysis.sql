SELECT
  churn_rate,
  refund_rate
FROM customer_churn_metrics
WHERE date BETWEEN :start_date AND :end_date
  AND segment = :segment
ORDER BY date DESC
LIMIT 1;
