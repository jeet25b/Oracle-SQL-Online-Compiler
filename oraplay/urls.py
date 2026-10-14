from django.contrib import admin
from django.urls import path, re_path
from django.views.generic.base import RedirectView
from workbench import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("portal/login/", views.portal_login, name="portal-login"),
    path("students/login/", views.student_login, name="student-login"),
    path("students/register/", views.student_register, name="student-register"),
    path("students/registration-pending/", views.student_registration_pending, name="student-registration-pending"),
    path("portal/logout/", views.portal_logout, name="portal-logout"),
    path("portal/dashboard/", views.portal_dashboard, name="portal-dashboard"),
    path("portal/admin/", views.portal_admin_dashboard, name="portal-admin-dashboard"),
    path("portal/accounts/", views.portal_accounts, name="portal-accounts"),
    path("portal/teacher/students/", views.portal_teacher_students, name="portal-teacher-students"),
    path("api/activity", views.record_activity),
    path("", views.student_login, name="home"),
    path("oraclesql/", views.workbench_home, name="workbench"),
    path("lms.html", views.learning_portal, name="learning-portal"),
    path("api/connect", views.connect_oracle),
    path("api/disconnect", views.disconnect_oracle),
    path("api/status", views.oracle_status),
    path("api/execute", views.execute_sql),
    path("api/objects", views.list_objects),
    re_path(r"^api/objects/(?P<owner>[^/]+)/(?P<name>[^/]+)$", views.object_details),
    path("api/files", views.sql_files),
    re_path(r"^api/files/(?P<name>[^/]+)$", views.sql_file),
    re_path(r"^(?P<asset>index\.html|app\.js|styles\.css|output\.css|lms\.css)$", views.frontend_asset),
]
