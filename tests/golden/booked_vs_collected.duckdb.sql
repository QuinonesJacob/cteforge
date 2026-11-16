/* Booked vs collected revenue per region for one period (synthetic example). */
/* The period appears twice: deals are filtered by close date (written as US-style text */
/* parsed with strptime) and invoices by invoice date (a typed TIMESTAMP literal). */
WITH booked AS (
  SELECT
    a.region,
    SUM(d.amount) AS booked
  FROM deals AS d
  JOIN accounts AS a
    ON a.account_id = d.account_id
  WHERE
    d.closed_at >= STRPTIME('01-15-2026', '%m-%d-%Y')
    AND d.closed_at < STRPTIME('03-01-2026', '%m-%d-%Y')
    AND d.status IN ('closed_won', 'closed_won_renewal')
  GROUP BY
    a.region
), collected AS (
  SELECT
    a.region,
    SUM(i.paid_amount) AS collected
  FROM invoices AS i
  JOIN deals AS d
    ON d.deal_id = i.deal_id
  JOIN accounts AS a
    ON a.account_id = d.account_id
  WHERE
    i.invoiced_at >= CAST('2026-01-15' AS TIMESTAMP)
    AND i.invoiced_at < CAST('2026-03-01' AS TIMESTAMP)
    AND i.status = 'paid'
  GROUP BY
    a.region
)
SELECT
  b.region,
  b.booked,
  COALESCE(c.collected, 0) AS collected
FROM booked AS b
LEFT JOIN collected AS c
  ON c.region = b.region
WHERE
  b.booked >= 1000.5 * 1000
ORDER BY
  b.region
