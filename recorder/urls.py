from django.urls import path
from . import views

urlpatterns = [
    path('', views.SystemMonitorView.as_view(), name='system-monitor'),
    path('storage-status/', views.storage_status_view, name='storage-status'),
    path('stream/<int:pk>/', views.StreamArchiveFormView.as_view(), name='stream-archive'),
    path('wipe-syslog/', views.wipe_log, name='wipe-syslog'),
    path('stream/<int:pk>/wipe-log/', views.wipe_log, name='wipe-ffmpeg-log'),
    path('storage/', views.manage_storage, name='manage-storage'),
    path('smart/', views.smart_status_view, name='smart-status'),
    path('stop/', views.stop_recording, name='stop'),
    path('restart/', views.restart_recording, name='restart'),
]
handler400 = handler403 = handler404 = handler500 = 'recorder.views.errors'
