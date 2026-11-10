"""Teacher-controlled registration and Oracle connection walkthroughs."""
import json

from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import PortalUser, SetupGuideProgress, TeacherActivityPreference


def _teacher(request):
    from .portal_views import _active_portal_role
    return request.user.is_authenticated and _active_portal_role(request) == PortalUser.Role.TEACHER


def _payload(request):
    try:
        value = json.loads(request.body or b'{}')
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def _response(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(['GET', 'POST'])
def teacher_settings(request):
    if not _teacher(request):
        return _response({'error': 'Only teachers can manage student setup help.'}, 403)
    preference, _ = TeacherActivityPreference.objects.get_or_create(teacher=request.user)
    if request.method == 'POST':
        data = _payload(request)
        if data is None or data.get('action') not in ('enable', 'disable', 'restart'):
            return _response({'error': 'Choose enable, disable, or restart.'}, 400)
        action = data['action']
        with transaction.atomic():
            updates = {'guided_setup_enabled': action != 'disable'}
            if action == 'restart':
                updates['guided_setup_revision'] = F('guided_setup_revision') + 1
            TeacherActivityPreference.objects.filter(pk=preference.pk).update(**updates)
        preference.refresh_from_db()
    return _response({'enabled': preference.guided_setup_enabled,
                      'revision': preference.guided_setup_revision,
                      'registrationPath': f'/portal/?guide={request.user.pk}'})


def _policy(request):
    policies = TeacherActivityPreference.objects.filter(guided_setup_enabled=True, teacher__is_active=True)
    if request.user.is_authenticated:
        from .portal_views import _active_portal_role
        role = _active_portal_role(request)
        if role == PortalUser.Role.TEACHER:
            return policies.filter(teacher=request.user).first()
        if role != PortalUser.Role.STUDENT:
            return None
        # Before enrollment, provide only the generic class-selection guide.
        # Once a class is chosen, the assigned teacher's policy takes over.
        if request.user.class_selection_required:
            return policies.order_by('teacher_id').first()
        # A teacher controls only students in their assigned teaching groups.
        return policies.filter(
            teacher__teaching_assignments__class_section__enrollments__student=request.user,
            teacher__teaching_assignments__class_section__enrollments__active=True,
        ).order_by('teacher_id').distinct().first()
    teacher_id = request.GET.get('teacher')
    if teacher_id:
        try:
            policies = policies.filter(teacher_id=int(teacher_id))
        except (ValueError, TypeError):
            return None
    return policies.order_by('teacher_id').first()


@require_http_methods(['GET', 'POST'])
def guide_state(request):
    policy = _policy(request)
    if not policy:
        return _response({'enabled': False}, 403 if request.method == 'POST' else 200)
    user_id = request.user.pk if request.user.is_authenticated else None
    completed = False
    if user_id:
        completed = SetupGuideProgress.objects.filter(
            student=request.user, teacher_id=policy.teacher_id,
            completed_revision=policy.guided_setup_revision,
        ).exists()
    if request.method == 'POST':
        if request.user.is_authenticated and request.user.class_selection_required:
            return _response({'error': 'Choose your class before completing Oracle setup.'}, 409)
        if not user_id or _teacher(request):
            return _response({'error': 'Sign in as a student to save guide progress.'}, 403)
        data = _payload(request)
        if data is None or data.get('revision') != policy.guided_setup_revision:
            return _response({'error': 'The teacher updated this guide. Restart it to continue.'}, 409)
        SetupGuideProgress.objects.update_or_create(
            student=request.user, teacher_id=policy.teacher_id,
            defaults={'completed_revision': policy.guided_setup_revision},
        )
        completed = True
    return _response({'enabled': True, 'teacherId': policy.teacher_id,
                      'revision': policy.guided_setup_revision, 'userId': user_id,
                      'completed': completed, 'classSelectionRequired': bool(user_id and request.user.class_selection_required), 'teacher': _teacher(request)})
