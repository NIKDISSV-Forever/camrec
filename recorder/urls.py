from django.urls import path
from . import views

urlpatterns = [
    path('', views.SystemMonitorView.as_view(), name='system-monitor'),
    path('stream/<int:pk>/', views.StreamArchiveFormView.as_view(), name='stream-archive'),
    path('wipe-syslog/', views.wipe_log, name='wipe-syslog'),
    path('stream/<int:pk>/wipe-log/', views.wipe_log, name='wipe-ffmpeg-log'),
    path('raid/', views.manage_raid_disk, name='manage-raid'),
    path('smart/', views.smart_status_view, name='smart-status'),
    path('stop/', views.stop_recording, name='stop'),
    path('restart/', views.restart_recording, name='restart'),
]