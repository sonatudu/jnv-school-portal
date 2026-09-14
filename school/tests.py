from datetime import date, time

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import UserCategory

from .attendance_auth import can_take_attendance, get_unique_active_mod
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
from .generation import (
    generate_sessions_for_calendar_day,
    generate_sessions_for_date,
    generate_sessions_for_date_range,
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
    ClassGradeStaffAssignment,
    ClassStaffRole,
    ClassTimetableEntry,
    DutyType,
    House,
    HouseMasterAssignment,
    HouseStaffRole,
    Routine,
    RoutineSlot,
    SchoolCalendarDay,
    StaffDutyAssignment,
    Student,
    StudentBiodataRow,
    StudentClassMembership,
    StudentTableColumn,
    StudentTableLayout,
    StudentGroup,
    StudentGroupMembership,
    StudentHouseMembership,
    Subject,
    TeacherProfile,
    TeachingAssignment,
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
        self.house = House.objects.create(name="Aravali")
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
        self.house = House.objects.create(name="Aravali")
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
        self.house = House.objects.create(name="Aravali")
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
        self.house = House.objects.create(name="Aravali")
        self.other_house = House.objects.create(name="Nilgiri")
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
        self.house = House.objects.create(name="Aravali")
        self.other_house = House.objects.create(name="Nilgiri")
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
                "Class",
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
        self.house = House.objects.create(name="Aravali")
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
        self.house = House.objects.create(name="Aravali")
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
        house = House.objects.create(name="Aravali")
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
        self.assertContains(change, 'name="blood_group"')
        self.assertContains(change, '<option value="AB+">AB+</option>')
        self.assertContains(change, '<option value="O-">O-</option>')

    def test_classes_list_in_seniority_order(self):
        self.assertEqual(
            list(ClassSection.objects.values_list("display_name", flat=True)),
            ["VI-A", "VI-B", "VII-A"],
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:school_classsection_changelist"))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(">VI-A</a>", html)
        self.assertIn(">VI-B</a>", html)
        self.assertIn(">VII-A</a>", html)
        self.assertLess(html.find(">VI-A</a>"), html.find(">VI-B</a>"))
        self.assertLess(html.find(">VI-B</a>"), html.find(">VII-A</a>"))
        self.assertIn('class="jnv-opened-name"', html)
        self.assertIn(">Classes</h2>", html)
        self.assertIn('class="jnv-class-list"', html)
        self.assertIn('aria-label="Search classes"', html)
        self.assertNotIn(">Class</th>", html)
        self.assertNotIn("<th>Class</th>", html)
        self.assertNotIn('id="searchbar"', html)
        self.assertNotIn('value="Search"', html)
        filtered = self.client.get(
            reverse("admin:school_classsection_changelist"),
            {"q": "VI-A"},
        )
        self.assertContains(filtered, ">VI-A</a>")
        self.assertNotContains(filtered, ">VII-A</a>")

    def test_class_overview_students_and_optional_assistant(self):
        self.client.force_login(self.admin_user)
        url = reverse(
            "admin:school_classsection_class_overview",
            args=[self.vi_a.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        row_start = html.find('class="jnv-year-row"')
        self.assertNotEqual(row_start, -1)
        nav_start = html.find('class="jnv-section-nav"')
        self.assertNotEqual(nav_start, -1)
        self.assertLess(row_start, nav_start)
        row_html = html[row_start : row_start + 900]
        self.assertIn("VI-A", row_html)
        self.assertIn("Academic year", row_html)
        self.assertLess(row_html.find("VI-A"), row_html.find("Academic year"))
        self.assertContains(response, "Class Teacher")
        self.assertContains(response, "Assistant Class Teacher")
        self.assertContains(response, "Students")
        self.assertContains(response, "Attendance")
        self.assertContains(response, "Routine")
        self.assertNotContains(response, "Father's name")
        self.assertNotContains(response, self.student.full_name)
        students_url = reverse(
            "admin:school_classsection_class_students",
            args=[self.vi_a.pk],
        )
        students_page = self.client.get(students_url)
        self.assertEqual(students_page.status_code, 200)
        self.assertContains(students_page, self.student.full_name)
        self.assertContains(
            students_page,
            reverse("admin:school_student_biodata", args=[self.student.pk]),
        )
        self.assertContains(students_page, "Father's name")
        self.assertContains(students_page, "Date of birth")
        self.assertContains(students_page, "House")
        bio = self.client.get(
            reverse("admin:school_student_biodata", args=[self.student.pk])
        )
        self.assertEqual(bio.status_code, 200)
        self.assertContains(bio, self.student.full_name)
        self.assertContains(bio, "Personal details")
        self.assertContains(bio, "Father's name")
        self.assertContains(response, "Assistant Class Teacher")
        self.assertContains(response, 'id="assistant-class-teacher"')
        self.assertContains(response, 'id="class-teacher-profile"')
        self.assertContains(response, 'aria-label="Teacher profile"')
        self.assertNotContains(response, ">Open</a>")

    def test_class_attendance_lists_students_and_saves_for_a_date(self):
        self.client.force_login(self.admin_user)
        url = reverse(
            "admin:school_classsection_class_attendance",
            args=[self.vi_a.pk],
        )
        response = self.client.get(
            url,
            {"academic_year": self.year.pk, "date": self.day.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        year_row = html.find('class="jnv-year-row"')
        nav = html.find('class="jnv-section-nav"')
        datepicker = html.find("jnv-datepicker", nav)
        date_input = html.find('name="date"', nav)
        self.assertNotEqual(year_row, -1)
        self.assertNotEqual(nav, -1)
        self.assertLess(year_row, nav)
        self.assertNotEqual(datepicker, -1)
        self.assertNotEqual(date_input, -1)
        self.assertGreater(datepicker, nav)
        self.assertGreater(date_input, nav)
        self.assertNotIn(">Date<", html[nav:])
        self.assertNotIn(">Show<", html[nav:])
        self.assertNotContains(
            response,
            "For each period, mark all Present or mark all Absent",
        )
        self.assertContains(response, self.student.full_name)
        self.assertContains(response, self.day.isoformat())
        self.assertContains(response, "Mark all Present")
        self.assertContains(response, "Mark all Absent")
        self.assertContains(response, "Strength")
        self.assertContains(response, ">OD</th>")
        self.assertContains(response, ">Sick</th>")
        self.assertContains(response, ">Leave</th>")
        posted = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "date": self.day.isoformat(),
                f"status_{self.class_session.pk}_{self.student.pk}": AttendanceStatus.ABSENT,
            },
        )
        self.assertEqual(posted.status_code, 302)
        entry = AttendanceEntry.objects.get(
            activity_session=self.class_session,
            student=self.student,
        )
        self.assertEqual(entry.status, AttendanceStatus.ABSENT)
        other = self.client.get(
            url,
            {"academic_year": self.year.pk, "date": date(2026, 9, 10).isoformat()},
        )
        self.assertEqual(other.status_code, 200)

    def test_class_routine_page(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                "admin:school_classsection_class_routine",
                args=[self.vi_a.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Routine")

    def test_class_and_assistant_teacher_must_differ(self):
        staff = User.objects.create_user(
            username="class-teacher-1",
            password="x",
            category=UserCategory.STAFF,
        )
        profile = TeacherProfile.objects.create(user=staff)
        self.client.force_login(self.admin_user)
        url = reverse(
            "admin:school_classsection_class_overview",
            args=[self.vi_a.pk],
        )
        response = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "class_teacher": str(profile.pk),
                "assistant_class_teacher": str(profile.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        assignments = ClassGradeStaffAssignment.objects.filter(
            class_section=self.vi_a,
            academic_year=self.year,
        )
        self.assertEqual(assignments.count(), 1)
        self.assertEqual(assignments.get().role, ClassStaffRole.CLASS_TEACHER)
        self.assertEqual(assignments.get().teacher, profile)

    def test_admin_can_add_extra_student_biodata_row(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin:school_student_biodata", args=[self.student.pk])
        response = self.client.post(
            url,
            {
                "biodata_action": "add",
                "label": "Aadhaar last 4",
                "value": "4321",
            },
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(url)
        self.assertContains(page, "Aadhaar last 4")
        self.assertContains(page, "4321")
        self.assertContains(page, "Add row")
        self.client.force_login(self.staff)
        staff_page = self.client.get(url)
        self.assertContains(staff_page, "Aadhaar last 4")
        self.assertNotContains(staff_page, "Add row")
        blocked = self.client.post(
            url,
            {
                "biodata_action": "add",
                "label": "Secret",
                "value": "no",
            },
        )
        self.assertEqual(blocked.status_code, 403)

    def test_class_students_lists_by_stored_roll_number(self):
        self.student.roll_number = 3
        self.student.save()
        Student.objects.create(
            admission_number="CM-Z",
            roll_number=1,
            first_name="Zara",
            last_name="Z",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        Student.objects.create(
            admission_number="CM-M",
            roll_number=2,
            first_name="Maya",
            last_name="M",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        other = Student.objects.create(
            admission_number="CM-OTHER",
            roll_number=1,
            first_name="Nisha",
            last_name="B",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_b,
            academic_year=self.year,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                "admin:school_classsection_class_students",
                args=[self.vi_a.pk],
            ),
            {"academic_year": self.year.pk},
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertLess(html.find("Zara Z"), html.find("Maya M"))
        self.assertLess(html.find("Maya M"), html.find("Ada A"))
        self.assertNotContains(response, other.full_name)
        self.assertNotContains(response, "jnv-drag-handle")
        self.student.refresh_from_db()
        self.assertEqual(self.student.roll_number, 3)

    def test_student_add_form_has_class_and_roll_no(self):
        self.client.force_login(self.admin_user)
        add_url = reverse("admin:school_student_add")
        page = self.client.get(add_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, ">Class:</label>")
        self.assertContains(page, 'for="id_roll_number">Roll no.</label>')
        self.assertContains(page, 'name="class_section"')
        self.assertContains(page, 'name="roll_number"')
        self.assertContains(page, f'value="{self.year.pk}"')
        self.assertContains(page, "Class and roll")

        posted = self.client.post(
            add_url,
            {
                "admission_number": "CM-NEW",
                "roll_number": "2",
                "first_name": "Zara",
                "last_name": "Z",
                "date_of_birth": "2014-02-02",
                "gender": "female",
                "class_section": str(self.vi_a.pk),
                "academic_year": str(self.year.pk),
                "is_active": "on",
                "class_memberships-TOTAL_FORMS": "0",
                "class_memberships-INITIAL_FORMS": "0",
                "class_memberships-MIN_NUM_FORMS": "0",
                "class_memberships-MAX_NUM_FORMS": "1000",
                "guardian_links-TOTAL_FORMS": "0",
                "guardian_links-INITIAL_FORMS": "0",
                "guardian_links-MIN_NUM_FORMS": "0",
                "guardian_links-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(posted.status_code, 302)
        created = Student.objects.get(admission_number="CM-NEW")
        self.assertEqual(created.class_section, self.vi_a)
        self.assertEqual(created.roll_number, 2)
        self.assertEqual(created.academic_year, self.year)
        students_page = self.client.get(
            reverse(
                "admin:school_classsection_class_students",
                args=[self.vi_a.pk],
            ),
            {"academic_year": self.year.pk},
        )
        html = students_page.content.decode()
        self.assertContains(students_page, "Ada A")
        self.assertContains(students_page, "Zara Z")
        table_at = html.find("<th>Roll</th>")
        self.assertNotEqual(table_at, -1)
        self.assertLess(html.find("Ada A", table_at), html.find("Zara Z", table_at))
        other_class = self.client.get(
            reverse(
                "admin:school_classsection_class_students",
                args=[self.vi_b.pk],
            ),
            {"academic_year": self.year.pk},
        )
        self.assertNotContains(other_class, "Zara Z")

    def test_class_students_search_filters_name_admission_and_roll(self):
        zara = Student.objects.create(
            admission_number="CM-Z99",
            roll_number=8,
            first_name="Zara",
            last_name="Z",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        Student.objects.create(
            admission_number="CM-M",
            roll_number=2,
            first_name="Maya",
            last_name="M",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        other = Student.objects.create(
            admission_number="CM-ZOUT",
            roll_number=8,
            first_name="Zara",
            last_name="Other",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.vi_b,
            academic_year=self.year,
        )
        url = reverse(
            "admin:school_classsection_class_students",
            args=[self.vi_a.pk],
        )
        self.client.force_login(self.admin_user)
        blank = self.client.get(url, {"academic_year": self.year.pk, "q": ""})
        self.assertContains(blank, "Ada A")
        self.assertContains(blank, "Zara Z")
        self.assertContains(blank, "Maya M")
        self.assertContains(blank, 'name="q"')
        self.assertContains(blank, 'aria-label="Search students"')
        self.assertNotContains(blank, ">Search</button>")
        self.assertNotContains(blank, 'class="jnv-class-search is-open"')
        html = blank.content.decode()
        self.assertLess(html.find("Ada A"), html.find("Maya M"))
        self.assertLess(html.find("Maya M"), html.find("Zara Z"))

        by_name = self.client.get(
            url, {"academic_year": self.year.pk, "q": "zara"}
        )
        self.assertContains(by_name, zara.full_name)
        self.assertNotContains(by_name, "Maya M")
        self.assertNotContains(by_name, other.full_name)
        self.assertContains(by_name, 'value="zara"')
        self.assertContains(by_name, 'class="jnv-class-search is-open"')
        self.assertContains(by_name, f'name="academic_year" value="{self.year.pk}"')

        by_admission = self.client.get(
            url, {"academic_year": self.year.pk, "q": "cm-z99"}
        )
        self.assertContains(by_admission, "Zara Z")
        self.assertNotContains(by_admission, "Ada A")

        by_roll = self.client.get(
            url, {"academic_year": self.year.pk, "q": "8"}
        )
        self.assertContains(by_roll, "Zara Z")
        self.assertNotContains(by_roll, "Maya M")
        self.assertNotContains(by_roll, other.full_name)

        missed = self.client.get(
            url, {"academic_year": self.year.pk, "q": "nobody"}
        )
        self.assertContains(missed, "No students match this search.")
        self.assertNotContains(missed, "Ada A")

    def test_admin_can_add_students_table_column(self):
        StudentBiodataRow.objects.create(
            student=self.student,
            label="Aadhaar last 4",
            value="4321",
        )
        url = reverse(
            "admin:school_classsection_class_students",
            args=[self.vi_a.pk],
        )
        self.client.force_login(self.admin_user)
        page = self.client.get(url, {"academic_year": self.year.pk})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'aria-label="Edit table columns"')
        self.assertContains(page, 'aria-label="Search students"')
        self.assertNotContains(page, ">Edit</button>")
        self.assertNotContains(page, "<th>Aadhaar last 4</th>")
        self.assertNotContains(page, "4321")

        posted = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "column_action": "add",
                "label": "Aadhaar last 4",
                "field_type": "text",
            },
        )
        self.assertEqual(posted.status_code, 302)
        column = StudentTableColumn.objects.get(label="Aadhaar last 4")
        self.assertEqual(column.field_type, StudentTableColumn.FieldType.TEXT)

        page = self.client.get(url, {"academic_year": self.year.pk})
        self.assertContains(page, "<th>Aadhaar last 4</th>")
        self.assertContains(page, "4321")
        other_class = self.client.get(
            reverse(
                "admin:school_classsection_class_students",
                args=[self.vi_b.pk],
            ),
            {"academic_year": self.year.pk},
        )
        self.assertContains(other_class, "<th>Aadhaar last 4</th>")

        self.client.force_login(self.staff)
        staff_page = self.client.get(url, {"academic_year": self.year.pk})
        self.assertEqual(staff_page.status_code, 200)
        self.assertContains(staff_page, "<th>Aadhaar last 4</th>")
        self.assertContains(staff_page, "4321")
        self.assertNotContains(staff_page, 'aria-label="Edit table columns"')
        self.assertNotContains(staff_page, "Add column")
        blocked = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "column_action": "add",
                "label": "Secret",
                "field_type": "text",
            },
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertFalse(StudentTableColumn.objects.filter(label="Secret").exists())

    def test_admin_students_table_editor_edits_moves_and_removes(self):
        house = House.objects.create(name="Aravali")
        StudentHouseMembership.objects.create(
            student=self.student,
            house=house,
            academic_year=self.year,
        )
        second = Student.objects.create(
            admission_number="CM-Z",
            roll_number=2,
            first_name="Zara",
            last_name="Z",
            date_of_birth=date(2014, 2, 2),
            gender="female",
            father_name="Old father",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        url = reverse(
            "admin:school_classsection_class_students",
            args=[self.vi_a.pk],
        )
        self.client.force_login(self.admin_user)
        view = self.client.get(url, {"academic_year": self.year.pk})
        self.assertEqual(view.status_code, 200)
        self.assertContains(view, 'aria-label="Edit table columns"')
        self.assertContains(view, 'aria-label="Search students"')
        self.assertLess(
            view.content.find(b'aria-label="Search students"'),
            view.content.find(b'aria-label="Edit table columns"'),
        )
        self.assertNotContains(view, 'aria-label="Done editing table"')
        self.assertNotContains(view, 'aria-label="Undo"')
        self.assertNotContains(view, 'aria-label="Redo"')
        self.assertNotContains(view, 'data-table-pan="left"')
        self.assertNotContains(view, 'data-table-pan="right"')
        self.assertNotContains(view, 'aria-label="Move row up"')
        self.assertNotContains(view, 'aria-label="Move Roll left"')
        self.assertNotContains(view, "jnv-drag-handle")
        self.assertNotContains(view, ">Edit</button>")
        self.assertContains(view, "Father's name")
        self.assertContains(view, "Old father")
        self.assertContains(view, 'class="jnv-sticky-name"')

        edit = self.client.get(url, {"academic_year": self.year.pk, "edit": "1"})
        self.assertEqual(edit.status_code, 200)
        self.assertContains(edit, 'aria-label="Done editing table"')
        self.assertContains(edit, ">Done</a>")
        self.assertContains(edit, 'aria-label="Undo"')
        self.assertContains(edit, 'aria-label="Redo"')
        self.assertContains(edit, 'title="Undo"')
        self.assertContains(edit, 'title="Redo"')
        self.assertNotContains(edit, ">Undo</button>")
        self.assertNotContains(edit, ">Redo</button>")
        self.assertContains(edit, 'class="jnv-edit-tool jnv-edit-tool-icon"')
        self.assertContains(edit, 'data-table-pan="left"')
        self.assertContains(edit, 'data-table-pan="right"')
        self.assertContains(edit, 'aria-label="Scroll table left"')
        self.assertContains(edit, 'aria-label="Scroll table right"')
        self.assertContains(edit, ">Left</button>")
        self.assertContains(edit, ">Right")
        self.assertContains(edit, "jnv-sticky-handle")
        self.assertContains(edit, 'class="jnv-sticky-name"')
        self.assertContains(edit, "Add column")
        self.assertContains(edit, 'aria-label="Move row down"')
        self.assertContains(edit, 'aria-label="Move row up"')
        self.assertContains(edit, 'aria-label="Remove Ada A from class"')
        self.assertContains(edit, 'aria-label="Move Roll right"')
        self.assertContains(edit, 'aria-label="Hide Blood group"')
        self.assertContains(edit, 'class="jnv-select-cell"')
        self.assertContains(edit, 'aria-label="Zara Z blood_group"')
        self.assertContains(edit, '<option value="A+">A+</option>')
        self.assertContains(edit, '<option value="O-">O-</option>')
        self.assertContains(edit, 'disabled aria-label="Undo"')
        self.assertContains(edit, 'disabled aria-label="Redo"')
        self.assertNotContains(edit, "jnv-drag-handle")

        saved = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "father_name",
                "value": "New father",
            },
        )
        self.assertEqual(saved.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.father_name, "New father")

        named = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "full_name",
                "value": "Zara Maya Z",
            },
        )
        self.assertEqual(named.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.first_name, "Zara")
        self.assertEqual(second.middle_name, "Maya")
        self.assertEqual(second.last_name, "Z")

        blood = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "blood_group",
                "value": "B+",
            },
        )
        self.assertEqual(blood.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.blood_group, "B+")
        invalid_blood = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "blood_group",
                "value": "ZZ",
            },
        )
        self.assertEqual(invalid_blood.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.blood_group, "B+")

        housed = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "house",
                "value": str(house.pk),
            },
        )
        self.assertEqual(housed.status_code, 302)
        self.assertEqual(
            StudentHouseMembership.objects.get(
                student=second, academic_year=self.year
            ).house,
            house,
        )

        extra = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "column_action": "add",
                "label": "Aadhaar last 4",
                "field_type": "text",
            },
        )
        self.assertEqual(extra.status_code, 302)
        extra_column = StudentTableColumn.objects.get(label="Aadhaar last 4")
        extra_saved = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(self.student.pk),
                "column_key": f"extra:{extra_column.pk}",
                "value": "4321",
            },
        )
        self.assertEqual(extra_saved.status_code, 302)
        self.assertEqual(
            StudentBiodataRow.objects.get(
                student=self.student, label="Aadhaar last 4"
            ).value,
            "4321",
        )

        moved_row = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "move_row",
                "student_id": str(self.student.pk),
                "direction": "down",
            },
        )
        self.assertEqual(moved_row.status_code, 302)
        self.student.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.student.roll_number, 2)
        self.assertEqual(second.roll_number, 1)

        moved_col = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "move_column",
                "column_key": "full_name",
                "direction": "left",
            },
        )
        self.assertEqual(moved_col.status_code, 302)
        page = self.client.get(url, {"academic_year": self.year.pk})
        html = page.content.decode()
        self.assertLess(html.find(">Name<"), html.find(">Admission No.<"))
        self.assertLess(html.find(">Admission No.<"), html.find(">Father's name<"))

        hidden = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "hide_column",
                "column_key": "blood_group",
            },
        )
        self.assertEqual(hidden.status_code, 302)
        self.assertTrue(
            StudentTableLayout.objects.get(column_key="blood_group").is_hidden
        )
        hidden_page = self.client.get(url, {"academic_year": self.year.pk})
        self.assertNotContains(hidden_page, ">Blood group<")
        self.assertContains(hidden_page, ">Name<")
        edit_hidden = self.client.get(
            url, {"academic_year": self.year.pk, "edit": "1"}
        )
        self.assertContains(edit_hidden, "Show Blood group")

        deleted_extra = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "column_action": "delete",
                "column_id": str(extra_column.pk),
            },
        )
        self.assertEqual(deleted_extra.status_code, 302)
        self.assertFalse(
            StudentTableColumn.objects.filter(label="Aadhaar last 4").exists()
        )
        after_delete = self.client.get(url, {"academic_year": self.year.pk})
        self.assertNotContains(after_delete, "<th>Aadhaar last 4</th>")

        removed = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "delete_row",
                "student_id": str(second.pk),
            },
        )
        self.assertEqual(removed.status_code, 302)
        self.assertTrue(Student.objects.filter(pk=second.pk).exists())
        self.assertFalse(
            StudentClassMembership.objects.filter(
                student=second,
                class_section=self.vi_a,
                academic_year=self.year,
            ).exists()
        )
        gone = self.client.get(url, {"academic_year": self.year.pk})
        self.assertNotContains(gone, "CM-Z")
        self.assertContains(gone, "Ada A")
        self.assertContains(gone, "Removed Zara Maya Z from the class.")

        restored = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "restore_row",
                "student_id": str(second.pk),
            },
        )
        self.assertEqual(restored.status_code, 302)
        self.assertTrue(
            StudentClassMembership.objects.filter(
                student=second,
                class_section=self.vi_a,
                academic_year=self.year,
            ).exists()
        )

        self.client.force_login(self.staff)
        staff_edit = self.client.get(
            url, {"academic_year": self.year.pk, "edit": "1"}
        )
        self.assertNotContains(staff_edit, 'aria-label="Done editing table"')
        self.assertNotContains(staff_edit, 'aria-label="Undo"')
        self.assertNotContains(staff_edit, 'aria-label="Redo"')
        self.assertNotContains(staff_edit, 'data-table-pan="left"')
        self.assertNotContains(staff_edit, 'data-table-pan="right"')
        self.assertNotContains(staff_edit, "Add column")
        self.assertNotContains(staff_edit, 'aria-label="Move row up"')
        blocked = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(self.student.pk),
                "column_key": "father_name",
                "value": "Blocked",
            },
        )
        self.assertEqual(blocked.status_code, 403)
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.father_name, "Blocked")

    def test_students_table_undo_redo_reverts_cell_and_row(self):
        second = Student.objects.create(
            admission_number="CM-UNDO",
            roll_number=2,
            first_name="Zara",
            last_name="Z",
            date_of_birth=date(2014, 2, 2),
            gender="female",
            father_name="Old father",
            blood_group="A+",
            class_section=self.vi_a,
            academic_year=self.year,
        )
        url = reverse(
            "admin:school_classsection_class_students",
            args=[self.vi_a.pk],
        )
        self.client.force_login(self.admin_user)
        saved = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "father_name",
                "value": "New father",
            },
        )
        self.assertEqual(saved.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.father_name, "New father")

        after_edit = self.client.get(
            url, {"academic_year": self.year.pk, "edit": "1"}
        )
        self.assertContains(after_edit, 'aria-label="Undo"')
        self.assertNotContains(after_edit, 'disabled aria-label="Undo"')
        self.assertContains(after_edit, 'disabled aria-label="Redo"')
        self.assertContains(after_edit, 'name="table_action" value="undo"')
        self.assertContains(after_edit, 'name="table_action" value="redo"')

        undone = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "undo",
            },
        )
        self.assertEqual(undone.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.father_name, "Old father")
        after_undo = self.client.get(
            url, {"academic_year": self.year.pk, "edit": "1"}
        )
        self.assertContains(after_undo, "Old father")
        self.assertContains(after_undo, 'disabled aria-label="Undo"')
        self.assertNotContains(after_undo, 'disabled aria-label="Redo"')

        redone = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "redo",
            },
        )
        self.assertEqual(redone.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.father_name, "New father")

        blood = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "save_cell",
                "student_id": str(second.pk),
                "column_key": "blood_group",
                "value": "O-",
            },
        )
        self.assertEqual(blood.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.blood_group, "O-")
        self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "undo",
            },
        )
        second.refresh_from_db()
        self.assertEqual(second.blood_group, "A+")

        moved = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "move_row",
                "student_id": str(self.student.pk),
                "direction": "down",
            },
        )
        self.assertEqual(moved.status_code, 302)
        self.student.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.student.roll_number, 2)
        self.assertEqual(second.roll_number, 1)
        self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "undo",
            },
        )
        self.student.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.student.roll_number, 1)
        self.assertEqual(second.roll_number, 2)

        moved_col = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "move_column",
                "column_key": "full_name",
                "direction": "left",
            },
        )
        self.assertEqual(moved_col.status_code, 302)
        page = self.client.get(url, {"academic_year": self.year.pk})
        html = page.content.decode()
        self.assertLess(html.find(">Name<"), html.find(">Admission No.<"))
        self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "table_action": "undo",
            },
        )
        restored_cols = self.client.get(url, {"academic_year": self.year.pk})
        html = restored_cols.content.decode()
        self.assertLess(html.find(">Admission No.<"), html.find(">Name<"))


class SessionGenerationTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2027-28",
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31),
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
        self.staff = User.objects.create_user(
            username="gen-staff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.admin_user = User.objects.create_user(
            username="gen-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )
        self.teacher = TeacherProfile.objects.create(user=self.staff)
        self.subject = Subject.objects.create(name="Mathematics", code="MA")
        TeachingAssignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            class_section=self.section,
            academic_year=self.year,
        )
        self.class_type = ActivityType.objects.create(
            name="Taught period",
            takes_attendance=True,
            default_audience_kind=AudienceKind.CLASS,
        )
        self.school_type = ActivityType.objects.create(
            name="Assembly",
            takes_attendance=True,
            default_audience_kind=AudienceKind.SCHOOL,
        )
        self.house_type = ActivityType.objects.create(
            name="House roll",
            takes_attendance=True,
            default_audience_kind=AudienceKind.HOUSE,
        )
        self.routine = Routine.objects.create(
            academic_year=self.year,
            name="Weekday",
        )
        self.saturday_routine = Routine.objects.create(
            academic_year=self.year,
            name="Half day",
        )
        self.slot_one = RoutineSlot.objects.create(
            routine=self.routine,
            activity_type=self.class_type,
            name="Alpha block",
            start_time=time(9, 15),
            end_time=time(9, 55),
            sort_order=1,
        )
        self.slot_two = RoutineSlot.objects.create(
            routine=self.routine,
            activity_type=self.class_type,
            name="Beta block",
            start_time=time(10, 5),
            end_time=time(10, 45),
            sort_order=2,
        )
        self.inactive_slot = RoutineSlot.objects.create(
            routine=self.routine,
            activity_type=self.class_type,
            name="Unused block",
            start_time=time(11, 0),
            end_time=time(11, 40),
            sort_order=3,
            is_active=False,
        )
        self.saturday_slot = RoutineSlot.objects.create(
            routine=self.saturday_routine,
            activity_type=self.class_type,
            name="Compact block",
            start_time=time(8, 30),
            end_time=time(9, 10),
            sort_order=1,
        )
        ClassTimetableEntry.objects.create(
            academic_year=self.year,
            class_section=self.section,
            routine_slot=self.slot_one,
            subject=self.subject,
            teacher=self.teacher,
        )
        ClassTimetableEntry.objects.create(
            academic_year=self.year,
            class_section=self.section,
            routine_slot=self.slot_two,
            subject=self.subject,
            teacher=self.teacher,
        )
        ClassTimetableEntry.objects.create(
            academic_year=self.year,
            class_section=self.section,
            routine_slot=self.saturday_slot,
            subject=self.subject,
            teacher=self.teacher,
        )
        self.day = date(2026, 8, 28)
        self.saturday = date(2026, 8, 29)
        self.calendar = SchoolCalendarDay.objects.create(
            date=self.day,
            academic_year=self.year,
            routine=self.routine,
        )
        self.saturday_calendar = SchoolCalendarDay.objects.create(
            date=self.saturday,
            academic_year=self.year,
            routine=self.saturday_routine,
        )
        self.student = Student.objects.create(
            admission_number="GEN1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )

    def test_routine_slots_and_calendar_are_configurable(self):
        self.assertEqual(self.routine.slots.count(), 3)
        self.assertEqual(self.calendar.routine, self.routine)
        self.assertEqual(self.saturday_calendar.routine, self.saturday_routine)

    def test_generate_date_creates_ordered_sessions_with_configured_times(self):
        result = generate_sessions_for_date(self.day)
        self.assertEqual(result.created, 2)
        sessions = list(
            ActivitySession.objects.filter(date=self.day).order_by("start_time", "name")
        )
        self.assertEqual(
            [session.name for session in sessions],
            ["Alpha block", "Beta block"],
        )
        self.assertEqual(sessions[0].start_time, time(9, 15))
        self.assertEqual(sessions[0].end_time, time(9, 55))
        self.assertEqual(sessions[0].activity_type, self.class_type)
        self.assertEqual(sessions[0].academic_year, self.year)
        self.assertEqual(sessions[0].class_section, self.section)
        self.assertEqual(sessions[0].routine_slot, self.slot_one)
        self.assertEqual(sessions[1].routine_slot.sort_order, 2)
        self.assertFalse(
            ActivitySession.objects.filter(name="Unused block").exists()
        )

    def test_different_dates_use_different_routines(self):
        generate_sessions_for_date(self.day)
        generate_sessions_for_date(self.saturday)
        weekday_names = set(
            ActivitySession.objects.filter(date=self.day).values_list("name", flat=True)
        )
        saturday_names = set(
            ActivitySession.objects.filter(date=self.saturday).values_list(
                "name", flat=True
            )
        )
        self.assertEqual(weekday_names, {"Alpha block", "Beta block"})
        self.assertEqual(saturday_names, {"Compact block"})

    def test_rerun_is_idempotent_and_leaves_attendance(self):
        generate_sessions_for_date(self.day)
        session = ActivitySession.objects.get(date=self.day, name="Alpha block")
        entry = AttendanceEntry.objects.create(
            activity_session=session,
            student=self.student,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        before_sessions = ActivitySession.objects.filter(date=self.day).count()
        second = generate_sessions_for_date(self.day)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.already_existed, 2)
        self.assertEqual(
            ActivitySession.objects.filter(date=self.day).count(),
            before_sessions,
        )
        self.assertEqual(ActivitySession.objects.get(pk=session.pk).pk, session.pk)
        self.assertEqual(AttendanceEntry.objects.get(pk=entry.pk).status, AttendanceStatus.PRESENT)
        self.assertEqual(AttendanceEntry.objects.count(), 1)

    def test_inactive_slots_and_missing_calendar(self):
        missing = generate_sessions_for_date(date(2026, 9, 1))
        self.assertIn("No calendar row", missing.errors[0])
        self.assertEqual(ActivitySession.objects.filter(date=date(2026, 9, 1)).count(), 0)
        generate_sessions_for_calendar_day(self.calendar)
        self.assertFalse(
            ActivitySession.objects.filter(routine_slot=self.inactive_slot).exists()
        )

    def test_academic_years_are_isolated(self):
        other_routine = Routine.objects.create(
            academic_year=self.other_year,
            name="Weekday",
        )
        other_slot = RoutineSlot.objects.create(
            routine=other_routine,
            activity_type=self.class_type,
            name="Next-year block",
            start_time=time(9, 15),
            end_time=time(9, 55),
            sort_order=1,
        )
        other_day = date(2027, 8, 28)
        SchoolCalendarDay.objects.create(
            date=other_day,
            academic_year=self.other_year,
            routine=other_routine,
        )
        TeachingAssignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            class_section=self.section,
            academic_year=self.other_year,
        )
        ClassTimetableEntry.objects.create(
            academic_year=self.other_year,
            class_section=self.section,
            routine_slot=other_slot,
            subject=self.subject,
            teacher=self.teacher,
        )
        generate_sessions_for_date(self.day)
        generate_sessions_for_date(other_day)
        this_year = ActivitySession.objects.filter(academic_year=self.year)
        next_year = ActivitySession.objects.filter(academic_year=self.other_year)
        self.assertTrue(this_year.exists())
        self.assertTrue(next_year.filter(name="Next-year block").exists())
        self.assertFalse(this_year.filter(name="Next-year block").exists())
        self.assertFalse(next_year.filter(date=self.day).exists())

    def test_class_targeting_and_date_range(self):
        generate_sessions_for_date(self.day)
        sessions = ActivitySession.objects.filter(date=self.day)
        self.assertEqual(
            set(sessions.values_list("class_section_id", flat=True)),
            {self.section.pk},
        )
        self.assertFalse(
            sessions.filter(class_section=self.other_section).exists()
        )
        self.assertEqual(
            list(students_for_session(sessions.get(name="Alpha block"))),
            [self.student],
        )
        later = date(2026, 8, 30)
        generate_sessions_for_date_range(self.day, later)
        self.assertEqual(
            ActivitySession.objects.filter(date=self.saturday).count(),
            1,
        )
        self.assertEqual(ActivitySession.objects.filter(date=later).count(), 0)
        inverted = generate_sessions_for_date_range(self.saturday, self.day)
        self.assertIn("start date must be on or before", inverted[0].errors[0])

    def test_house_and_school_generation_use_existing_duty_architecture(self):
        house = House.objects.create(name="Aravali")
        HouseMasterAssignment.objects.create(
            staff=self.staff,
            house=house,
            academic_year=self.year,
        )
        house_slot = RoutineSlot.objects.create(
            routine=self.routine,
            activity_type=self.house_type,
            name="House gathering",
            start_time=time(7, 0),
            end_time=time(7, 20),
            sort_order=0,
        )
        school_slot = RoutineSlot.objects.create(
            routine=self.routine,
            activity_type=self.school_type,
            name="Morning assembly",
            start_time=time(8, 0),
            end_time=time(8, 20),
            sort_order=4,
        )
        generate_sessions_for_date(self.day)
        self.assertTrue(
            ActivitySession.objects.filter(
                date=self.day,
                house=house,
                name="House gathering",
            ).exists()
        )
        self.assertFalse(
            ActivitySession.objects.filter(
                date=self.day,
                audience_kind=AudienceKind.SCHOOL,
            ).exists()
        )
        StaffDutyAssignment.objects.create(
            duty_type=DutyType.objects.create(
                name="Day officer",
                unique_per_day=True,
                is_active=True,
            ),
            staff=self.staff,
            date=self.day,
            academic_year=self.year,
        )
        generate_sessions_for_date(self.day)
        school = ActivitySession.objects.get(
            date=self.day,
            audience_kind=AudienceKind.SCHOOL,
        )
        self.assertEqual(school.responsible_staff, self.staff)
        self.assertEqual(school.routine_slot, school_slot)

    def test_admin_generate_action_does_not_duplicate(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin:school_schoolcalendarday_changelist")
        data = {
            "action": "generate_daily_sessions",
            "_selected_action": [str(self.calendar.pk)],
        }
        first = self.client.post(url, data, follow=True)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(ActivitySession.objects.filter(date=self.day).count(), 2)
        second = self.client.post(url, data, follow=True)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ActivitySession.objects.filter(date=self.day).count(), 2)


class ModDutyAndResponsibilityTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.other_year = AcademicYear.objects.create(
            name="2027-28",
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31),
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.house = House.objects.create(name="Aravali")
        self.teacher = User.objects.create_user(
            username="resp-teacher",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="resp-other",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.mod = User.objects.create_user(
            username="resp-mod",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.hm = User.objects.create_user(
            username="resp-hm",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.lonely = User.objects.create_user(
            username="resp-lonely",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.activity = ActivityType.objects.create(
            name="Supervised study",
            takes_attendance=True,
        )
        self.mod_duty = DutyType.objects.create(
            name="Master on Duty",
            unique_per_day=True,
            is_active=True,
        )
        self.gate_duty = DutyType.objects.create(
            name="Gate",
            unique_per_day=False,
            is_active=True,
        )
        self.day = date(2026, 8, 28)
        self.next_day = date(2026, 8, 29)
        self.session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Evening study",
            start_time=time(18, 0),
            end_time=time(19, 30),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.teacher,
        )
        self.next_session = ActivitySession.objects.create(
            date=self.next_day,
            academic_year=self.year,
            activity_type=self.activity,
            name="Evening study",
            start_time=time(18, 0),
            end_time=time(19, 30),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.teacher,
        )
        self.house_session = ActivitySession.objects.create(
            date=self.day,
            academic_year=self.year,
            activity_type=self.activity,
            name="House gathering",
            start_time=time(20, 0),
            end_time=time(20, 30),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.teacher,
        )
        HouseMasterAssignment.objects.create(
            staff=self.hm,
            house=self.house,
            academic_year=self.year,
        )
        self.student = Student.objects.create(
            admission_number="RESP1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )

    def test_mod_assignment_is_date_specific(self):
        assignment = StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.mod,
            date=self.day,
            academic_year=self.year,
        )
        self.assertTrue(assignment.enforces_unique_per_day)
        self.assertEqual(get_unique_active_mod(self.day, self.year), self.mod)
        StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.mod,
            date=self.next_day,
            academic_year=self.year,
        )
        self.assertEqual(get_unique_active_mod(self.next_day, self.year), self.mod)
        self.assertIsNone(get_unique_active_mod(date(2026, 8, 30), self.year))

    def test_duplicate_mod_same_date_is_rejected(self):
        StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.mod,
            date=self.day,
            academic_year=self.year,
        )
        with self.assertRaises(ValidationError):
            StaffDutyAssignment.objects.create(
                duty_type=self.mod_duty,
                staff=self.other_staff,
                date=self.day,
                academic_year=self.year,
            )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffDutyAssignment.objects.bulk_create(
                    [
                        StaffDutyAssignment(
                            duty_type=self.mod_duty,
                            staff=self.other_staff,
                            date=self.day,
                            academic_year=self.year,
                            enforces_unique_per_day=True,
                        )
                    ]
                )
        self.assertEqual(
            StaffDutyAssignment.objects.filter(
                date=self.day, academic_year=self.year
            ).count(),
            1,
        )

    def test_non_unique_duties_can_share_a_date(self):
        StaffDutyAssignment.objects.create(
            duty_type=self.gate_duty,
            staff=self.teacher,
            date=self.day,
            academic_year=self.year,
        )
        StaffDutyAssignment.objects.create(
            duty_type=self.gate_duty,
            staff=self.other_staff,
            date=self.day,
            academic_year=self.year,
        )
        self.assertEqual(
            StaffDutyAssignment.objects.filter(
                duty_type=self.gate_duty,
                date=self.day,
            ).count(),
            2,
        )

    def test_years_are_isolated_and_admin_lists_mod(self):
        StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.mod,
            date=self.day,
            academic_year=self.year,
        )
        other_date = date(2027, 8, 28)
        StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.other_staff,
            date=other_date,
            academic_year=self.other_year,
        )
        self.assertEqual(get_unique_active_mod(self.day, self.year), self.mod)
        self.assertEqual(
            get_unique_active_mod(other_date, self.other_year),
            self.other_staff,
        )
        self.assertIsNone(get_unique_active_mod(self.day, self.other_year))
        self.client.force_login(
            User.objects.create_user(
                username="resp-admin",
                password="x",
                category=UserCategory.ADMINISTRATION,
                is_staff=True,
                is_superuser=True,
            )
        )
        listing = self.client.get(
            reverse("admin:school_staffdutyassignment_changelist"),
            {"date__gte": self.day.isoformat(), "date__lt": self.next_day.isoformat()},
        )
        self.assertContains(listing, "resp-mod")
        self.assertContains(listing, "Master on Duty")

    def test_responsible_staff_differs_from_attendance_taker(self):
        StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.mod,
            date=self.day,
            academic_year=self.year,
        )
        self.assertEqual(self.session.responsible_staff, self.teacher)
        self.assertTrue(can_take_attendance(self.mod, self.session))
        self.client.force_login(self.mod)
        url = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[self.session.pk],
        )
        response = self.client.post(
            url,
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = AttendanceEntry.objects.get()
        self.assertEqual(entry.taken_by, self.mod)
        self.assertEqual(entry.activity_session.responsible_staff, self.teacher)
        self.assertNotEqual(entry.taken_by, entry.activity_session.responsible_staff)

    def test_mod_authority_does_not_spill_to_other_dates_or_lonely_staff(self):
        StaffDutyAssignment.objects.create(
            duty_type=self.mod_duty,
            staff=self.mod,
            date=self.day,
            academic_year=self.year,
        )
        self.assertTrue(can_take_attendance(self.teacher, self.session))
        self.assertTrue(can_take_attendance(self.mod, self.session))
        self.assertFalse(can_take_attendance(self.mod, self.next_session))
        self.assertFalse(can_take_attendance(self.lonely, self.session))
        self.assertTrue(can_take_attendance(self.hm, self.house_session))
        self.assertFalse(can_take_attendance(self.hm, self.session))
        HouseMasterAssignment.objects.create(
            staff=self.other_staff,
            house=self.house,
            academic_year=self.year,
            role=HouseStaffRole.ASSISTANT_HOUSE_TEACHER,
        )
        self.assertTrue(can_take_attendance(self.other_staff, self.house_session))
        self.assertEqual(self.house_session.responsible_staff, self.teacher)


class HouseStaffAssignmentTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.house = House.objects.create(name="Aravali")
        self.teacher = User.objects.create_user(
            username="houseteacher",
            password="x",
            category=UserCategory.STAFF,
        )
        self.assistant = User.objects.create_user(
            username="assistant",
            password="x",
            category=UserCategory.STAFF,
        )
        self.admin_user = User.objects.create_user(
            username="admin1",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
        )

    def test_house_form_offers_teacher_and_assistant_roles(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin:school_house_change", args=[self.house.pk])
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("House Teacher", html)
        self.assertIn("Assistant House Teacher", html)

    def test_one_of_each_role_per_house_year(self):
        HouseMasterAssignment.objects.create(
            staff=self.teacher,
            house=self.house,
            academic_year=self.year,
            role=HouseStaffRole.HOUSE_TEACHER,
        )
        HouseMasterAssignment.objects.create(
            staff=self.assistant,
            house=self.house,
            academic_year=self.year,
            role=HouseStaffRole.ASSISTANT_HOUSE_TEACHER,
        )
        extra = User.objects.create_user(
            username="extra",
            password="x",
            category=UserCategory.STAFF,
        )
        with self.assertRaises(ValidationError):
            HouseMasterAssignment.objects.create(
                staff=extra,
                house=self.house,
                academic_year=self.year,
                role=HouseStaffRole.HOUSE_TEACHER,
            )

    def test_house_pages_and_attendance_totals(self):
        section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        student = Student.objects.create(
            admission_number="H1",
            roll_number=1,
            first_name="Hari",
            last_name="H",
            date_of_birth=date(2014, 1, 1),
            gender="male",
            class_section=section,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=student,
            house=self.house,
            academic_year=self.year,
        )
        activity = ActivityType.objects.create(
            name="House roll call",
            takes_attendance=True,
            default_audience_kind=AudienceKind.HOUSE,
        )
        day = date(2026, 8, 28)
        session = ActivitySession.objects.create(
            date=day,
            academic_year=self.year,
            activity_type=activity,
            name="House roll call",
            start_time=time(20, 0),
            end_time=time(20, 30),
            audience_kind=AudienceKind.HOUSE,
            house=self.house,
            responsible_staff=self.teacher,
        )
        self.client.force_login(self.admin_user)
        changelist = self.client.get(reverse("admin:school_house_changelist"))
        self.assertContains(changelist, self.house.name)
        self.assertContains(changelist, ">Houses</h2>")
        self.assertContains(changelist, 'aria-label="Search houses"')
        self.assertNotContains(changelist, 'value="Search"')
        overview = self.client.get(
            reverse("admin:school_house_house_overview", args=[self.house.pk])
        )
        overview_html = overview.content.decode()
        year_row = overview_html.find('class="jnv-year-row"')
        self.assertNotEqual(year_row, -1)
        nav = overview_html.find('class="jnv-section-nav"')
        self.assertNotEqual(nav, -1)
        self.assertLess(year_row, nav)
        year_html = overview_html[year_row : year_row + 900]
        self.assertIn(self.house.name, year_html)
        self.assertIn("Academic year", year_html)
        self.assertLess(year_html.find(self.house.name), year_html.find("Academic year"))
        self.assertContains(overview, "House teacher")
        self.assertContains(overview, 'id="house-teacher-profile"')
        self.assertContains(overview, 'aria-label="View profile"')
        self.assertContains(overview, "Students")
        self.assertContains(overview, "Attendance")
        students = self.client.get(
            reverse("admin:school_house_house_students", args=[self.house.pk])
        )
        self.assertContains(students, student.full_name)
        attendance = self.client.get(
            reverse("admin:school_house_house_attendance", args=[self.house.pk]),
            {"academic_year": self.year.pk, "date": day.isoformat()},
        )
        self.assertEqual(attendance.status_code, 200)
        attendance_html = attendance.content.decode()
        year_row = attendance_html.find('class="jnv-year-row"')
        nav = attendance_html.find('class="jnv-section-nav"')
        datepicker = attendance_html.find("jnv-datepicker", nav)
        date_input = attendance_html.find('name="date"', nav)
        self.assertNotEqual(year_row, -1)
        self.assertNotEqual(nav, -1)
        self.assertLess(year_row, nav)
        self.assertNotEqual(datepicker, -1)
        self.assertNotEqual(date_input, -1)
        self.assertGreater(datepicker, nav)
        self.assertGreater(date_input, nav)
        self.assertNotIn(">Date<", attendance_html[nav:])
        self.assertNotIn(">Show<", attendance_html[nav:])
        self.assertNotContains(
            attendance,
            "For each period, mark all Present or mark all Absent",
        )
        self.assertContains(attendance, student.full_name)
        self.assertContains(attendance, "Mark all Present")
        self.assertContains(attendance, "Mark all Absent")
        self.assertContains(attendance, "Strength")
        self.assertContains(attendance, ">OD</th>")
        self.assertContains(attendance, ">Sick</th>")
        self.assertContains(attendance, ">Leave</th>")
        posted = self.client.post(
            reverse("admin:school_house_house_attendance", args=[self.house.pk]),
            {
                "academic_year": self.year.pk,
                "date": day.isoformat(),
                f"status_{session.pk}_{student.pk}": AttendanceStatus.SICK,
            },
        )
        self.assertEqual(posted.status_code, 302)
        entry = AttendanceEntry.objects.get(
            activity_session=session,
            student=student,
        )
        self.assertEqual(entry.status, AttendanceStatus.SICK)
        routine = self.client.get(
            reverse("admin:school_house_house_routine", args=[self.house.pk])
        )
        self.assertEqual(routine.status_code, 200)
        self.assertContains(routine, "Routine")



