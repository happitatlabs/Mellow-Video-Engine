package com.rca.order;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class OrderSearchService {

    private final OrderSearchDao dao = new OrderSearchDao();

    public List<Map<String, Object>> searchOrders(
            String orderNo,
            String customerName,
            String status,
            String urgentYn,
            String fromDate,
            String toDate,
            String includeClosed,
            String userRole,
            String userDept,
            String loginId,
            int pageNo
    ) {
        Map<String, Object> param = new HashMap<String, Object>();
        param.put("orderNo", orderNo);
        param.put("customerName", customerName);
        param.put("status", status);
        param.put("urgentYn", urgentYn);
        param.put("fromDate", fromDate);
        param.put("toDate", toDate);
        param.put("includeClosed", includeClosed);
        param.put("pageNo", pageNo);

        // 예외 규칙 5: 운영부서는 부서 제한 없음
        // 예외 규칙 6: 일반 사용자는 본인 부서 데이터만 허용
        // 예외 규칙 7: 일반 사용자가 상태값 미입력이면 진행중/요청만 허용
        // 예외 규칙 8: 반려건은 ADMIN, QA만 전체 조회 가능

        if (!"OPS".equals(userDept) && !"ADMIN".equals(userRole)) {
            param.put("deptFilter", userDept);
        }

        if ((status == null || "".equals(status)) && !"OPS".equals(userDept)) {
            param.put("defaultStatusScope", "ACTIVE_ONLY");
        }

        if ("REJ".equals(status) && !"ADMIN".equals(userRole) && !"QA".equals(userDept)) {
            param.put("rejectLimited", "Y");
            param.put("loginId", loginId);
        }

        if ("Y".equals(urgentYn)) {
            param.put("sortPriority", "URGENT_FIRST");
        }

        if (pageNo < 1) {
            pageNo = 1;
        }
        param.put("offset", (pageNo - 1) * 20);
        param.put("limit", 20);

        return dao.searchOrders(param);
    }
}
