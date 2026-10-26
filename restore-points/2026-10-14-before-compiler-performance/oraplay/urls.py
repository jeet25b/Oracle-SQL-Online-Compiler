from django.urls import path, re_path
from workbench import views

urlpatterns = [
    path("", views.workbench_home, name="home"),
    path("oraclesql/", views.workbench_home, name="workbench"),
    path("api/connect", views.connect_oracle),
    path("api/disconnect", views.disconnect_oracle),
    path("api/status", views.oracle_status),
    path("api/execute", views.execute_sql),
    path("api/objects", views.list_objects),
    re_path(r"^api/objects/(?P<owner>[^/]+)/(?P<name>[^/]+)$", views.object_details),
    path("api/files", views.sql_files),
    re_path(r"^api/files/(?P<name>[^/]+)$", views.sql_file),
    re_path(r"^(?P<asset>index\.html|app\.js|styles\.css|output\.css)$", views.frontend_asset),
]
