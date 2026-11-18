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
    d.closed_at >= CAST('2026-01-01' AS DATETIME)
    AND d.closed_at < CAST('2027-01-01' AS DATETIME)
    AND d.status IN ('closed_won', 'closed_won_renewal')
), deals_in_region AS (
  SELECT
    cd.deal_id,
    cd.amount,
    a.region,
    DATE_ADD(
      '0000-01-01 00:00:00',
      INTERVAL (TIMESTAMPDIFF(QUARTER, '0000-01-01 00:00:00', cd.closed_at)) QUARTER
    ) AS month
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
  ROUND(SUM(COALESCE(c.paid, 0)) / SUM(dr.amount), 4) AS realization_rate
FROM deals_in_region AS dr
LEFT JOIN collected AS c
  ON c.deal_id = dr.deal_id
GROUP BY
  dr.month,
  dr.region
ORDER BY
  CASE WHEN dr.month IS NULL THEN 1 ELSE 0 END, dr.month,
  CASE WHEN dr.region IS NULL THEN 1 ELSE 0 END, dr.region
