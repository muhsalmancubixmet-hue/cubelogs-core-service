"""
api/views/crm.py — CRM Lead views (public + backoffice)
"""
from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import Lead, LeadHistory
from api.serializers import LeadSerializer, LeadHistorySerializer


class IsSuperAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and getattr(request.user, 'isSuperAdmin', False)):
            return False

        # If user is a client admin (belongs to an organization), allow access
        if request.user.organization is not None:
            return True

        # Root admin has access to everything
        if request.user.email == 'salmankcsiju@gmail.com':
            return True

        # Enforce page access permissions for backoffice operators
        user_perms = getattr(request.user, 'permissions', [])
        if not isinstance(user_perms, list):
            user_perms = []

        all_backoffice_perms = ['packages', 'subscribers', 'leads', 'cms', 'faqs', 'testimonials', 'coupons', 'staff', 'audit_logs']
        has_any_backoffice_perm = any(p in user_perms for p in all_backoffice_perms)
        if not has_any_backoffice_perm:
            user_perms = all_backoffice_perms

        path = request.path
        if 'packages' in path:
            return 'packages' in user_perms
        elif 'subscribers' in path:
            return 'subscribers' in user_perms
        elif 'leads' in path:
            return 'leads' in user_perms
        elif 'cms' in path:
            return 'cms' in user_perms
        elif 'lms' in path:
            return 'lms' in user_perms
        elif 'coupons' in path:
            return 'coupons' in user_perms
        elif 'employees' in path:
            return 'staff' in user_perms
        elif 'audit-logs' in path:
            return 'audit_logs' in user_perms

        return True


class LeadViewSet(viewsets.ModelViewSet):
    queryset = Lead.objects.all().order_by('-createdAt')
    serializer_class = LeadSerializer

    def get_permissions(self):
        if self.action == 'create':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save()


class PublicLeadCreateView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LeadSerializer(data=request.data)
        if serializer.is_valid():
            lead = serializer.save()
            LeadHistory.objects.create(
                lead=lead,
                action="Lead generated from public website enquiry."
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BackofficeLeadListView(APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request):
        leads = Lead.objects.all().order_by('-created_at')
        serializer = LeadSerializer(leads, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BackofficeLeadDetailView(APIView):
    permission_classes = [IsSuperAdminUser]

    def get(self, request, pk):
        try:
            lead = Lead.objects.get(pk=pk)
        except Lead.DoesNotExist:
            return Response({'error': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not lead.is_read:
            lead.is_read = True
            lead.read_by = request.user
            lead.save()

            operator_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email
            LeadHistory.objects.create(
                lead=lead,
                modified_by=request.user,
                action=f"Lead details read by {operator_name}."
            )

        lead_data = LeadSerializer(lead).data
        histories = LeadHistory.objects.filter(lead=lead).order_by('timestamp')
        histories_data = LeadHistorySerializer(histories, many=True).data

        return Response({
            'lead': lead_data,
            'histories': histories_data
        }, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        try:
            lead = Lead.objects.get(pk=pk)
        except Lead.DoesNotExist:
            return Response({'error': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)

        old_status = lead.status
        old_staff = lead.assigned_staff

        serializer = LeadSerializer(lead, data=request.data, partial=True)
        if serializer.is_valid():
            updated_lead = serializer.save()

            operator_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.email

            # Check status change
            new_status = updated_lead.status
            if old_status != new_status:
                LeadHistory.objects.create(
                    lead=updated_lead,
                    modified_by=request.user,
                    action=f"Status updated from {old_status} to {new_status} by {operator_name}."
                )

            # Check staff assignment change
            new_staff = updated_lead.assigned_staff
            if old_staff != new_staff:
                if new_staff is None:
                    LeadHistory.objects.create(
                        lead=updated_lead,
                        modified_by=request.user,
                        action=f"Lead unassigned by {operator_name}."
                    )
                else:
                    staff_name = f"{new_staff.first_name} {new_staff.last_name}".strip() or new_staff.email
                    LeadHistory.objects.create(
                        lead=updated_lead,
                        modified_by=request.user,
                        action=f"Lead assigned to {staff_name} by {operator_name}."
                    )

            lead_data = LeadSerializer(updated_lead).data
            histories = LeadHistory.objects.filter(lead=updated_lead).order_by('timestamp')
            histories_data = LeadHistorySerializer(histories, many=True).data

            return Response({
                'lead': lead_data,
                'histories': histories_data
            }, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
