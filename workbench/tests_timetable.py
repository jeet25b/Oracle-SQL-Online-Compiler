from datetime import date, time

from django.test import SimpleTestCase, TestCase

from .models import ClassSection, ClassSubject, Course, PortalUser, Subject, TeacherAssignment, TimetableEntry
from .timetable import occurs_on, recurrence_overlaps


class RecurrenceTests(SimpleTestCase):
    def entry(self, **values):
        return TimetableEntry(start_date=date(2026, 1, 1), start_time=time(9), end_time=time(10), **values)

    def test_daily_and_weekly_and_inclusive_bounds(self):
        daily = self.entry(frequency="daily", end_date=date(2026, 1, 1))
        weekly = self.entry(frequency="weekly", weekdays=[date(2026, 1, 1).weekday()])
        self.assertTrue(recurrence_overlaps(daily, weekly))
        weekly.weekdays = [(date(2026, 1, 1).weekday() + 1) % 7]
        self.assertFalse(recurrence_overlaps(daily, weekly))
        self.assertTrue(occurs_on(daily, date(2026, 1, 1)))
        self.assertFalse(occurs_on(daily, date(2026, 1, 2)))

    def test_monthly_skips_missing_dates_and_supports_leap_day(self):
        monthly = self.entry(frequency="monthly", month_days=[31])
        self.assertTrue(occurs_on(monthly, date(2026, 1, 31)))
        self.assertFalse(occurs_on(monthly, date(2026, 2, 28)))
        monthly.start_date, monthly.end_date = date(2026, 2, 1), date(2026, 2, 28)
        self.assertFalse(recurrence_overlaps(monthly, self.entry(frequency="daily")))
        monthly.start_date, monthly.end_date, monthly.month_days = date(2028, 2, 1), date(2028, 2, 29), [29]
        self.assertTrue(recurrence_overlaps(monthly, self.entry(frequency="daily")))

    def test_monthly_weekly_intersection_and_disjoint_month_dates(self):
        monthly = self.entry(frequency="monthly", month_days=[5], end_date=date(2026, 1, 31))
        weekly = self.entry(frequency="weekly", weekdays=[date(2026, 1, 5).weekday()])
        self.assertTrue(recurrence_overlaps(monthly, weekly))
        weekly.weekdays = [(weekly.weekdays[0] + 1) % 7]
        self.assertFalse(recurrence_overlaps(monthly, weekly))
        self.assertFalse(recurrence_overlaps(monthly, self.entry(frequency="monthly", month_days=[6])))

    def test_nonoverlapping_date_ranges(self):
        first = self.entry(frequency="daily", end_date=date(2026, 1, 5))
        second = self.entry(frequency="daily")
        second.start_date = date(2026, 1, 6)
        self.assertFalse(recurrence_overlaps(first, second))


class TimetableManagementTests(TestCase):
    def setUp(self):
        self.admin = PortalUser.objects.create_user(username="tt-admin", role="ADMIN")
        self.teacher = PortalUser.objects.create_user(username="tt-teacher", role="TEACHER")
        self.other_teacher = PortalUser.objects.create_user(username="tt-other", role="TEACHER")
        course = Course.objects.create(code="TT", name="B.Tech")
        self.section = ClassSection.objects.create(course=course, name="A", branch="CS", semester=1, section="A", group="G1")
        self.other = ClassSection.objects.create(course=course, name="B", branch="CS", semester=1, section="B", group="G1")
        subject = Subject.objects.create(code="TTSQL", name="SQL")
        self.offering = ClassSubject.objects.create(class_section=self.section, subject=subject)
        other_offering = ClassSubject.objects.create(class_section=self.other, subject=subject)
        self.allocation = TeacherAssignment.objects.create(teacher=self.teacher, class_section=self.section, class_subject=self.offering, subject="SQL")
        self.other_allocation = TeacherAssignment.objects.create(teacher=self.other_teacher, class_section=self.other, class_subject=other_offering, subject="SQL")
        self.url = f"/portal/timetable/?class={self.section.pk}"
        self.data = {"allocation": self.allocation.pk, "frequency": "weekly", "weekdays": [0, 2], "start_date": "2026-10-01", "start_time": "09:00", "end_time": "10:00", "is_active": "on"}

    def test_authorized_teacher_can_allocate_only_in_assigned_class(self):
        self.teacher.can_manage_classes = True
        self.teacher.save()
        self.client.force_login(self.teacher)
        response = self.client.get(f"/portal/classes/?class={self.section.pk}")
        self.assertContains(response, "Allocate a teacher")
        self.assertNotContains(response, "Unassign from class")
        self.assertNotContains(response, "Add course")
        self.assertEqual(self.client.get(f"/portal/classes/?class={self.other.pk}").status_code, 403)
        data = {"action": "allocation", "allocation-teacher": self.other_teacher.pk, "allocation-class_subject": self.offering.pk, "allocation-teaching_type": "lecture"}
        self.assertEqual(self.client.post(f"/portal/classes/?class={self.section.pk}", data).status_code, 302)
        self.assertTrue(TeacherAssignment.objects.filter(teacher=self.other_teacher, class_section=self.section).exists())
        self.assertEqual(self.client.post(f"/portal/classes/?class={self.section.pk}", {"action": "enroll"}).status_code, 403)

    def test_permissions_are_independent_and_revocable(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.teacher.can_manage_timetable = True
        self.teacher.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get("/portal/classes/").status_code, 403)
        self.assertEqual(self.client.get(f"/portal/timetable/?class={self.other.pk}").status_code, 403)
        self.teacher.can_manage_timetable = False
        self.teacher.save()
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)

    def test_weekly_save_edit_disable_and_remove(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        entry = TimetableEntry.objects.get()
        self.assertEqual(entry.weekdays, [0, 2])
        self.assertContains(self.client.get(self.url + "&date=2026-10-05"), "09:00–10:00")
        edit = self.url + f"&edit={entry.pk}"
        self.assertEqual(self.client.post(edit, {**self.data, "frequency": "monthly", "month_days": [15], "is_active": ""}).status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.month_days, [15])
        self.assertEqual(entry.weekdays, [])
        self.assertFalse(entry.is_active)
        self.assertEqual(self.client.post(self.url, {"action": "remove", "entry_id": entry.pk}).status_code, 302)
        self.assertFalse(TimetableEntry.objects.exists())

    def test_conflicts_are_blocked_but_adjacent_times_are_allowed(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, self.data)
        self.assertContains(self.client.post(self.url, {**self.data, "frequency": "daily", "start_time": "09:30", "end_time": "10:30"}), "overlaps another booking")
        self.assertEqual(TimetableEntry.objects.count(), 1)
        self.assertEqual(self.client.post(self.url, {**self.data, "start_time": "10:00", "end_time": "11:00"}).status_code, 302)
        self.assertEqual(TimetableEntry.objects.count(), 2)

    def test_same_teacher_in_another_class_cannot_be_double_booked(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, self.data)
        self.other_allocation.teacher = self.teacher
        self.other_allocation.save()
        response = self.client.post(f"/portal/timetable/?class={self.other.pk}", {**self.data, "allocation": self.other_allocation.pk})
        self.assertContains(response, "overlaps another booking")
        self.assertEqual(TimetableEntry.objects.count(), 1)

    def test_bad_recurrences_and_times_are_rejected(self):
        self.client.force_login(self.admin)
        for update in ({"weekdays": []}, {"frequency": "monthly", "month_days": []}, {"frequency": "monthly", "month_days": [32]}, {"end_time": "08:00"}, {"end_date": "2026-09-30"}, {"allocation": self.other_allocation.pk}):
            self.assertEqual(self.client.post(self.url, {**self.data, **update}).status_code, 200)
            self.assertFalse(TimetableEntry.objects.exists())

    def test_foreign_entry_cannot_be_edited_or_removed(self):
        self.client.force_login(self.admin)
        self.client.post(f"/portal/timetable/?class={self.other.pk}", {**self.data, "allocation": self.other_allocation.pk})
        entry = TimetableEntry.objects.get()
        self.teacher.can_manage_timetable = True
        self.teacher.save()
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.url + f"&edit={entry.pk}").status_code, 403)
        self.assertEqual(self.client.post(self.url, {"action": "remove", "entry_id": entry.pk}).status_code, 403)
        self.assertTrue(TimetableEntry.objects.filter(pk=entry.pk).exists())

    def test_scheduled_allocation_is_protected_from_removal(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, self.data)
        response = self.client.post(f"/portal/classes/?class={self.section.pk}", {"action": "remove_allocation", "target_id": self.allocation.pk})
        self.assertContains(response, "timetable entries before removing")
        self.assertTrue(TeacherAssignment.objects.filter(pk=self.allocation.pk).exists())

    def test_teacher_role_removal_clears_permissions_even_on_partial_save(self):
        self.teacher.can_manage_classes = self.teacher.can_manage_timetable = True
        self.teacher.save()
        self.teacher.role = "STUDENT"
        self.teacher.save(update_fields=("role",))
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.can_manage_classes)
        self.assertFalse(self.teacher.can_manage_timetable)
