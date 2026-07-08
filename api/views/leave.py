"""
api/views/leave.py — Leave & leave type views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from api.models import Employee, AuditLog, LeaveType, Leave
from api.serializers import LeaveTypeSerializer, LeaveSerializer


class LeaveTypeViewSet(viewsets.ModelViewSet):
    queryset = LeaveType.objects.all().order_by('-id')
    serializer_class = LeaveTypeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and user.organization:
            org_qs = qs.filter(organization=user.organization)
            if not org_qs.exists():
                # Clone the global leave types for this organization
                global_types = qs.filter(organization__isnull=True)
                cloned_map = {}
                for gt in global_types:
                    new_lt = LeaveType.objects.create(
                        name=gt.name,
                        description=gt.description,
                        limitPeriod=gt.limitPeriod,
                        maxLimit=gt.maxLimit,
                        restrictedDates=gt.restrictedDates,
                        carryForward=gt.carryForward,
                        maxCarryForward=gt.maxCarryForward,
                        status=gt.status,
                        minAdvanceDays=gt.minAdvanceDays,
                        organization=user.organization
                    )
                    cloned_map[gt.id] = new_lt

                # Update existing Leaves of this organization to point to the cloned LeaveTypes
                leaves_to_update = Leave.objects.filter(employee__organization=user.organization)
                for leave in leaves_to_update:
                    if leave.leaveType_id in cloned_map:
                        leave.leaveType = cloned_map[leave.leaveType_id]
                        leave.save()

                org_qs = qs.filter(organization=user.organization)
            return org_qs.order_by('-id')
        return qs

    def perform_create(self, serializer):
        if self.request.user.is_authenticated:
            serializer.save(organization=self.request.user.organization)
        else:
            serializer.save()


class LeaveViewSet(viewsets.ModelViewSet):
    queryset = Leave.objects.all().order_by('-id')
    serializer_class = LeaveSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(employee__organization=self.request.user.organization)
        employee_id = self.request.query_params.get('employee_id')
        leave_status = self.request.query_params.get('status')
        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        if leave_status:
            qs = qs.filter(status=leave_status)
        return qs

    def perform_create(self, serializer):
        leave = serializer.save()
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Leave Applied",
            details=f"Applied for {leave.leaveTypeName} leave from {leave.startDate} to {leave.endDate} ({leave.duration} days)."
        )

        # Leave notification email code path has been removed per instructions.
        pass

    @action(detail=True, methods=['patch'], url_path='status')
    def update_status(self, request, pk=None):
        leave = self.get_object()
        new_status = request.data.get('status')
        if new_status not in ['Approved', 'Rejected', 'Pending']:
            return Response({'error': 'Invalid status value'}, status=status.HTTP_400_BAD_REQUEST)
        leave.status = new_status
        leave.save()

        # Log status update
        user = self.request.user
        actor_name = f"{user.first_name} {user.last_name}".strip() or user.email
        AuditLog.objects.create(
            employee=user,
            employeeName=actor_name,
            action="Leave Status Updated",
            details=f"Updated leave request status for {leave.employeeName} to '{new_status}'."
        )

        # Send leave status update email to employee
        if leave.employee and leave.employee.email:
            try:
                from api.tasks import queue_and_send_email
                subject = f"Leave Request {new_status}: {leave.leaveTypeName}"
                body = (
                    f"Hi {leave.employee.first_name or 'there'},\n\n"
                    f"Your leave request for {leave.leaveTypeName} has been {new_status.lower()}.\n"
                    f"Details:\n"
                    f"Duration: {leave.startDate} to {leave.endDate} ({leave.duration} days)\n"
                    f"Reason: {leave.reason or 'No reason provided'}\n\n"
                    f"CubeLogs Portal"
                )
                queue_and_send_email(leave.employee.email, subject, body)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to send leave status email to employee: {e}")

        serializer = self.get_serializer(leave)
        return Response(serializer.data, status=status.HTTP_200_OK)
