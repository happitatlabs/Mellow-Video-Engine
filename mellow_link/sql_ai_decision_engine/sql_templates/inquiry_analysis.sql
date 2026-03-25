SELECT
  inquiry_growth,
  inquiry_count
FROM inquiry_metrics
WHERE date BETWEEN :start_date AND :end_date
  AND segment = :segment
ORDER BY date DESC
LIMIT 1;
