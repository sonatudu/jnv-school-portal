from datetime import date, time

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import UserCategory

from .attendance_roster import (
    build_class_attendance_report,
    build_house_attendance_report,
    build_school_attendance_report,
    build_student_attendance_history,
    completion_status,
    orphan_entries_for_session,
    roster_student_ids_by_session,
    students_for_session,
)
from .models import (
    AcademicYear,
    ActivitySession,
    ActivitySessionParticipant,
    ActivityType,
    AttendanceEntry,
    AttendanceRevision,
    AttendanceStatus,
    AudienceKind,
    ClassSection,
    DutyType,
    House,
    HouseMasterAssignment,
    Routine,
    SchoolCalendarDay,
    StaffDutyAssignment,
    Student,
    StudentClassMembership,
    StudentGroup,
    StudentGroupMembership,
    StudentHouseMembership,
)


User = get_user_model()


class AttendanceRosterTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.staff = User.objects.create_user(
            username="teacher1",
            password="x",
            category=UserCategory.STAFF,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
            default_audience_kind=AudienceKind.CLASS,
        )
        self.student_a = Student.objects.create(
            admission_number="A1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="B1",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.inactive = Student.objects.create(
            admission_number="I1",
            roll_number=3,
            first_name="Ina",
            last_name="I",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
            is_active=False,
        )

    def _session(self, **kwargs):
        defaults = {
            "date": date(2026, 8, 28),
            "academic_year": self.year,
            "activity_type": self.activity,
            "name": "Period 3",
            "start_time": time(9, 0),
            "end_time": time(9, 40),
            "audience_kind": AudienceKind.CLASS,
            "class_section": self.section,
            "responsible_staff": self.staff,
        }
        defaults.update(kwargs)
        return ActivitySession.objects.create(**defaults)

    def test_class_roster_is_live_active_students(self):
        session = self._session()
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_a])

    def test_house_roster_uses_membership_not_class(self):
        StudentHouseMembership.objects.create(
            student=self.student_b,
            house=self.house,
            academic_year=self.year,
        )
        session = self._session(
            audience_kind=AudienceKind.HOUSE,
            class_section=None,
            house=self.house,
        )
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_b])

    def test_group_roster_uses_snapshot_not_live_membership(self):
        group = StudentGroup.objects.create(name="Band", academic_year=self.year)
        StudentGroupMembership.objects.create(group=group, student=self.student_a)
        session = self._session(
            audience_kind=AudienceKind.STUDENT_GROUP,
            class_section=None,
            student_group=group,
        )
        ActivitySessionParticipant.objects.create(session=session, student=self.student_b)
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_b])

    def test_selected_students_roster_uses_participants(self):
        session = self._session(
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            class_section=None,
        )
        ActivitySessionParticipant.objects.create(session=session, student=self.student_b)
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_b])

    def test_school_roster_is_live_year_students(self):
        session = self._session(
            audience_kind=AudienceKind.SCHOOL,
            class_section=None,
        )
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_a, self.student_b])

    def test_orphan_entries_are_returned(self):
        session = self._session()
        entry = AttendanceEntry.objects.create(
            activity_session=session,
            student=self.student_b,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        orphans = list(orphan_entries_for_session(session, [self.student_a.pk]))
        self.assertEqual(orphans, [entry])

    def test_batched_roster_ids_match_students_for_session(self):
        class_session = self._session()
        house_session = self._session(
            audience_kind=AudienceKind.HOUSE,
            class_section=None,
            house=self.house,
            name="House roll",
        )
        StudentHouseMembership.objects.create(
            student=self.student_b,
            house=self.house,
            academic_year=self.year,
        )
        selected = self._session(
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            class_section=None,
            name="Selected",
        )
        ActivitySessionParticipant.objects.create(session=selected, student=self.student_a)
        batched = roster_student_ids_by_session(
            [class_session, house_session, selected]
        )
        for session in (class_session, house_session, selected):
            self.assertEqual(
                batched[session.pk],
                {student.pk for student in students_for_session(session)},
            )

    def test_completion_status_labels(self):
        self.assertEqual(completion_status(0, 0), "Empty roster")
        self.assertEqual(completion_status(0, 4), "Not started")
        self.assertEqual(completion_status(2, 4), "Partial")
        self.assertEqual(completion_status(4, 4), "Complete")


class MarkAttendanceAdminTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.staff = User.objects.create_user(
            username="teacher1",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other = User.objects.create_user(
            username="teacher2",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly no roll",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="A1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="A2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.session = ActivitySession.objects.create(
            date=date(2026, 8, 28),
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.url = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[self.session.pk],
        )

    def test_responsible_partial_save_and_correction(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")
        self.assertContains(response, "0 / 2 marked")

        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.PRESENT,
                f"notes_{self.student_a.pk}": "first",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceEntry.objects.count(), 1)
        entry = AttendanceEntry.objects.get()
        self.assertEqual(entry.student, self.student_a)
        self.assertEqual(entry.taken_by, self.staff)
        self.assertIsNone(entry.updated_by)
        self.assertEqual(AttendanceRevision.objects.count(), 0)

        taken_at = entry.taken_at
        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.LATE,
                f"notes_{self.student_a.pk}": "late arrival",
                "change_reason": "came late",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, AttendanceStatus.LATE)
        self.assertEqual(entry.taken_by, self.staff)
        self.assertEqual(entry.taken_at, taken_at)
        self.assertEqual(entry.updated_by, self.staff)
        revision = AttendanceRevision.objects.get()
        self.assertEqual(revision.old_status, AttendanceStatus.PRESENT)
        self.assertEqual(revision.new_status, AttendanceStatus.LATE)
        self.assertEqual(revision.reason, "came late")

        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.LATE,
                f"notes_{self.student_a.pk}": "notes only",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceRevision.objects.count(), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.notes, "notes only")
        self.assertEqual(entry.taken_by, self.staff)

        response = self.client.post(
            self.url,
            {"action": "save_unmarked_present"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceEntry.objects.count(), 2)
        entry.refresh_from_db()
        self.assertEqual(entry.status, AttendanceStatus.LATE)
        other = AttendanceEntry.objects.get(student=self.student_b)
        self.assertEqual(other.status, AttendanceStatus.PRESENT)
        self.assertEqual(other.taken_by, self.staff)

        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"notes_{self.student_a.pk}": "cleared",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be cleared")
        entry.refresh_from_db()
        self.assertEqual(entry.status, AttendanceStatus.LATE)

    def test_unrelated_staff_gets_403(self):
        self.client.force_login(self.other)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "You are not authorized to mark attendance for this session.",
            status_code=403,
        )

    def test_unrelated_staff_post_to_guessed_url_gets_403(self):
        self.client.force_login(self.other)
        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AttendanceEntry.objects.count(), 0)

    def test_non_attendance_activity_gets_403(self):
        self.session.activity_type = self.no_att
        self.session.save()
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "This activity does not take attendance.",
            status_code=403,
        )


class AttendanceAuthorizationAdminTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.responsible = User.objects.create_user(
            username="responsible",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.hm = User.objects.create_user(
            username="housemaster",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.mod = User.objects.create_user(
            username="mod",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.ordinary = User.objects.create_user(
            username="ordinary",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_superuser=True,
        )
        self.admin_user = User.objects.create_user(
            username="adminuser",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.parent = User.objects.create_user(
            username="parentuser",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.student = Student.objects.create(
            admission_number="A1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.class_session = ActivitySession.objects.create(
            date=date(2026, 8, 28),
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.responsible,
        )
        self.house_session = ActivitySession.objects.create(
            date=date(2026, 8, 28),
            academic_year=self.year,
            activity_type=self.activity,
            name="House roll",
            start_time=time(10, 0),
            end_time=time(10, 30),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.responsible,
        )
        HouseMasterAssignment.objects.create(
            staff=self.hm,
            house=self.house,
            academic_year=self.year,
        )
        self.duty = DutyType.objects.create(
            name="MOD",
            unique_per_day=True,
            is_active=True,
        )
        StaffDutyAssignment.objects.create(
            duty_type=self.duty,
            staff=self.mod,
            date=self.class_session.date,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student,
            house=self.house,
            academic_year=self.year,
        )

    def _mark_url(self, session):
        return reverse(
            "admin:school_activitysession_mark_attendance",
            args=[session.pk],
        )

    def _admin_request(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def test_house_master_can_mark_house_session(self):
        self.client.force_login(self.hm)
        url = self._mark_url(self.house_session)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = AttendanceEntry.objects.get()
        self.assertEqual(entry.taken_by, self.hm)
        self.assertEqual(entry.activity_session, self.house_session)

    def test_unique_mod_can_mark_session_on_that_date(self):
        self.client.force_login(self.mod)
        url = self._mark_url(self.class_session)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.ABSENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceEntry.objects.get().taken_by, self.mod)

    def test_administration_can_mark_any_attendance_capable_session(self):
        self.client.force_login(self.admin_user)
        url = self._mark_url(self.class_session)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.LATE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceEntry.objects.get().taken_by, self.admin_user)

    def test_ordinary_staff_cannot_mark(self):
        self.client.force_login(self.ordinary)
        url = self._mark_url(self.class_session)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            url,
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AttendanceEntry.objects.count(), 0)

    def test_parent_cannot_mark(self):
        self.client.force_login(self.parent)
        url = self._mark_url(self.class_session)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AttendanceEntry.objects.count(), 0)

    def test_staff_cannot_change_activity_session_in_admin(self):
        session_admin = site._registry[ActivitySession]
        request = self._admin_request(self.ordinary)
        self.assertFalse(session_admin.has_change_permission(request))
        self.assertFalse(session_admin.has_change_permission(request, self.class_session))
        self.assertFalse(session_admin.has_add_permission(request))
        self.assertFalse(session_admin.has_delete_permission(request, self.class_session))

        self.client.force_login(self.ordinary)
        url = reverse(
            "admin:school_activitysession_change",
            args=[self.class_session.pk],
        )
        response = self.client.post(
            url,
            {
                "date": self.class_session.date.isoformat(),
                "academic_year": self.year.pk,
                "activity_type": self.activity.pk,
                "name": "Hacked name",
                "start_time": "09:00:00",
                "end_time": "09:40:00",
                "audience_kind": AudienceKind.CLASS,
                "class_section": self.section.pk,
                "responsible_staff": self.ordinary.pk,
            },
        )
        self.assertNotEqual(response.status_code, 200)
        self.class_session.refresh_from_db()
        self.assertEqual(self.class_session.name, "Period 3")
        self.assertEqual(self.class_session.responsible_staff_id, self.responsible.pk)

    def test_attendance_revision_admin_cannot_delete(self):
        entry = AttendanceEntry.objects.create(
            activity_session=self.class_session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.responsible,
        )
        entry.status = AttendanceStatus.ABSENT
        entry.updated_by = self.admin_user
        entry._status_change_reason = "correction"
        entry.save()
        revision = AttendanceRevision.objects.get()

        revision_admin = site._registry[AttendanceRevision]
        for user in (self.admin_user, self.ordinary, self.responsible):
            request = self._admin_request(user)
            self.assertFalse(revision_admin.has_add_permission(request))
            self.assertFalse(revision_admin.has_change_permission(request, revision))
            self.assertFalse(revision_admin.has_delete_permission(request))
            self.assertFalse(revision_admin.has_delete_permission(request, revision))

        self.client.force_login(self.admin_user)
        url = reverse("admin:school_attendancerevision_delete", args=[revision.pk])
        response = self.client.post(url, {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(AttendanceRevision.objects.filter(pk=revision.pk).exists())

    def test_participant_admin_writes_are_administration_only(self):
        participant = ActivitySessionParticipant.objects.create(
            session=self.class_session,
            student=self.student,
        )
        participant_admin = site._registry[ActivitySessionParticipant]
        staff_request = self._admin_request(self.ordinary)
        admin_request = self._admin_request(self.admin_user)
        self.assertFalse(participant_admin.has_add_permission(staff_request))
        self.assertFalse(participant_admin.has_change_permission(staff_request, participant))
        self.assertFalse(participant_admin.has_delete_permission(staff_request, participant))
        self.assertTrue(participant_admin.has_add_permission(admin_request))
        self.assertTrue(participant_admin.has_change_permission(admin_request, participant))
        self.assertTrue(participant_admin.has_delete_permission(admin_request, participant))

        self.client.force_login(self.ordinary)
        add_url = reverse("admin:school_activitysessionparticipant_add")
        response = self.client.post(
            add_url,
            {"session": self.house_session.pk, "student": self.student.pk},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ActivitySessionParticipant.objects.count(), 1)


class AttendanceOverviewAdminTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.admin_user = User.objects.create_user(
            username="overview-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="overview-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="overview-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="overview-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="overview-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="OA1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="OA2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="OB1",
            roll_number=1,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.routine = Routine.objects.create(
            academic_year=self.year,
            name="Regular",
            is_active=True,
        )
        SchoolCalendarDay.objects.create(
            date=self.day,
            academic_year=self.year,
            routine=self.routine,
        )
        self.staff_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.other_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.empty_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Selected empty",
            start_time=time(11, 0),
            end_time=time(11, 20),
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            responsible_staff=self.staff,
        )
        self.url = reverse("admin:school_activitysession_attendance_overview")
        self.staff_mark_url = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[self.staff_session.pk],
        )
        self.other_mark_url = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[self.other_session.pk],
        )

    def test_administration_can_access_overview(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Period 3 VI-B")

    def test_authorized_staff_can_access_overview(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Period 3 VI-A")

    def test_parent_and_inactive_cannot_access_overview(self):
        self.client.force_login(self.parent)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.inactive)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertIn(response.status_code, (302, 403))

    def test_staff_does_not_see_unauthorized_sessions(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(response, "Period 3 VI-A")
        self.assertNotContains(response, "Period 3 VI-B")
        self.assertContains(response, self.staff_mark_url)
        self.assertNotContains(response, self.other_mark_url)

    def test_non_attendance_sessions_are_excluded(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertNotContains(response, "Silent assembly")

    def test_marked_count_uses_roster_and_ignores_orphans(self):
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.other_student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(response, "Partial")
        html = response.content.decode()
        self.assertIn(">1</td>", html)
        self.assertIn(">2</td>", html)
        self.assertNotContains(response, "Complete")

    def test_overview_status_labels(self):
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        complete_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Selected complete",
            start_time=time(12, 0),
            end_time=time(12, 20),
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            responsible_staff=self.staff,
        )
        ActivitySessionParticipant.objects.create(
            session=complete_session,
            student=self.student_a,
        )
        AttendanceEntry.objects.create(
            activity_session=complete_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(response, "Empty roster")
        self.assertContains(response, "Not started")
        self.assertContains(response, "Partial")
        self.assertContains(response, "Complete")

    def test_missing_calendar_day_is_empty_and_does_not_generate(self):
        other_day = date(2026, 8, 29)
        ActivitySession.objects.create(
            date=other_day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Should stay hidden",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        before = ActivitySession.objects.count()
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": other_day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No school calendar day for this date")
        self.assertNotContains(response, "Should stay hidden")
        self.assertEqual(ActivitySession.objects.count(), before)


class StudentAttendanceHistoryTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.admin_user = User.objects.create_user(
            username="hist-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="hist-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="hist-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="hist-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="hist-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=False,
        )
        self.student = Student.objects.create(
            admission_number="H1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="H2",
            roll_number=1,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.later_day = date(2026, 8, 29)
        self.staff_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.later_session = ActivitySession.objects.create(
            date=self.later_day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 later",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.other_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.old_session = ActivitySession.objects.create(
            date=date(2025, 8, 28),
            academic_year=self.other_year,
            activity_type=self.activity,
            name="Old year period",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.url = reverse(
            "admin:school_student_attendance_history",
            args=[self.student.pk],
        )

    def test_administration_sees_authorized_history(self):
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Period 3 later")
        self.assertContains(response, "Present")

    def test_staff_sees_only_authorized_sessions(self):
        AttendanceEntry.objects.create(
            activity_session=self.other_session,
            student=self.student,
            status=AttendanceStatus.ABSENT,
            taken_by=self.other_staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student,
            status=AttendanceStatus.LATE,
            taken_by=self.staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Late")
        self.assertNotContains(response, "Period 3 VI-B")

    def test_parent_and_inactive_cannot_access_history(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.inactive)
        self.assertIn(self.client.get(self.url).status_code, (302, 403))

    def test_year_and_date_range_filter(self):
        AttendanceEntry.objects.create(
            activity_session=self.old_session,
            student=self.student,
            status=AttendanceStatus.LEAVE,
            taken_by=self.staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Old year period")
        response = self.client.get(
            self.url,
            {"academic_year": self.other_year.pk},
        )
        self.assertContains(response, "Old year period")
        self.assertContains(response, "Leave")
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Period 3 VI-A")
        self.assertNotContains(response, "Period 3 later")

    def test_orphan_entry_is_listed_and_counted(self):
        AttendanceEntry.objects.create(
            activity_session=self.other_session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.other_staff,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertContains(response, "Period 3 VI-B")
        self.assertContains(response, "Not on current roster")
        self.assertContains(response, "Total marked: 1")

    def test_percentage_and_unmarked_are_not_absent(self):
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        history = build_student_attendance_history(
            self.student,
            [self.staff_session, self.later_session],
        )
        self.assertEqual(history["total_marked"], 1)
        self.assertEqual(history["total_eligible"], 2)
        self.assertEqual(history["percentage"], 50.0)
        self.assertEqual(history["status_counts"][AttendanceStatus.ABSENT], 0)
        self.assertEqual(len(history["unmarked_rows"]), 1)
        self.assertEqual(history["unmarked_rows"][0]["session"], self.later_session)

        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.later_day.isoformat(),
            },
        )
        self.assertContains(response, "50%")
        self.assertContains(response, "Period 3 later")
        self.assertContains(response, "Not marked yet")
        unmarked_html = response.content.decode().split("Not marked yet", 1)[1]
        self.assertNotIn("Absent", unmarked_html)

    def test_non_attendance_sessions_excluded(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Silent assembly")

    def test_guessed_url_does_not_leak_unauthorized_entries(self):
        AttendanceEntry.objects.create(
            activity_session=self.other_session,
            student=self.student,
            status=AttendanceStatus.ABSENT,
            taken_by=self.other_staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Period 3 VI-B")
        other_mark = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[self.other_session.pk],
        )
        self.assertNotContains(response, other_mark)

    def test_history_post_does_not_write_attendance(self):
        self.client.force_login(self.staff)
        before = AttendanceEntry.objects.count()
        response = self.client.post(
            self.url,
            {f"status_{self.student.pk}": AttendanceStatus.ABSENT},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AttendanceEntry.objects.count(), before)

    def test_zero_eligible_percentage_is_na(self):
        selected = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Empty selected",
            start_time=time(11, 0),
            end_time=time(11, 20),
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            responsible_staff=self.staff,
        )
        history = build_student_attendance_history(self.student, [selected])
        self.assertEqual(history["total_eligible"], 0)
        self.assertEqual(history["total_marked"], 0)
        self.assertIsNone(history["percentage"])


class ClassAttendanceReportTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.admin_user = User.objects.create_user(
            username="crep-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="crep-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="crep-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="crep-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="crep-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.period = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.remedial = ActivityType.objects.create(
            name="Remedial",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="CR1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="CR2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.orphan = Student.objects.create(
            admission_number="CR3",
            roll_number=1,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.later_day = date(2026, 8, 29)
        self.period_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.remedial_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.remedial,
            name="Remedial 3",
            start_time=time(10, 0),
            end_time=time(10, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.later_session = ActivitySession.objects.create(
            date=self.later_day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period later",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.other_class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="House roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.staff,
        )
        self.old_session = ActivitySession.objects.create(
            date=date(2025, 8, 28),
            academic_year=self.other_year,
            activity_type=self.period,
            name="Old year period",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.url = reverse(
            "admin:school_classsection_attendance_report",
            args=[self.section.pk],
        )

    def test_administration_can_access_report(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")
        self.assertContains(response, "Current roster: 2")

    def test_authorized_staff_can_access_report(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")

    def test_parent_and_inactive_cannot_access_report(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.inactive)
        self.assertIn(self.client.get(self.url).status_code, (302, 403))

    def test_staff_cannot_see_other_teacher_class_sessions(self):
        AttendanceEntry.objects.create(
            activity_session=self.other_class_session,
            student=self.orphan,
            status=AttendanceStatus.ABSENT,
            taken_by=self.other_staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Period VI-B")
        self.assertNotContains(response, "Cara")

    def test_staff_cannot_see_other_class_roster_without_authorized_sessions(self):
        other_url = reverse(
            "admin:school_classsection_attendance_report",
            args=[self.other_section.pk],
        )
        self.client.force_login(self.staff)
        response = self.client.get(other_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current roster: 0")
        self.assertContains(response, "Total marked: 0")
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "Ada")

    def test_year_and_date_range_filter(self):
        AttendanceEntry.objects.create(
            activity_session=self.old_session,
            student=self.student_a,
            status=AttendanceStatus.LEAVE,
            taken_by=self.staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertContains(response, "Leave: 0")
        response = self.client.get(self.url, {"academic_year": self.other_year.pk})
        self.assertContains(response, "Leave: 1")
        self.assertContains(response, "Ada")
        self.assertContains(response, "Not on current roster")
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Ada")
        report = build_class_attendance_report(
            self.section,
            self.year,
            [self.period_session, self.remedial_session],
        )
        self.assertEqual(report["roster_size"], 2)

    def test_invalid_date_range_shows_error_and_no_data(self):
        AttendanceEntry.objects.create(
            activity_session=self.period_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.later_day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "start date must be on or before")
        self.assertContains(response, "Total marked: 0")

    def test_current_roster_and_orphan_marks(self):
        AttendanceEntry.objects.create(
            activity_session=self.period_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.period_session,
            student=self.orphan,
            status=AttendanceStatus.LATE,
            taken_by=self.staff,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Current roster: 2")
        self.assertContains(response, "Students with marks: 2")
        self.assertContains(response, "Roster students with no marks: 1")
        self.assertContains(response, "Cara")
        self.assertContains(response, "Not on current roster")

    def test_unmarked_is_not_absent_and_percentages(self):
        AttendanceEntry.objects.create(
            activity_session=self.period_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.remedial_session,
            student=self.student_a,
            status=AttendanceStatus.ABSENT,
            taken_by=self.staff,
        )
        report = build_class_attendance_report(
            self.section,
            self.year,
            [self.period_session, self.remedial_session],
        )
        by_name = {row["student"].pk: row for row in report["student_rows"]}
        ada = by_name[self.student_a.pk]
        ben = by_name[self.student_b.pk]
        self.assertEqual(ada["present"], 1)
        self.assertEqual(ada["absent"], 1)
        self.assertEqual(ada["marked"], 2)
        self.assertEqual(ada["unmarked"], 0)
        self.assertEqual(ada["percentage"], 50.0)
        self.assertEqual(ben["marked"], 0)
        self.assertEqual(ben["absent"], 0)
        self.assertEqual(ben["unmarked"], 2)
        self.assertIsNone(ben["percentage"])
        self.assertEqual(report["status_counts"][AttendanceStatus.PRESENT], 1)
        self.assertEqual(report["status_counts"][AttendanceStatus.ABSENT], 1)
        self.assertEqual(report["total_marked"], 2)
        self.assertEqual(report["percentage"], 50.0)
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "50%")
        self.assertContains(response, "N/A")

    def test_includes_remedial_excludes_non_attendance_and_house(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        report = build_class_attendance_report(
            self.section,
            self.year,
            [self.period_session, self.remedial_session],
        )
        ada = [row for row in report["student_rows"] if row["student"] == self.student_a][0]
        self.assertEqual(ada["eligible"], 2)
        self.assertNotContains(response, "Silent assembly")
        self.assertNotContains(response, "House roll")

    def test_report_does_not_write_or_generate(self):
        self.client.force_login(self.staff)
        before_sessions = ActivitySession.objects.count()
        before_entries = AttendanceEntry.objects.count()
        self.client.get(self.url)
        self.client.post(self.url, {"status": AttendanceStatus.ABSENT})
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(AttendanceEntry.objects.count(), before_entries)

    def test_zero_marked_percentage_is_na(self):
        report = build_class_attendance_report(
            self.section,
            self.year,
            [self.period_session],
        )
        self.assertEqual(report["total_marked"], 0)
        self.assertIsNone(report["percentage"])
        for row in report["student_rows"]:
            self.assertIsNone(row["percentage"])
            self.assertEqual(row["absent"], 0)

    def test_empty_sessions_hides_class_roster(self):
        report = build_class_attendance_report(self.section, self.year, [])
        self.assertEqual(report["roster_size"], 0)
        self.assertEqual(report["student_rows"], [])


class HouseAttendanceReportTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.other_house = House.objects.create(name="Nilgiri", code="NL")
        self.admin_user = User.objects.create_user(
            username="hrep-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.hm = User.objects.create_user(
            username="hrep-hm",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_hm = User.objects.create_user(
            username="hrep-other-hm",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.ordinary = User.objects.create_user(
            username="hrep-ordinary",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="hrep-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="hrep-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.activity = ActivityType.objects.create(
            name="House roll call",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="House meeting",
            takes_attendance=False,
        )
        self.period = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.student_a = Student.objects.create(
            admission_number="HR1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="HR2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.orphan = Student.objects.create(
            admission_number="HR3",
            roll_number=3,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student_a,
            house=self.house,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student_b,
            house=self.house,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.orphan,
            house=self.other_house,
            academic_year=self.year,
        )
        HouseMasterAssignment.objects.create(
            staff=self.hm,
            house=self.house,
            academic_year=self.year,
        )
        HouseMasterAssignment.objects.create(
            staff=self.other_hm,
            house=self.other_house,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.later_day = date(2026, 8, 29)
        self.house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Morning house",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.hm,
        )
        self.later_session = ActivitySession.objects.create(
            date=self.later_day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Evening house",
            start_time=time(18, 0),
            end_time=time(18, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.hm,
        )
        self.other_house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Nilgiri house",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.other_house,
            responsible_staff=self.other_hm,
        )
        self.class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.ordinary,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent house",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.hm,
        )
        self.old_session = ActivitySession.objects.create(
            date=date(2025, 8, 28),
            academic_year=self.other_year,
            activity_type=self.activity,
            name="Old house roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.hm,
        )
        self.url = reverse(
            "admin:school_house_attendance_report",
            args=[self.house.pk],
        )

    def test_administration_can_access_report(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")
        self.assertContains(response, "Current roster: 2")

    def test_house_master_can_access_report(self):
        self.client.force_login(self.hm)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")

    def test_parent_and_inactive_cannot_access_report(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.inactive)
        self.assertIn(self.client.get(self.url).status_code, (302, 403))

    def test_ordinary_staff_and_other_hm_do_not_see_house_marks(self):
        AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.student_a,
            status=AttendanceStatus.ABSENT,
            taken_by=self.hm,
        )
        self.client.force_login(self.ordinary)
        response = self.client.get(self.url)
        self.assertContains(response, "Total marked: 0")
        self.assertContains(response, "Current roster: 0")
        self.assertNotContains(response, "Ada")
        self.client.force_login(self.other_hm)
        response = self.client.get(self.url)
        self.assertContains(response, "Total marked: 0")
        self.assertContains(response, "Current roster: 0")
        self.assertNotContains(response, "Ada")

    def test_year_and_date_range_filter(self):
        AttendanceEntry.objects.create(
            activity_session=self.old_session,
            student=self.student_a,
            status=AttendanceStatus.LEAVE,
            taken_by=self.hm,
        )
        self.client.force_login(self.hm)
        response = self.client.get(self.url)
        self.assertContains(response, "Leave: 0")
        response = self.client.get(self.url, {"academic_year": self.other_year.pk})
        self.assertContains(response, "Leave: 1")
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Ada")
        report = build_house_attendance_report(
            self.house,
            self.year,
            [self.house_session],
        )
        self.assertEqual(report["roster_size"], 2)

    def test_invalid_date_range_shows_error_and_no_data(self):
        AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.hm,
        )
        self.client.force_login(self.hm)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.later_day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "start date must be on or before")
        self.assertContains(response, "Total marked: 0")

    def test_current_roster_and_orphan_marks(self):
        AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.hm,
        )
        AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.orphan,
            status=AttendanceStatus.LATE,
            taken_by=self.hm,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Current roster: 2")
        self.assertContains(response, "Students with marks: 2")
        self.assertContains(response, "Roster students with no marks: 1")
        self.assertContains(response, "Cara")
        self.assertContains(response, "Not on current roster")

    def test_unmarked_is_not_absent_and_percentages(self):
        AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.hm,
        )
        AttendanceEntry.objects.create(
            activity_session=self.later_session,
            student=self.student_a,
            status=AttendanceStatus.ABSENT,
            taken_by=self.hm,
        )
        report = build_house_attendance_report(
            self.house,
            self.year,
            [self.house_session, self.later_session],
        )
        by_id = {row["student"].pk: row for row in report["student_rows"]}
        ada = by_id[self.student_a.pk]
        ben = by_id[self.student_b.pk]
        self.assertEqual(ada["present"], 1)
        self.assertEqual(ada["absent"], 1)
        self.assertEqual(ada["marked"], 2)
        self.assertEqual(ada["unmarked"], 0)
        self.assertEqual(ada["percentage"], 50.0)
        self.assertEqual(ben["marked"], 0)
        self.assertEqual(ben["absent"], 0)
        self.assertEqual(ben["unmarked"], 2)
        self.assertIsNone(ben["percentage"])
        self.assertEqual(report["total_marked"], 2)
        self.assertEqual(report["percentage"], 50.0)
        self.client.force_login(self.hm)
        response = self.client.get(self.url)
        self.assertContains(response, "50%")
        self.assertContains(response, "N/A")

    def test_excludes_class_and_non_attendance_sessions(self):
        AttendanceEntry.objects.create(
            activity_session=self.class_session,
            student=self.student_a,
            status=AttendanceStatus.LEAVE,
            taken_by=self.ordinary,
        )
        report = build_house_attendance_report(
            self.house,
            self.year,
            [self.house_session],
        )
        ada = [row for row in report["student_rows"] if row["student"] == self.student_a][0]
        self.assertEqual(ada["eligible"], 1)
        self.client.force_login(self.hm)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Leave: 0")
        self.assertNotContains(response, "Period 3")
        self.assertNotContains(response, "Silent house")

    def test_report_does_not_write_or_generate(self):
        self.client.force_login(self.hm)
        before_sessions = ActivitySession.objects.count()
        before_entries = AttendanceEntry.objects.count()
        self.client.get(self.url)
        self.client.post(self.url, {"status": AttendanceStatus.ABSENT})
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(AttendanceEntry.objects.count(), before_entries)

    def test_zero_marked_percentage_is_na(self):
        report = build_house_attendance_report(
            self.house,
            self.year,
            [self.house_session],
        )
        self.assertEqual(report["total_marked"], 0)
        self.assertIsNone(report["percentage"])
        for row in report["student_rows"]:
            self.assertIsNone(row["percentage"])
            self.assertEqual(row["absent"], 0)

    def test_empty_sessions_hides_house_roster(self):
        report = build_house_attendance_report(self.house, self.year, [])
        self.assertEqual(report["roster_size"], 0)
        self.assertEqual(report["student_rows"], [])


class AbsenceRegisterAdminTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.admin_user = User.objects.create_user(
            username="register-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="register-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="register-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.lonely_staff = User.objects.create_user(
            username="register-lonely",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="register-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="register-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="RA1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="RA2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="RB1",
            roll_number=1,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.orphan = Student.objects.create(
            admission_number="RX1",
            roll_number=9,
            first_name="Ora",
            last_name="O",
            date_of_birth=date(2014, 1, 4),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.routine = Routine.objects.create(
            academic_year=self.year,
            name="Regular",
            is_active=True,
        )
        self.calendar_day = SchoolCalendarDay.objects.create(
            date=self.day,
            academic_year=self.year,
            routine=self.routine,
        )
        self.staff_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.other_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3 VI-B",
            start_time=time(10, 0),
            end_time=time(10, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student_a,
            status=AttendanceStatus.ABSENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.student_b,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.staff_session,
            student=self.orphan,
            status=AttendanceStatus.LEAVE,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.other_session,
            student=self.other_student,
            status=AttendanceStatus.LATE,
            taken_by=self.other_staff,
        )
        self.url = reverse("admin:school_activitysession_absence_register")
        self.overview_url = reverse(
            "admin:school_activitysession_attendance_overview"
        )
        self.register_dated = f"{self.url}?date={self.day.isoformat()}"

    def test_administration_sees_exception_marks_from_all_authorized_sessions(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RA1")
        self.assertContains(response, "RB1")
        self.assertContains(response, "RX1")
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Period 3 VI-B")

    def test_staff_sees_only_sessions_user_may_mark(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RA1")
        self.assertContains(response, "RX1")
        self.assertNotContains(response, "RB1")
        self.assertNotContains(response, "Period 3 VI-B")

    def test_staff_cannot_see_another_teachers_exception_student_names(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "RB1")

    def test_staff_with_no_authorized_sessions_sees_no_student_names(self):
        self.client.force_login(self.lonely_staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Ada")
        self.assertNotContains(response, "Ben")
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "Ora")
        self.assertNotContains(response, "RA1")
        self.assertNotContains(response, "RA2")
        self.assertNotContains(response, "RB1")
        self.assertNotContains(response, "RX1")
        self.assertContains(
            response,
            "No Absent/Late/Leave marks on your authorized sessions.",
        )

    def test_parent_gets_403(self):
        self.client.force_login(self.parent)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(response.status_code, 403)

    def test_inactive_user_is_blocked(self):
        self.client.force_login(self.inactive)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertIn(response.status_code, (302, 403))

    def test_absent_late_leave_appear_by_default_and_present_is_excluded(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(response, "Absent")
        self.assertContains(response, "Late")
        self.assertContains(response, "Leave")
        self.assertContains(response, "RA1")
        self.assertContains(response, "RB1")
        self.assertContains(response, "RX1")
        self.assertNotContains(response, "RA2")
        self.assertNotContains(response, ">Ben<")

    def test_status_filter_narrows_results(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.url,
            {"date": self.day.isoformat(), "status": AttendanceStatus.LATE},
        )
        self.assertContains(response, "RB1")
        self.assertNotContains(response, "RA1")
        self.assertNotContains(response, "RX1")
        self.assertContains(response, "Late")

        empty = self.client.get(
            self.url,
            {"date": self.day.isoformat(), "status": AttendanceStatus.ABSENT},
        )
        self.assertContains(empty, "RA1")
        self.assertNotContains(empty, "RB1")

        self.client.force_login(self.staff)
        staff_late = self.client.get(
            self.url,
            {"date": self.day.isoformat(), "status": AttendanceStatus.LATE},
        )
        self.assertContains(
            staff_late,
            "No Late marks on your authorized sessions.",
        )
        self.assertNotContains(staff_late, "RB1")

    def test_unmarked_students_never_appear(self):
        unmarked = Student.objects.create(
            admission_number="RU1",
            roll_number=3,
            first_name="Uma",
            last_name="U",
            date_of_birth=date(2014, 1, 5),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertNotContains(response, "RU1")
        self.assertNotContains(response, "Uma")
        self.assertEqual(unmarked.attendance_entries.count(), 0)

    def test_non_attendance_sessions_never_appear(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertNotContains(response, "Silent assembly")

    def test_orphan_exception_marks_remain_visible_when_session_authorized(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(response, "RX1")
        self.assertContains(response, "Ora")

    def test_missing_calendar_day_is_empty_and_does_not_generate(self):
        other_day = date(2026, 8, 29)
        ActivitySession.objects.create(
            date=other_day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Should stay hidden",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=ActivitySession.objects.get(name="Should stay hidden"),
            student=self.student_a,
            status=AttendanceStatus.ABSENT,
            taken_by=self.staff,
        )
        before_sessions = ActivitySession.objects.count()
        before_days = SchoolCalendarDay.objects.count()
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": other_day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No school calendar day for this date")
        self.assertNotContains(response, "Should stay hidden")
        self.assertNotContains(response, "RA1")
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(SchoolCalendarDay.objects.count(), before_days)

    def test_invalid_date_shows_error_and_no_data(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"date": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid date.")
        self.assertNotContains(response, "RA1")
        self.assertNotContains(response, "Period 3 VI-A")

    def test_get_and_post_do_not_write_or_generate(self):
        self.client.force_login(self.admin_user)
        before_sessions = ActivitySession.objects.count()
        before_entries = AttendanceEntry.objects.count()
        before_days = SchoolCalendarDay.objects.count()
        get_response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertEqual(get_response.status_code, 200)
        post_response = self.client.post(self.url, {"date": self.day.isoformat()})
        self.assertEqual(post_response.status_code, 405)
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(AttendanceEntry.objects.count(), before_entries)
        self.assertEqual(SchoolCalendarDay.objects.count(), before_days)

    def test_unauthorized_session_student_names_do_not_appear_in_html(self):
        self.client.force_login(self.staff)
        html = self.client.get(
            self.url, {"date": self.day.isoformat()}
        ).content.decode()
        self.assertNotIn("Cara", html)
        self.assertNotIn("RB1", html)
        self.assertNotIn("Period 3 VI-B", html)

    def test_calendar_link_points_to_absence_register_for_date(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin:school_schoolcalendarday_changelist")
        )
        self.assertContains(response, "Absences this date")
        self.assertContains(response, self.register_dated)

    def test_daily_overview_link_points_to_absence_register_for_date(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.overview_url, {"date": self.day.isoformat()}
        )
        self.assertContains(response, "Absence register")
        self.assertContains(response, self.register_dated)


class SchoolAttendanceReportTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.other_house = House.objects.create(name="Nilgiri", code="NL")
        self.admin_user = User.objects.create_user(
            username="srep-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="srep-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="srep-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.lonely_staff = User.objects.create_user(
            username="srep-lonely",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.hm = User.objects.create_user(
            username="srep-hm",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.mod = User.objects.create_user(
            username="srep-mod",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="srep-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="srep-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.period = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.remedial = ActivityType.objects.create(
            name="Remedial",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="SA1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="SA2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="SB1",
            roll_number=1,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.orphan = Student.objects.create(
            admission_number="SX1",
            roll_number=9,
            first_name="Ora",
            last_name="O",
            date_of_birth=date(2014, 1, 4),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student_a,
            house=self.house,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student_b,
            house=self.house,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.later_day = date(2026, 8, 29)
        self.group = StudentGroup.objects.create(name="Band", academic_year=self.year)
        HouseMasterAssignment.objects.create(
            staff=self.hm,
            house=self.house,
            academic_year=self.year,
        )
        self.duty = DutyType.objects.create(
            name="MOD",
            unique_per_day=True,
            is_active=True,
        )
        StaffDutyAssignment.objects.create(
            duty_type=self.duty,
            staff=self.mod,
            date=self.day,
            academic_year=self.year,
        )
        self.class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.remedial_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.remedial,
            name="Remedial VI-A",
            start_time=time(14, 0),
            end_time=time(14, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.other_class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Aravali house roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.other_staff,
        )
        self.other_house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Nilgiri house roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.other_house,
            responsible_staff=self.other_staff,
        )
        self.school_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Morning school",
            start_time=time(6, 30),
            end_time=time(6, 50),
            audience_kind=AudienceKind.SCHOOL,
            responsible_staff=self.other_staff,
        )
        self.group_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Band practice",
            start_time=time(16, 0),
            end_time=time(16, 40),
            audience_kind=AudienceKind.STUDENT_GROUP,
            student_group=self.group,
            responsible_staff=self.other_staff,
        )
        ActivitySessionParticipant.objects.create(
            session=self.group_session,
            student=self.student_a,
        )
        self.selected_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Selected coaching",
            start_time=time(17, 0),
            end_time=time(17, 20),
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            responsible_staff=self.other_staff,
        )
        ActivitySessionParticipant.objects.create(
            session=self.selected_session,
            student=self.student_b,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.old_session = ActivitySession.objects.create(
            date=date(2025, 8, 28),
            academic_year=self.other_year,
            activity_type=self.period,
            name="Old year period",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.class_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.remedial_session,
            student=self.student_a,
            status=AttendanceStatus.ABSENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.class_session,
            student=self.orphan,
            status=AttendanceStatus.LEAVE,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.other_class_session,
            student=self.other_student,
            status=AttendanceStatus.LATE,
            taken_by=self.other_staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.other_staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.school_session,
            student=self.student_b,
            status=AttendanceStatus.PRESENT,
            taken_by=self.other_staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.group_session,
            student=self.student_a,
            status=AttendanceStatus.LATE,
            taken_by=self.other_staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.selected_session,
            student=self.student_b,
            status=AttendanceStatus.LEAVE,
            taken_by=self.other_staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.old_session,
            student=self.student_a,
            status=AttendanceStatus.LEAVE,
            taken_by=self.staff,
        )
        self.url = reverse("admin:school_activitysession_school_attendance_report")
        self.overview_url = reverse(
            "admin:school_activitysession_attendance_overview"
        )
        self.range_query = {
            "academic_year": self.year.pk,
            "date_from": self.day.isoformat(),
            "date_to": self.day.isoformat(),
        }

    def test_administration_sees_all_authorized_attendance_capable_sessions(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Period 3 VI-A")
        self.assertContains(response, "VI-A")
        self.assertContains(response, "VI-B")
        self.assertContains(response, "Aravali")
        self.assertNotContains(response, "Morning school")
        self.assertContains(response, "Whole school")
        self.assertContains(response, "Named student group")
        self.assertContains(response, "Selected students")
        self.assertContains(response, "Period")
        self.assertContains(response, "Remedial")
        self.assertNotContains(response, "Silent assembly")
        report = response.context["report"]
        self.assertEqual(report["total_marked"], 8)
        self.assertEqual(report["status_counts"][AttendanceStatus.PRESENT], 3)
        self.assertEqual(report["status_counts"][AttendanceStatus.ABSENT], 1)
        self.assertEqual(report["status_counts"][AttendanceStatus.LATE], 2)
        self.assertEqual(report["status_counts"][AttendanceStatus.LEAVE], 2)

    def test_staff_sees_only_sessions_user_may_mark(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.status_code, 200)
        report = response.context["report"]
        self.assertEqual(report["total_marked"], 3)
        self.assertContains(response, "VI-A")
        self.assertNotContains(response, "VI-B")
        self.assertNotContains(response, "Aravali")
        self.assertNotContains(response, "Nilgiri")

    def test_staff_cannot_see_another_teachers_attendance_in_totals(self):
        self.client.force_login(self.staff)
        report = self.client.get(self.url, self.range_query).context["report"]
        self.assertEqual(report["status_counts"][AttendanceStatus.LATE], 0)
        self.assertEqual(report["status_counts"][AttendanceStatus.LEAVE], 1)
        self.assertEqual(report["status_counts"][AttendanceStatus.PRESENT], 1)

    def test_staff_html_contains_no_unauthorized_student_identities(self):
        self.client.force_login(self.staff)
        html = self.client.get(self.url, self.range_query).content.decode()
        self.assertNotIn("Cara", html)
        self.assertNotIn("SB1", html)
        self.assertNotIn("Ada", html)
        self.assertNotIn("SA1", html)
        self.assertNotIn("Ora", html)
        self.assertNotIn("SX1", html)
        self.assertNotIn("Ben", html)

    def test_staff_with_no_authorized_sessions_gets_empty_report(self):
        self.client.force_login(self.lonely_staff)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No authorized attendance-capable sessions in this range.",
        )
        report = response.context["report"]
        self.assertEqual(report["session_count"], 0)
        self.assertEqual(report["total_marked"], 0)
        self.assertEqual(report["class_rows"], [])
        self.assertEqual(report["house_rows"], [])
        self.assertIsNone(report["percentage"])
        self.assertContains(response, "N/A")
        self.assertNotContains(response, "Ada")
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "VI-A")
        self.assertNotContains(response, "Aravali")

    def test_unique_mod_sees_sessions_on_mod_date(self):
        self.client.force_login(self.mod)
        report = self.client.get(self.url, self.range_query).context["report"]
        self.assertEqual(report["total_marked"], 8)
        self.assertTrue(any(row["label"] == "VI-B" for row in report["class_rows"]))

    def test_house_master_sees_assigned_house_sessions(self):
        self.client.force_login(self.hm)
        response = self.client.get(self.url, self.range_query)
        report = response.context["report"]
        self.assertEqual(report["total_marked"], 1)
        self.assertEqual(report["status_counts"][AttendanceStatus.PRESENT], 1)
        self.assertContains(response, "Aravali")
        self.assertNotContains(response, "VI-A")
        self.assertNotContains(response, "Nilgiri")

    def test_parent_gets_403(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.url, self.range_query).status_code, 403)

    def test_inactive_user_is_blocked(self):
        self.client.force_login(self.inactive)
        self.assertIn(
            self.client.get(self.url, self.range_query).status_code,
            (302, 403),
        )

    def test_current_academic_year_is_the_default(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_year"], self.year)
        self.assertEqual(response.context["date_from"], self.year.start_date)
        self.assertEqual(response.context["date_to"], self.year.end_date)

    def test_latest_start_date_is_fallback_without_current_year(self):
        self.year.is_current = False
        self.year.save()
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_year"], self.year)

    def test_date_filtering_excludes_outside_range(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.context["report"]["status_counts"][AttendanceStatus.LEAVE], 1)
        old = self.client.get(
            self.url,
            {
                "academic_year": self.other_year.pk,
                "date_from": self.other_year.start_date.isoformat(),
                "date_to": self.other_year.end_date.isoformat(),
            },
        )
        self.assertEqual(old.context["report"]["status_counts"][AttendanceStatus.LEAVE], 1)
        self.assertEqual(old.context["report"]["total_marked"], 1)

    def test_invalid_academic_year_is_empty(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"academic_year": "abc"})
        self.assertContains(response, "Enter a valid academic year.")
        self.assertEqual(response.context["report"]["total_marked"], 0)
        self.assertEqual(response.context["report"]["class_rows"], [])

    def test_invalid_dates_are_empty(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": "not-a-date",
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "Enter a valid date.")
        self.assertEqual(response.context["report"]["total_marked"], 0)

    def test_inverted_date_range_is_empty(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.later_day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(response, "start date must be on or before")
        self.assertEqual(response.context["report"]["total_marked"], 0)
        self.assertContains(response, "N/A")

    def test_present_rate_is_pooled_not_averaged(self):
        report = build_school_attendance_report(
            [self.class_session, self.other_class_session]
        )
        self.assertEqual(report["status_counts"][AttendanceStatus.PRESENT], 1)
        self.assertEqual(report["total_marked"], 3)
        self.assertEqual(report["percentage"], 100.0 / 3)
        class_rates = [row["percentage"] for row in report["class_rows"]]
        self.assertNotEqual(report["percentage"], sum(class_rates) / len(class_rates))
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "37.5%")

    def test_zero_marked_percentage_is_na(self):
        AttendanceEntry.objects.filter(activity_session=self.remedial_session).delete()
        empty = build_school_attendance_report([self.remedial_session])
        self.assertEqual(empty["total_marked"], 0)
        self.assertIsNone(empty["percentage"])

    def test_unmarked_is_not_absent(self):
        report = build_school_attendance_report([self.class_session])
        self.assertEqual(report["status_counts"][AttendanceStatus.ABSENT], 0)
        self.assertEqual(report["eligible_student_sessions"], 2)
        self.assertEqual(report["marked_on_roster"], 1)
        self.assertEqual(report["orphan_marks"], 1)

    def test_all_audience_kinds_contribute_to_school_totals(self):
        report = build_school_attendance_report(
            [
                self.class_session,
                self.house_session,
                self.school_session,
                self.group_session,
                self.selected_session,
            ]
        )
        self.assertEqual(report["total_marked"], 6)
        kinds = {row["label"] for row in report["audience_kind_rows"]}
        self.assertEqual(
            kinds,
            {
                "Class section",
                "House",
                "Whole school",
                "Named student group",
                "Selected students",
            },
        )

    def test_non_attendance_sessions_are_excluded_and_period_remedial_included(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        self.assertNotContains(response, "Silent assembly")
        self.assertContains(response, "Period")
        self.assertContains(response, "Remedial")
        report = response.context["report"]
        self.assertEqual(report["total_marked"], 3)

    def test_class_breakdown_contains_only_class_sessions(self):
        report = build_school_attendance_report(
            [self.class_session, self.house_session, self.school_session]
        )
        class_row = report["class_rows"][0]
        self.assertEqual(class_row["total_marked"], 2)
        self.assertEqual(class_row["status_counts"][AttendanceStatus.PRESENT], 1)
        self.assertEqual(class_row["status_counts"][AttendanceStatus.LEAVE], 1)
        self.assertFalse(any(row["label"] == "Aravali" for row in report["class_rows"]))

    def test_house_breakdown_contains_only_house_sessions(self):
        report = build_school_attendance_report(
            [self.class_session, self.house_session]
        )
        house_row = report["house_rows"][0]
        self.assertEqual(house_row["label"], "Aravali (AR)")
        self.assertEqual(house_row["total_marked"], 1)
        self.assertEqual(house_row["status_counts"][AttendanceStatus.PRESENT], 1)

    def test_orphan_marks_count_without_exposing_identity(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        report = response.context["report"]
        self.assertEqual(report["orphan_marks"], 1)
        self.assertEqual(report["status_counts"][AttendanceStatus.LEAVE], 1)
        self.assertNotContains(response, "Ora")
        self.assertNotContains(response, "SX1")

    def test_class_and_house_report_links_use_same_filters(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, self.range_query)
        class_url = reverse(
            "admin:school_classsection_attendance_report",
            args=[self.section.pk],
        )
        house_url = reverse(
            "admin:school_house_attendance_report",
            args=[self.house.pk],
        )
        self.assertContains(response, f"{class_url}?academic_year={self.year.pk}")
        self.assertContains(response, f"date_from={self.day.isoformat()}")
        self.assertContains(response, f"{house_url}?academic_year={self.year.pk}")

    def test_get_and_post_do_not_write_or_generate(self):
        self.client.force_login(self.admin_user)
        before_sessions = ActivitySession.objects.count()
        before_entries = AttendanceEntry.objects.count()
        before_days = SchoolCalendarDay.objects.count()
        self.assertEqual(self.client.get(self.url, self.range_query).status_code, 200)
        post = self.client.post(self.url, self.range_query)
        self.assertEqual(post.status_code, 405)
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(AttendanceEntry.objects.count(), before_entries)
        self.assertEqual(SchoolCalendarDay.objects.count(), before_days)

    def test_missing_calendar_day_does_not_generate(self):
        before_sessions = ActivitySession.objects.count()
        before_days = SchoolCalendarDay.objects.count()
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(SchoolCalendarDay.objects.count(), before_days)
        self.assertFalse(SchoolCalendarDay.objects.filter(date=self.day).exists())

    def test_academic_year_admin_link_includes_year(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:school_academicyear_changelist"))
        self.assertContains(response, "School attendance report")
        self.assertContains(response, f"{self.url}?academic_year={self.year.pk}")

    def test_activity_session_admin_link_works(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:school_activitysession_changelist"))
        self.assertContains(response, "School attendance report")
        self.assertContains(response, self.url)

    def test_daily_overview_link_uses_selected_date(self):
        SchoolCalendarDay.objects.create(
            date=self.day,
            academic_year=self.year,
            routine=Routine.objects.create(
                academic_year=self.year,
                name="Regular",
                is_active=True,
            ),
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.overview_url, {"date": self.day.isoformat()}
        )
        self.assertContains(response, "School attendance report")
        self.assertContains(response, f"date_from={self.day.isoformat()}")
        self.assertContains(response, f"date_to={self.day.isoformat()}")
        self.assertContains(response, f"academic_year={self.year.pk}")


class AttendanceCoverageTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.admin_user = User.objects.create_user(
            username="cov-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="cov-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="cov-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.lonely_staff = User.objects.create_user(
            username="cov-lonely",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.hm = User.objects.create_user(
            username="cov-hm",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.mod = User.objects.create_user(
            username="cov-mod",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="cov-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="cov-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.period = ActivityType.objects.create(name="Period", takes_attendance=True)
        self.no_att = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="CA1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="CA2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="CB1",
            roll_number=1,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.orphan = Student.objects.create(
            admission_number="CX1",
            roll_number=9,
            first_name="Ora",
            last_name="O",
            date_of_birth=date(2014, 1, 4),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student_a,
            house=self.house,
            academic_year=self.year,
        )
        HouseMasterAssignment.objects.create(
            staff=self.hm,
            house=self.house,
            academic_year=self.year,
        )
        StaffDutyAssignment.objects.create(
            duty_type=DutyType.objects.create(
                name="MOD",
                unique_per_day=True,
                is_active=True,
            ),
            staff=self.mod,
            date=date(2026, 8, 28),
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.later_day = date(2026, 8, 29)
        self.group = StudentGroup.objects.create(name="Band", academic_year=self.year)
        self.partial_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.not_started_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Remedial VI-A",
            start_time=time(14, 0),
            end_time=time(14, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.empty_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Selected empty",
            start_time=time(15, 0),
            end_time=time(15, 20),
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            responsible_staff=self.staff,
        )
        self.complete_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Selected complete",
            start_time=time(16, 0),
            end_time=time(16, 20),
            audience_kind=AudienceKind.SELECTED_STUDENTS,
            responsible_staff=self.staff,
        )
        ActivitySessionParticipant.objects.create(
            session=self.complete_session,
            student=self.student_a,
        )
        self.other_class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Aravali house roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.other_staff,
        )
        self.school_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Morning school",
            start_time=time(6, 30),
            end_time=time(6, 50),
            audience_kind=AudienceKind.SCHOOL,
            responsible_staff=self.other_staff,
        )
        self.group_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Band practice",
            start_time=time(17, 0),
            end_time=time(17, 40),
            audience_kind=AudienceKind.STUDENT_GROUP,
            student_group=self.group,
            responsible_staff=self.other_staff,
        )
        ActivitySessionParticipant.objects.create(
            session=self.group_session,
            student=self.student_a,
        )
        self.no_att_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.no_att,
            name="Silent assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.old_session = ActivitySession.objects.create(
            date=date(2025, 8, 28),
            academic_year=self.other_year,
            activity_type=self.period,
            name="Old year period",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.partial_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.partial_session,
            student=self.orphan,
            status=AttendanceStatus.LEAVE,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.complete_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self.url = reverse("admin:school_activitysession_attendance_coverage")
        self.overview_url = reverse(
            "admin:school_activitysession_attendance_overview"
        )
        self.school_url = reverse(
            "admin:school_activitysession_school_attendance_report"
        )
        self.range_query = {
            "academic_year": self.year.pk,
            "date_from": self.day.isoformat(),
            "date_to": self.day.isoformat(),
        }

    def test_administration_sees_authorized_sessions(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Period 3 VI-B")
        self.assertContains(response, "Aravali house roll")
        self.assertContains(response, "Morning school")
        self.assertContains(response, "Band practice")
        self.assertNotContains(response, "Silent assembly")
        self.assertEqual(response.context["summary"]["total"], 8)

    def test_staff_sees_only_sessions_they_may_mark(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Remedial VI-A")
        self.assertNotContains(response, "Period 3 VI-B")
        self.assertNotContains(response, "Aravali house roll")
        self.assertNotContains(response, "Morning school")

    def test_unique_mod_sees_sessions_on_mod_date(self):
        self.client.force_login(self.mod)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "Period 3 VI-B")
        self.assertContains(response, "Morning school")

    def test_house_master_sees_assigned_house_sessions(self):
        self.client.force_login(self.hm)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "Aravali house roll")
        self.assertNotContains(response, "Period 3 VI-A")
        self.assertNotContains(response, "Period 3 VI-B")

    def test_staff_with_no_authorized_sessions_is_empty(self):
        self.client.force_login(self.lonely_staff)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(
            response,
            "No authorized attendance-capable sessions in this range.",
        )
        self.assertEqual(response.context["summary"]["total"], 0)
        self.assertEqual(response.context["rows"], [])
        self.assertNotContains(response, "Ada")
        self.assertNotContains(response, "CA1")

    def test_parent_and_inactive_are_blocked(self):
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.url, self.range_query).status_code, 403)
        self.client.force_login(self.inactive)
        self.assertIn(
            self.client.get(self.url, self.range_query).status_code,
            (302, 403),
        )

    def test_default_and_fallback_academic_year(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_year"], self.year)
        self.assertEqual(response.context["date_from"], self.year.start_date)
        self.assertEqual(response.context["date_to"], self.year.end_date)
        self.year.is_current = False
        self.year.save()
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_year"], self.year)

    def test_date_range_and_invalid_filters(self):
        self.client.force_login(self.admin_user)
        self.assertNotContains(
            self.client.get(self.url, self.range_query),
            "Old year period",
        )
        invalid_year = self.client.get(self.url, {"academic_year": "abc"})
        self.assertContains(invalid_year, "Enter a valid academic year.")
        self.assertEqual(invalid_year.context["rows"], [])
        invalid_date = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": "not-a-date",
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(invalid_date, "Enter a valid date.")
        self.assertEqual(invalid_date.context["rows"], [])
        inverted = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.later_day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(inverted, "start date must be on or before")
        self.assertEqual(inverted.context["rows"], [])
        other = self.client.get(
            self.url,
            {
                "academic_year": self.other_year.pk,
                "date_from": self.other_year.start_date.isoformat(),
                "date_to": self.other_year.end_date.isoformat(),
            },
        )
        self.assertContains(other, "Old year period")
        self.assertNotContains(other, "Period 3 VI-A")

    def test_completion_statuses_and_default_hides_complete(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        by_name = {
            row["session"].name: row["status"] for row in response.context["rows"]
        }
        self.assertEqual(by_name["Period 3 VI-A"], "Partial")
        self.assertEqual(by_name["Remedial VI-A"], "Not started")
        self.assertEqual(by_name["Selected empty"], "Empty roster")
        self.assertNotIn("Selected complete", by_name)
        self.assertEqual(response.context["summary"]["complete"], 1)
        self.assertEqual(response.context["summary"]["partial"], 1)
        self.assertEqual(response.context["summary"]["not_started"], 1)
        self.assertEqual(response.context["summary"]["empty_roster"], 1)

        complete = self.client.get(
            self.url,
            {**self.range_query, "status": "complete"},
        )
        names = [row["session"].name for row in complete.context["rows"]]
        self.assertEqual(names, ["Selected complete"])

        multi = self.client.get(
            self.url,
            {**self.range_query, "status": ["partial", "complete"]},
        )
        names = [row["session"].name for row in multi.context["rows"]]
        self.assertEqual(names, ["Period 3 VI-A", "Selected complete"])

    def test_orphan_does_not_complete_and_unmarked_is_not_absent(self):
        self.client.force_login(self.staff)
        row = [
            item
            for item in self.client.get(self.url, self.range_query).context["rows"]
            if item["session"] == self.partial_session
        ][0]
        self.assertEqual(row["roster_count"], 2)
        self.assertEqual(row["marked_count"], 1)
        self.assertEqual(row["status"], "Partial")
        html = self.client.get(self.url, self.range_query).content.decode()
        self.assertNotIn("Absent", html)

    def test_all_audience_kinds_included(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            self.url,
            {**self.range_query, "status": ["not_started", "partial", "empty_roster", "complete"]},
        )
        html = response.content.decode()
        self.assertIn("Period 3 VI-A", html)
        self.assertIn("Aravali house roll", html)
        self.assertIn("Morning school", html)
        self.assertIn("Band practice", html)
        self.assertIn("Selected empty", html)
        self.assertIn("Selected complete", html)
        self.assertNotIn("Silent assembly", html)

    def test_privacy_no_student_identities(self):
        self.client.force_login(self.staff)
        html = self.client.get(self.url, self.range_query).content.decode()
        for needle in ("Ada", "Ben", "Cara", "Ora", "CA1", "CA2", "CB1", "CX1"):
            self.assertNotIn(needle, html)

    def test_get_and_post_do_not_write_or_generate(self):
        self.client.force_login(self.admin_user)
        before_sessions = ActivitySession.objects.count()
        before_entries = AttendanceEntry.objects.count()
        before_revisions = AttendanceRevision.objects.count()
        before_days = SchoolCalendarDay.objects.count()
        before_statuses = list(
            AttendanceEntry.objects.order_by("pk").values_list("pk", "status")
        )
        self.assertEqual(self.client.get(self.url, self.range_query).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.range_query).status_code, 405)
        self.assertEqual(ActivitySession.objects.count(), before_sessions)
        self.assertEqual(AttendanceEntry.objects.count(), before_entries)
        self.assertEqual(AttendanceRevision.objects.count(), before_revisions)
        self.assertEqual(SchoolCalendarDay.objects.count(), before_days)
        self.assertEqual(
            list(AttendanceEntry.objects.order_by("pk").values_list("pk", "status")),
            before_statuses,
        )
        self.assertFalse(SchoolCalendarDay.objects.filter(date=self.day).exists())
        self.assertContains(
            self.client.get(self.url, self.range_query),
            "Period 3 VI-A",
        )

    def test_entry_point_links(self):
        self.client.force_login(self.admin_user)
        changelist = self.client.get(
            reverse("admin:school_activitysession_changelist")
        )
        self.assertContains(changelist, "Attendance coverage")
        self.assertContains(changelist, self.url)
        year_list = self.client.get(reverse("admin:school_academicyear_changelist"))
        self.assertContains(year_list, f"{self.url}?academic_year={self.year.pk}")
        SchoolCalendarDay.objects.create(
            date=self.day,
            academic_year=self.year,
            routine=Routine.objects.create(
                academic_year=self.year,
                name="Regular",
                is_active=True,
            ),
        )
        overview = self.client.get(
            self.overview_url, {"date": self.day.isoformat()}
        )
        self.assertContains(overview, "Attendance coverage")
        self.assertContains(overview, f"date_from={self.day.isoformat()}")
        self.assertContains(overview, f"date_to={self.day.isoformat()}")
        self.assertContains(overview, f"academic_year={self.year.pk}")
        school = self.client.get(self.school_url, self.range_query)
        self.assertContains(school, "Attendance coverage")
        self.assertContains(school, f"{self.url}?academic_year={self.year.pk}")
        self.assertContains(school, f"date_from={self.day.isoformat()}")


class AttendanceCorrectionAuditTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2025-26",
            start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.admin_user = User.objects.create_user(
            username="aud-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="aud-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="aud-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.lonely_staff = User.objects.create_user(
            username="aud-lonely",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.hm = User.objects.create_user(
            username="aud-hm",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.mod = User.objects.create_user(
            username="aud-mod",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="aud-parent",
            password="x",
            category=UserCategory.PARENT,
            is_staff=True,
        )
        self.inactive = User.objects.create_user(
            username="aud-inactive",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
            is_active=False,
        )
        self.period = ActivityType.objects.create(name="Period", takes_attendance=True)
        self.student_a = Student.objects.create(
            admission_number="AU1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="AU2",
            roll_number=1,
            first_name="Cara",
            last_name="C",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.other_section,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student_a,
            house=self.house,
            academic_year=self.year,
        )
        HouseMasterAssignment.objects.create(
            staff=self.hm,
            house=self.house,
            academic_year=self.year,
        )
        StaffDutyAssignment.objects.create(
            duty_type=DutyType.objects.create(
                name="MOD",
                unique_per_day=True,
                is_active=True,
            ),
            staff=self.mod,
            date=date(2026, 8, 28),
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.later_day = date(2026, 8, 29)
        self.class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.other_class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.other_section,
            responsible_staff=self.other_staff,
        )
        self.house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Aravali house roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.other_staff,
        )
        self.old_session = ActivitySession.objects.create(
            date=date(2025, 8, 28),
            academic_year=self.other_year,
            activity_type=self.period,
            name="Old year period",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.staff_entry = AttendanceEntry.objects.create(
            activity_session=self.class_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self._revise(
            self.staff_entry,
            AttendanceStatus.ABSENT,
            self.staff,
            "first correction",
        )
        self._revise(
            self.staff_entry,
            AttendanceStatus.LATE,
            self.admin_user,
            "",
        )
        self._revise(
            self.staff_entry,
            AttendanceStatus.PRESENT,
            self.staff,
            "back to present",
        )
        other_entry = AttendanceEntry.objects.create(
            activity_session=self.other_class_session,
            student=self.other_student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.other_staff,
        )
        self._revise(other_entry, AttendanceStatus.LEAVE, self.other_staff, "other class")
        house_entry = AttendanceEntry.objects.create(
            activity_session=self.house_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.other_staff,
        )
        self._revise(house_entry, AttendanceStatus.ABSENT, self.other_staff, "house fix")
        old_entry = AttendanceEntry.objects.create(
            activity_session=self.old_session,
            student=self.student_a,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self._revise(old_entry, AttendanceStatus.LEAVE, self.staff, "old year")
        self.url = reverse(
            "admin:school_activitysession_attendance_correction_audit"
        )
        self.range_query = {
            "academic_year": self.year.pk,
            "date_from": self.day.isoformat(),
            "date_to": self.day.isoformat(),
        }

    def _revise(self, entry, new_status, changed_by, reason=""):
        entry.status = new_status
        entry.updated_by = changed_by
        entry._status_change_reason = reason
        entry.save()
        return AttendanceRevision.objects.order_by("pk").last()

    def test_administration_sees_authorized_revisions(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result_count"], 5)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Period 3 VI-B")
        self.assertContains(response, "Aravali house roll")
        self.assertContains(response, "Cara")
        self.assertNotContains(response, "Old year period")

    def test_staff_sees_only_authorized_revisions(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, self.range_query)
        self.assertEqual(response.context["result_count"], 3)
        self.assertContains(response, "Period 3 VI-A")
        self.assertContains(response, "Ada")
        self.assertNotContains(response, "Period 3 VI-B")
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "AU2")
        self.assertNotContains(response, "Aravali house roll")

    def test_unique_mod_and_house_master(self):
        self.client.force_login(self.mod)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "Period 3 VI-B")
        self.assertContains(response, "Cara")
        self.client.force_login(self.hm)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "Aravali house roll")
        self.assertNotContains(response, "Period 3 VI-B")
        self.assertNotContains(response, "Cara")

    def test_lonely_staff_parent_inactive(self):
        self.client.force_login(self.lonely_staff)
        response = self.client.get(self.url, self.range_query)
        self.assertContains(response, "No attendance corrections found in this range.")
        self.assertEqual(response.context["result_count"], 0)
        self.assertNotContains(response, "Ada")
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "AU1")
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(self.url, self.range_query).status_code, 403)
        self.client.force_login(self.inactive)
        self.assertIn(
            self.client.get(self.url, self.range_query).status_code,
            (302, 403),
        )

    def test_year_and_date_filters(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_year"], self.year)
        self.assertEqual(response.context["date_from"], self.year.start_date)
        self.year.is_current = False
        self.year.save()
        response = self.client.get(self.url)
        self.assertEqual(response.context["selected_year"], self.year)
        other = self.client.get(
            self.url,
            {
                "academic_year": self.other_year.pk,
                "date_from": self.other_year.start_date.isoformat(),
                "date_to": self.other_year.end_date.isoformat(),
            },
        )
        self.assertContains(other, "Old year period")
        self.assertContains(other, "old year")
        self.assertNotContains(other, "Period 3 VI-A")
        invalid_year = self.client.get(self.url, {"academic_year": "abc"})
        self.assertContains(invalid_year, "Enter a valid academic year.")
        self.assertEqual(invalid_year.context["rows"], [])
        invalid_date = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": "bad",
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(invalid_date, "Enter a valid date.")
        self.assertEqual(invalid_date.context["rows"], [])
        inverted = self.client.get(
            self.url,
            {
                "academic_year": self.year.pk,
                "date_from": self.later_day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertContains(inverted, "start date must be on or before")
        self.assertEqual(inverted.context["rows"], [])

    def test_session_changed_by_student_status_filters(self):
        self.client.force_login(self.admin_user)
        by_session = self.client.get(
            self.url,
            {**self.range_query, "session": self.other_class_session.pk},
        )
        self.assertEqual(by_session.context["result_count"], 1)
        self.assertContains(by_session, "Cara")
        self.assertEqual(
            [row["session"].name for row in by_session.context["rows"]],
            ["Period 3 VI-B"],
        )
        by_changer = self.client.get(
            self.url,
            {**self.range_query, "changed_by": self.admin_user.pk},
        )
        self.assertEqual(by_changer.context["result_count"], 1)
        self.assertContains(by_changer, "aud-admin")
        by_student = self.client.get(
            self.url,
            {**self.range_query, "student": self.other_student.pk},
        )
        self.assertEqual(by_student.context["result_count"], 1)
        self.assertContains(by_student, "AU2")
        by_status = self.client.get(
            self.url,
            {**self.range_query, "status": AttendanceStatus.LEAVE},
        )
        self.assertEqual(by_status.context["result_count"], 1)
        self.assertContains(by_status, "Leave")
        combined = self.client.get(
            self.url,
            {
                **self.range_query,
                "session": self.class_session.pk,
                "student": self.student_a.pk,
                "changed_by": self.staff.pk,
                "status": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(combined.context["result_count"], 1)
        self.assertContains(combined, "back to present")

    def test_staff_cannot_discover_unauthorized_students_via_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {**self.range_query, "student": self.other_student.pk},
        )
        self.assertEqual(response.context["result_count"], 0)
        self.assertNotContains(response, "Cara")
        self.assertNotContains(response, "AU2")
        option_ids = [student.pk for student in response.context["student_options"]]
        self.assertNotIn(self.other_student.pk, option_ids)
        session_ids = [session.pk for session in response.context["session_options"]]
        self.assertNotIn(self.other_class_session.pk, session_ids)

    def test_complete_revision_history_order_and_reason(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {**self.range_query, "session": self.class_session.pk},
        )
        rows = response.context["rows"]
        self.assertEqual(len(rows), 3)
        statuses = [
            (row["revision"].old_status, row["revision"].new_status)
            for row in rows
        ]
        self.assertEqual(
            statuses,
            [
                (AttendanceStatus.LATE, AttendanceStatus.PRESENT),
                (AttendanceStatus.ABSENT, AttendanceStatus.LATE),
                (AttendanceStatus.PRESENT, AttendanceStatus.ABSENT),
            ],
        )
        self.assertEqual(rows[0]["revision"].reason, "back to present")
        self.assertEqual(rows[1]["revision"].reason, "")
        self.assertContains(response, "—")
        self.assertContains(response, "first correction")
        self.assertContains(response, "aud-staff")
        self.assertContains(response, "aud-admin")

    def test_get_and_post_do_not_write(self):
        self.client.force_login(self.admin_user)
        before = (
            ActivitySession.objects.count(),
            AttendanceEntry.objects.count(),
            AttendanceRevision.objects.count(),
            SchoolCalendarDay.objects.count(),
        )
        self.assertEqual(self.client.get(self.url, self.range_query).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.range_query).status_code, 405)
        self.assertEqual(
            (
                ActivitySession.objects.count(),
                AttendanceEntry.objects.count(),
                AttendanceRevision.objects.count(),
                SchoolCalendarDay.objects.count(),
            ),
            before,
        )

    def test_pagination_preserves_filters(self):
        entry = self.staff_entry
        for index in range(48):
            new_status = (
                AttendanceStatus.ABSENT
                if entry.status == AttendanceStatus.PRESENT
                else AttendanceStatus.PRESENT
            )
            self._revise(entry, new_status, self.staff, f"page-{index}")
        self.client.force_login(self.staff)
        query = {**self.range_query, "session": self.class_session.pk}
        page1 = self.client.get(self.url, query)
        self.assertEqual(page1.context["result_count"], 51)
        self.assertEqual(len(page1.context["rows"]), 50)
        self.assertContains(page1, "page=2")
        self.assertContains(page1, f"academic_year={self.year.pk}")
        page2 = self.client.get(self.url, {**query, "page": 2})
        self.assertEqual(len(page2.context["rows"]), 1)
        self.assertEqual(page2.context["page_obj"].number, 2)

    def test_entry_point_links(self):
        self.client.force_login(self.admin_user)
        changelist = self.client.get(
            reverse("admin:school_activitysession_changelist")
        )
        self.assertContains(changelist, "Correction audit")
        self.assertContains(changelist, self.url)
        coverage = self.client.get(
            reverse("admin:school_activitysession_attendance_coverage"),
            self.range_query,
        )
        self.assertContains(coverage, "Correction audit")
        self.assertContains(coverage, f"{self.url}?academic_year={self.year.pk}")
        school = self.client.get(
            reverse("admin:school_activitysession_school_attendance_report"),
            self.range_query,
        )
        self.assertContains(school, "Correction audit")
        self.assertContains(school, f"date_from={self.day.isoformat()}")
        SchoolCalendarDay.objects.create(
            date=self.day,
            academic_year=self.year,
            routine=Routine.objects.create(
                academic_year=self.year,
                name="Regular",
                is_active=True,
            ),
        )
        overview = self.client.get(
            reverse("admin:school_activitysession_attendance_overview"),
            {"date": self.day.isoformat()},
        )
        self.assertContains(overview, "Correction audit")
        self.assertContains(overview, f"date_from={self.day.isoformat()}")
        history = self.client.get(
            reverse(
                "admin:school_student_attendance_history",
                args=[self.student_a.pk],
            )
        )
        self.assertContains(history, "Correction audit")
        self.assertContains(history, f"student={self.student_a.pk}")


class StudentClassMembershipTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.next_year = AcademicYear.objects.create(
            name="2027-28",
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31),
        )
        self.vi_a = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.vi_b = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.vii_a = ClassSection.objects.create(
            grade_name="VII",
            section_name="A",
            display_name="VII-A",
        )
        self.staff = User.objects.create_user(
            username="class-mem-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.admin_user = User.objects.create_user(
            username="class-mem-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.period = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
            default_audience_kind=AudienceKind.CLASS,
        )
        self.student = Student.objects.create(
            admission_number="CM1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        self.day = date(2026, 8, 28)
        self.class_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.vi_a,
            responsible_staff=self.staff,
        )
        self.unmarked_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 4 VI-A",
            start_time=time(9, 40),
            end_time=time(10, 20),
            audience_kind=AudienceKind.CLASS,
            class_section=self.vi_a,
            responsible_staff=self.staff,
        )
        self.school_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="School assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            audience_kind=AudienceKind.SCHOOL,
            class_section=None,
            responsible_staff=self.staff,
        )

    def test_membership_created_and_unique_per_year(self):
        memberships = list(self.student.class_memberships.order_by("pk"))
        self.assertEqual(len(memberships), 1)
        self.assertEqual(memberships[0].class_section, self.vi_a)
        self.assertEqual(memberships[0].academic_year, self.year)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StudentClassMembership.objects.create(
                    student=self.student,
                    class_section=self.vi_b,
                    academic_year=self.year,
                )

    def test_multiple_years_and_roster_index_lookup(self):
        self.student.class_section = self.vii_a
        self.student.academic_year = self.next_year
        self.student.save()
        years = {
            row.academic_year_id: row.class_section_id
            for row in self.student.class_memberships.all()
        }
        self.assertEqual(years[self.year.pk], self.vi_a.pk)
        self.assertEqual(years[self.next_year.pk], self.vii_a.pk)
        found = StudentClassMembership.objects.filter(
            academic_year=self.year,
            class_section=self.vi_a,
        )
        self.assertEqual(found.get().student, self.student)
        index_fields = [
            tuple(index.fields)
            for index in StudentClassMembership._meta.indexes
        ]
        self.assertIn(("academic_year", "class_section"), index_fields)

    def test_backfill_is_safe_and_does_not_duplicate(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module(
            "school.migrations.0006_studentclassmembership"
        )
        StudentClassMembership.objects.all().delete()
        self.assertEqual(StudentClassMembership.objects.count(), 0)
        migration.backfill_student_class_memberships(apps, None)
        self.assertEqual(StudentClassMembership.objects.count(), 1)
        membership = StudentClassMembership.objects.get()
        self.assertEqual(membership.student, self.student)
        self.assertEqual(membership.class_section, self.vi_a)
        self.assertEqual(membership.academic_year, self.year)
        migration.backfill_student_class_memberships(apps, None)
        self.assertEqual(StudentClassMembership.objects.count(), 1)

    def test_promotion_keeps_historical_roster_reports_and_history(self):
        AttendanceEntry.objects.create(
            activity_session=self.class_session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.school_session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        self.student.class_section = self.vii_a
        self.student.academic_year = self.next_year
        self.student.save()

        old = self.student.class_memberships.get(academic_year=self.year)
        self.assertEqual(old.class_section, self.vi_a)
        new = self.student.class_memberships.get(academic_year=self.next_year)
        self.assertEqual(new.class_section, self.vii_a)

        self.assertEqual(
            list(students_for_session(self.class_session)),
            [self.student],
        )
        self.assertEqual(
            list(students_for_session(self.school_session)),
            [self.student],
        )
        self.assertEqual(
            roster_student_ids_by_session(
                [self.class_session, self.school_session]
            ),
            {
                self.class_session.pk: {self.student.pk},
                self.school_session.pk: {self.student.pk},
            },
        )
        self.assertEqual(
            list(
                orphan_entries_for_session(
                    self.class_session,
                    roster_student_ids_by_session([self.class_session])[
                        self.class_session.pk
                    ],
                )
            ),
            [],
        )

        class_report = build_class_attendance_report(
            self.vi_a,
            self.year,
            [self.class_session],
        )
        self.assertEqual(class_report["roster_size"], 1)
        self.assertEqual(class_report["student_rows"][0]["student"], self.student)
        self.assertTrue(class_report["student_rows"][0]["on_current_roster"])

        school_report = build_school_attendance_report([self.school_session])
        self.assertEqual(school_report["distinct_roster_students"], 1)
        self.assertEqual(school_report["orphan_marks"], 0)

        history = build_student_attendance_history(
            self.student,
            [self.class_session, self.unmarked_session],
        )
        self.assertEqual(history["total_marked"], 1)
        self.assertEqual(history["total_eligible"], 2)
        self.assertFalse(history["marked_rows"][0]["is_orphan"])
        self.assertEqual(history["unmarked_rows"][0]["session"], self.unmarked_session)

        next_session = ActivitySession.objects.create(
            date=date(2027, 8, 28),
            academic_year=self.next_year,
            activity_type=self.period,
            name="Period 3 VII-A",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.vii_a,
            responsible_staff=self.staff,
        )
        self.assertEqual(list(students_for_session(next_session)), [self.student])
        self.assertEqual(list(students_for_session(self.class_session)), [self.student])

    def test_intra_year_section_change_updates_single_membership(self):
        self.student.class_section = self.vi_b
        self.student.save()
        memberships = list(
            self.student.class_memberships.filter(academic_year=self.year)
        )
        self.assertEqual(len(memberships), 1)
        self.assertEqual(memberships[0].class_section, self.vi_b)
        self.assertEqual(list(students_for_session(self.class_session)), [])
        vi_b_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Period 3 VI-B",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.vi_b,
            responsible_staff=self.staff,
        )
        self.assertEqual(list(students_for_session(vi_b_session)), [self.student])

    def test_house_and_group_rosters_unchanged(self):
        house = House.objects.create(name="Aravali", code="AR")
        other = Student.objects.create(
            admission_number="CM2",
            roll_number=2,
            first_name="Bea",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="female",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=other,
            house=house,
            academic_year=self.year,
        )
        house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="House roll",
            start_time=time(7, 0),
            end_time=time(7, 20),
            audience_kind=AudienceKind.HOUSE,
            class_section=None,
            house=house,
            responsible_staff=self.staff,
        )
        self.assertEqual(list(students_for_session(house_session)), [other])
        group = StudentGroup.objects.create(name="Band", academic_year=self.year)
        StudentGroupMembership.objects.create(group=group, student=self.student)
        group_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.period,
            name="Band",
            start_time=time(16, 0),
            end_time=time(17, 0),
            audience_kind=AudienceKind.STUDENT_GROUP,
            class_section=None,
            student_group=group,
            responsible_staff=self.staff,
        )
        ActivitySessionParticipant.objects.create(
            session=group_session,
            student=other,
        )
        self.assertEqual(list(students_for_session(group_session)), [other])

    def test_student_admin_save_syncs_without_deleting_history(self):
        request = RequestFactory().post("/")
        request.user = self.admin_user
        admin_instance = site._registry[Student]
        self.student.class_section = self.vii_a
        self.student.academic_year = self.next_year
        admin_instance.save_model(request, self.student, form=None, change=True)
        self.assertEqual(self.student.class_memberships.count(), 2)
        self.assertEqual(
            self.student.class_memberships.get(academic_year=self.year).class_section,
            self.vi_a,
        )
        self.assertEqual(
            self.student.class_memberships.get(
                academic_year=self.next_year
            ).class_section,
            self.vii_a,
        )
        self.student.class_section = self.vi_b
        self.student.academic_year = self.next_year
        admin_instance.save_model(request, self.student, form=None, change=True)
        self.assertEqual(self.student.class_memberships.count(), 2)
        self.assertEqual(
            self.student.class_memberships.get(
                academic_year=self.next_year
            ).class_section,
            self.vi_b,
        )
        self.client.force_login(self.admin_user)
        changelist = self.client.get(reverse("admin:school_student_changelist"))
        self.assertEqual(changelist.status_code, 200)
        membership_list = self.client.get(
            reverse("admin:school_studentclassmembership_changelist")
        )
        self.assertContains(membership_list, "VI-A")
        self.assertContains(membership_list, "VI-B")
        change = self.client.get(
            reverse("admin:school_student_change", args=[self.student.pk])
        )
        self.assertContains(change, "Class placement history")


