/* Realization rate: share of booked revenue on won deals that has actually been collected. */
/* Trusted query (synthetic example). */
WITH closed_deals AS (
  SELECT
    d.deal_id,
    d.account_id,
    d.amount,
    d.closed_at
  FROM deals AS d
  WHERE
    d.closed_at >= CAST('2026-07-01' AS TIMESTAMP)
    AND d.closed_at < CAST('2026-10-01' AS TIMESTAMP)
    AND d.status IN ('closed_won', 'closed_won_renewal')
), deals_in_region AS (
  SELECT
    cd.deal_id,
    cd.amount,
    a.region,
    DATE_TRUNC('MONTH', cd.closed_at) AS month
  FROM closed_deals AS cd
  JOIN accounts AS a
    ON a.account_id = cd.account_id
  WHERE
    a.region IN ('EMEA')
), collected AS (
  SELECT
    i.deal_id,
    SUM(i.paid_amount) AS paid
  FROM invoices AS i
  WHERE
    i.status = 'paid'
  GROUP BY
    i.deal_id
)
SELECT
  dr.month,
  dr.region,
  ROUND(CAST(SUM(COALESCE(c.paid, 0)) AS DOUBLE PRECISION) / SUM(dr.amount), 4) AS realization_rate
FROM deals_in_region AS dr
LEFT JOIN collected AS c
  ON c.deal_id = dr.deal_id
GROUP BY
  dr.month,
  dr.region
ORDER BY
  dr.month,
  dr.region
