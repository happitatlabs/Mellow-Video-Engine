SELECT
    o.ORDER_NO,
    o.CUSTOMER_NAME,
    o.STATUS_CD,
    c.CODE_NAME AS STATUS_NAME,
    o.URGENT_YN,
    d.DEPT_NAME,
    u.USER_NAME AS HANDLER_NAME,
    o.ORDER_DATE
FROM TB_ORDER o
LEFT JOIN TB_CODE c
    ON c.GROUP_CD = 'ORDER_STATUS'
   AND c.CODE_CD = o.STATUS_CD
LEFT JOIN TB_DEPT d
    ON d.DEPT_CD = o.DEPT_CD
LEFT JOIN TB_USER u
    ON u.USER_ID = o.HANDLER_ID
WHERE 1 = 1
  AND (:orderNo IS NULL OR o.ORDER_NO = :orderNo)
  AND (:customerName IS NULL OR o.CUSTOMER_NAME LIKE CONCAT('%', :customerName, '%'))
  AND (:fromDate IS NULL OR o.ORDER_DATE >= :fromDate)
  AND (:toDate IS NULL OR o.ORDER_DATE <= :toDate)
  AND (:urgentYn IS NULL OR o.URGENT_YN = :urgentYn)
  AND (:deptFilter IS NULL OR o.DEPT_CD = :deptFilter)
  AND (
      :defaultStatusScope IS NULL
      OR o.STATUS_CD IN ('REQ', 'ING')
  )
  AND (
      :rejectLimited IS NULL
      OR o.HANDLER_ID = :loginId
  )
  AND (
      :includeClosed = 'Y'
      OR o.STATUS_CD <> 'CMP'
  )
  AND (:status IS NULL OR o.STATUS_CD = :status)
ORDER BY
  CASE WHEN :sortPriority = 'URGENT_FIRST' AND o.URGENT_YN = 'Y' THEN 0 ELSE 1 END,
  o.ORDER_DATE DESC
LIMIT :limit OFFSET :offset;
