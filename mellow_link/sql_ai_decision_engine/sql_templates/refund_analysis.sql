SELECT
  refund_rate,
  inquiry_growth
FROM customer_service_metrics
WHERE date BETWEEN :start_date AND :end_date
  AND segment = :segment
ORDER BY date DESC
LIMIT 1;
