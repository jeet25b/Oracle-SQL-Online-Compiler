import calendar
from datetime import date, timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .class_management import can_manage_academics, managed_classes, ordered_classes, page_context
from .models import AuditEvent, ClassSection, PortalUser, TeacherAssignment, TimetableEntry


def occurs_on(entry, day):
    if day < entry.start_date or (entry.end_date and day > entry.end_date): return False
    if entry.frequency == "daily": return True
    if entry.frequency == "weekly": return day.weekday() in entry.weekdays
    return entry.frequency == "monthly" and day.day in entry.month_days


def recurrence_overlaps(left, right):
    start = max(left.start_date, right.start_date)
    # Gregorian dates and weekdays repeat every 400 years. This also bounds open-ended schedules.
    cycle_end = start + timedelta(days=min(146097, (date.max - start).days))
    end = min(left.end_date or cycle_end, right.end_date or cycle_end, cycle_end)
    if end < start: return False
    if left.frequency != "monthly" and right.frequency != "monthly":
        weekdays = (set(range(7)) if left.frequency == "daily" else set(left.weekdays)) & (set(range(7)) if right.frequency == "daily" else set(right.weekdays))
        return any(start + timedelta(days=offset) <= end and (start.weekday() + offset) % 7 in weekdays for offset in range(min(7, (end - start).days + 1)))
    monthly = left if left.frequency == "monthly" else right
    days = set(monthly.month_days)
    if left.frequency == right.frequency == "monthly": days &= set(right.month_days)
    if not days: return False
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        maximum = calendar.monthrange(year, month)[1]
        for number in days:
            if number <= maximum:
                candidate = date(year, month, number)
                if start <= candidate <= end and occurs_on(left, candidate) and occurs_on(right, candidate): return True
        if year == 9999 and month == 12: break
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return False


def validate_conflicts(entry):
    allocation = entry.allocation
    resources = Q(allocation__class_section_id=allocation.class_section_id) | Q(allocation__teacher_id=allocation.teacher_id)
    others = TimetableEntry.objects.filter(resources, is_active=True, start_time__lt=entry.end_time, end_time__gt=entry.start_time).exclude(pk=entry.pk)
    for other in others:
        if recurrence_overlaps(entry, other):
            raise ValidationError("This schedule overlaps another booking for the class or teacher. Choose a different time or recurrence.")


class AllocationChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        subject = obj.class_subject.subject.name if obj.class_subject_id else obj.subject
        subject = subject or "Class access"
        return f"{subject} · {obj.teacher}"


class TimetableForm(forms.ModelForm):
    allocation = AllocationChoice(queryset=TeacherAssignment.objects.none(), label="Subject / session / teacher allocation")
    weekdays = forms.MultipleChoiceField(choices=tuple(enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))), required=False, widget=forms.CheckboxSelectMultiple, label="Weekdays (weekly only)")
    month_days = forms.MultipleChoiceField(choices=[(day, str(day)) for day in range(1, 32)], required=False, widget=forms.SelectMultiple(attrs={"size": 5}), label="Dates of month (monthly only)")

    class Meta:
        model = TimetableEntry
        fields = ("allocation", "frequency", "start_date", "end_date", "start_time", "end_time", "weekdays", "month_days", "room", "is_active")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"}), "start_time": forms.TimeInput(attrs={"type": "time"}), "end_time": forms.TimeInput(attrs={"type": "time"})}
        labels = {"start_date": "Repeat from", "end_date": "Repeat until (optional)", "room": "Room / location (optional)", "is_active": "Enable schedule"}

    def __init__(self, *args, section, **kwargs):
        super().__init__(*args, **kwargs)
        allocations = TeacherAssignment.objects.filter(class_section=section).filter(
            Q(class_subject__isnull=False) | ~Q(subject="")
        ).select_related("teacher", "class_subject__subject", "class_section__course")
        self.fields["allocation"].queryset = allocations
        self.has_allocations = allocations.exists()
        if not self.has_allocations:
            self.fields["allocation"].empty_label = "No subject / session / teacher allocations"
            self.fields["allocation"].help_text = "Add a teacher allocation for this class before creating a schedule."
            self.fields["allocation"].disabled = True

    def clean_weekdays(self): return sorted(int(day) for day in self.cleaned_data["weekdays"])

    def clean_month_days(self): return sorted(int(day) for day in self.cleaned_data["month_days"])


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def timetable(request):
    from .portal_views import _render_dashboard
    if not can_manage_academics(request.user, "can_manage_timetable"):
        return HttpResponseForbidden("Timetable management requires administrator authorization.")
    classes = ordered_classes(managed_classes(request.user))
    selected = request.GET.get("class", "")
    section = classes.filter(pk=selected if selected.isdigit() else None).first()
    if selected and not section: return HttpResponseForbidden("This class is outside your management scope.")
    entry_id = request.GET.get("edit", "")
    entries = TimetableEntry.objects.filter(allocation__class_section__in=classes).select_related("allocation__teacher", "allocation__class_subject__subject", "allocation__class_section__course")
    if section: entries = entries.filter(allocation__class_section=section)
    entry = entries.filter(pk=entry_id if entry_id.isdigit() else None).first()
    if entry_id and not entry: return HttpResponseForbidden("This timetable entry is outside your management scope.")
    if request.method == "POST" and request.POST.get("action") == "remove":
        target = request.POST.get("entry_id", "")
        with transaction.atomic():
            record = entries.filter(pk=target if target.isdigit() else None).first()
            if not record: return HttpResponseForbidden("This timetable entry is outside your management scope.")
            target_class = record.allocation.class_section_id
            record.delete()
            AuditEvent.objects.create(actor=request.user, action="timetable_remove", target=f"entry:{target}:class:{target_class}")
        return redirect(f"/portal/timetable/?class={target_class}")
    if request.method == "POST" and not section: return HttpResponseForbidden("Choose an assigned class first.")
    form = TimetableForm(request.POST if request.method == "POST" else None, instance=entry or TimetableEntry(created_by=request.user), section=section) if section else None
    if form and request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        with transaction.atomic():
            # Serialize bookings sharing a class, teacher or lab before the final conflict check.
            ClassSection.objects.select_for_update().get(pk=section.pk)
            PortalUser.objects.select_for_update().get(pk=item.allocation.teacher_id)
            try:
                item.full_clean()
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
            else:
                item.save()
                AuditEvent.objects.create(actor=request.user, action="timetable_save", target=f"entry:{item.pk}:class:{section.pk}")
                return redirect(f"/portal/timetable/?class={section.pk}")
    try: day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError: day = timezone.localdate()
    entries = list(entries)
    agenda = [item for item in entries if item.is_active and occurs_on(item, day)]
    return _render_dashboard("timetable.html", page_context(request, is_timetable_page=True, classes=classes, section=section, schedule_form=form, has_allocations=bool(form and form.has_allocations), editing=entry, entries=entries, agenda=agenda, agenda_date=day), request)
