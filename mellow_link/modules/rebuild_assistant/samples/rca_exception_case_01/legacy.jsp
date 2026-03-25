<%@ page contentType="text/html; charset=UTF-8" %>
<%@ page import="java.util.*, java.text.*" %>
<%@ page import="com.rca.order.OrderSearchService" %>
<%
    String userRole = (String) session.getAttribute("USER_ROLE");
    String userDept = (String) session.getAttribute("USER_DEPT");
    String loginId = (String) session.getAttribute("LOGIN_ID");

    String orderNo = request.getParameter("orderNo");
    String customerName = request.getParameter("customerName");
    String status = request.getParameter("status");
    String urgentYn = request.getParameter("urgentYn");
    String fromDate = request.getParameter("fromDate");
    String toDate = request.getParameter("toDate");
    String includeClosed = request.getParameter("includeClosed");
    String pageNo = request.getParameter("pageNo");

    if (pageNo == null || "".equals(pageNo)) {
        pageNo = "1";
    }

    // 예외 규칙 1: 운영부서는 상태값 없이도 조회 가능
    // 예외 규칙 2: 일반 사용자는 반드시 기간 또는 주문번호 중 하나 필요
    // 예외 규칙 3: 긴급건은 관리자/운영부서만 조회 가능
    // 예외 규칙 4: 종료건 포함은 팀장 이상만 허용

    String validationMsg = null;

    if (!"OPS".equals(userDept)) {
        boolean noDate = (fromDate == null || "".equals(fromDate)) && (toDate == null || "".equals(toDate));
        boolean noOrderNo = (orderNo == null || "".equals(orderNo));
        if (noDate && noOrderNo) {
            validationMsg = "일반 사용자는 기간 또는 주문번호 중 하나를 입력해야 합니다.";
        }
    }

    if ("Y".equals(urgentYn) && !"ADMIN".equals(userRole) && !"OPS".equals(userDept)) {
        validationMsg = "긴급건 조회 권한이 없습니다.";
    }

    if ("Y".equals(includeClosed) && !"ADMIN".equals(userRole) && !"MANAGER".equals(userRole)) {
        validationMsg = "종료건 포함 조회는 팀장 이상만 가능합니다.";
    }

    List<Map<String, Object>> resultList = new ArrayList<Map<String, Object>>();
    String statusLabel = "전체";

    if (validationMsg == null) {
        if ("REQ".equals(status)) statusLabel = "요청";
        else if ("ING".equals(status)) statusLabel = "진행중";
        else if ("CMP".equals(status)) statusLabel = "완료";
        else if ("REJ".equals(status)) statusLabel = "반려";

        OrderSearchService service = new OrderSearchService();
        resultList = service.searchOrders(orderNo, customerName, status, urgentYn, fromDate, toDate,
                                          includeClosed, userRole, userDept, loginId, Integer.parseInt(pageNo));
    }
%>
<html>
<head>
    <title>주문 조회</title>
</head>
<body>
<h2>주문 조회</h2>
<% if (validationMsg != null) { %>
    <div style="color:red;"><%= validationMsg %></div>
<% } %>
<form method="get">
    주문번호: <input type="text" name="orderNo" value="<%= orderNo == null ? "" : orderNo %>" />
    고객명: <input type="text" name="customerName" value="<%= customerName == null ? "" : customerName %>" />
    상태:
    <select name="status">
        <option value="">전체</option>
        <option value="REQ">요청</option>
        <option value="ING">진행중</option>
        <option value="CMP">완료</option>
        <option value="REJ">반려</option>
    </select>
    긴급여부: <input type="checkbox" name="urgentYn" value="Y" <%= "Y".equals(urgentYn) ? "checked" : "" %> />
    종료건 포함: <input type="checkbox" name="includeClosed" value="Y" <%= "Y".equals(includeClosed) ? "checked" : "" %> />
    <button type="submit">조회</button>
</form>

<div>현재 상태 라벨: <%= statusLabel %></div>

<table border="1" cellpadding="4" cellspacing="0">
    <tr>
        <th>주문번호</th>
        <th>고객명</th>
        <th>상태</th>
        <th>긴급</th>
        <th>담당부서</th>
        <th>처리자</th>
        <th>주문일자</th>
    </tr>
<%
    for (Map<String, Object> row : resultList) {
%>
    <tr>
        <td><%= row.get("ORDER_NO") %></td>
        <td><%= row.get("CUSTOMER_NAME") %></td>
        <td><%= row.get("STATUS_NAME") %></td>
        <td><%= row.get("URGENT_YN") %></td>
        <td><%= row.get("DEPT_NAME") %></td>
        <td><%= row.get("HANDLER_NAME") %></td>
        <td><%= row.get("ORDER_DATE") %></td>
    </tr>
<%
    }
%>
</table>
</body>
</html>
