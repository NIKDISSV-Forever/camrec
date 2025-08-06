from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.contrib.sessions.models import Session
from django.contrib.admin.models import LogEntry
from django.http import HttpResponseRedirect
from django.urls import reverse

from .forms import StreamActionForm
from .models import Stream, System


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('session_key', 'expire_date')
    search_fields = ('session_key',)
    readonly_fields = ('session_key', 'session_data', 'expire_date')


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ('action_time', 'user', 'content_type', 'object_repr', 'action_flag')
    list_filter = ('action_flag', 'content_type', 'user')


@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    def changelist_view(self, request, extra_context=None):
        system_obj = System.get()
        change_url = reverse(
            'admin:recorder_system_change',
            args=[system_obj.pk]
        )
        return HttpResponseRedirect(change_url)

    def has_add_permission(self, request):
        return not System.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Stream)
class StreamAdmin(ModelAdmin):
    list_display = ('__str__', 'segment_duration', 'loglevel', 'created_at')
    search_fields = ('host', 'login')
    actions = ('set_segment_duration', 'set_loglevel', 'delete_selected')
    list_filter = ('loglevel',)
    action_form = StreamActionForm

    fieldsets = (
        ('Основные настройки подключения', {
            'fields': ('protocol', 'host', 'port', 'path')
        }),
        ('Учетные данные для доступа', {
            'classes': ('collapse',),  # Делаем эту секцию сворачиваемой
            'fields': ('login', 'password'),
            'description': 'Логин и пароль для доступа к видеопотоку камеры.'
        }),
        ('Настройки записи и логирования', {
            'fields': ('segment_duration', 'loglevel')
        }),
    )

    @admin.action(description="Изменить длительность сегментов")
    def set_segment_duration(self, request, queryset):
        if value := request.POST.get('segment_duration'):
            try:
                value = int(value)
                updated = queryset.update(segment_duration=value)
                self.message_user(request, f"Обновлена длительность сегмента до {value} для {updated} потоков")
            except ValueError:
                self.message_user(request, "Некорректное число", level=messages.ERROR)
        else:
            self.message_user(request, "Введите длительность сегмента", level=messages.ERROR)

    @admin.action(description="Изменить уровень логирования")
    def set_loglevel(self, request, queryset):
        if value := request.POST.get('loglevel'):
            updated = queryset.update(loglevel=value)
            self.message_user(request, f"Установлен уровень логирования {value!r} для {updated} потоков")
        else:
            self.message_user(request, "Выберите уровень логирования", level=messages.ERROR)
