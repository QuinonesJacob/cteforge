/* Paid revenue per month and sales channel for customers in one country (synthetic shop */
/* schema: customers -> orders). Trusted MySQL query used by cteforge's tests (ADR 0028). */
/* MySQL has no DATE_TRUNC: the month is DATE_FORMAT(..., '%Y-%m-01'). */
SELECT
  DATE_FORMAT(o.created_at, '%Y-%m-01') AS period,
  o.channel,
  COUNT(*) AS orders,
  SUM(o.amount) AS revenue
FROM shop.orders AS o
JOIN shop.customers AS c
  ON c.customer_id = o.customer_id
WHERE
  o.created_at >= '2026-02-01'
  AND o.created_at < '2026-03-01'
  AND o.status IN ('paid', 'shipped')
  AND c.country = 'DE'
  AND o.is_test = 0
  AND o.amount >= 10.00
GROUP BY
  DATE_FORMAT(o.created_at, '%Y-%m-01'),
  o.channel
ORDER BY
  period,
  o.channel
