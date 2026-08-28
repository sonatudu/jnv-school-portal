from datetime import date, time

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import UserCategory

from .attendance_roster import (
    build_class_attendance_report,
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

