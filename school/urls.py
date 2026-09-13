from django.urls import path

from . import ops_views, portal

urlpatterns = [
    path("", portal.home, name="portal-home"),
    path("dashboard/", portal.dashboard, name="portal-dashboard"),
    path("staff/", portal.staff_dashboard, name="portal-staff"),
    path(
        "staff/sessions/<int:session_id>/attendance/",
        portal.staff_mark_attendance,
        name="portal-staff-mark",
    ),
    path("staff/exceptions/", portal.exception_register, name="portal-exceptions"),
    path("staff/outings/", portal.outing_board, name="portal-outings"),
    path("mess/", portal.mess_menu, name="portal-mess"),
    path("circulars/", portal.circular_list, name="portal-circulars"),
    path("life/", ops_views.life_hub, name="portal-life"),
    path("life/events/", ops_views.events_board, name="portal-events"),
    path("life/houses/", ops_views.house_points, name="portal-house-life"),
    path("life/library/", ops_views.library_board, name="portal-library"),
    path("life/exams/", ops_views.exams_board, name="portal-exams"),
    path("life/vmc/", ops_views.vmc_board, name="portal-vmc"),
    path("life/sick-bay/", ops_views.sick_bay_board, name="portal-sick-bay"),
    path("life/visitors/", ops_views.visitors_board, name="portal-visitors"),
    path("life/migration/", ops_views.migration_board, name="portal-migration"),
    path("life/vvn/", ops_views.vvn_board, name="portal-vvn"),
    path("parent/", portal.parent_dashboard, name="portal-parent"),
    path(
        "parent/students/<int:student_id>/attendance/",
        portal.parent_student_attendance,
        name="portal-parent-history",
    ),
]
