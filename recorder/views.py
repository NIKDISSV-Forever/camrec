import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Generator, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.http import (HttpResponse, HttpResponseBadRequest, StreamingHttpResponse,
                         HttpResponseRedirect)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, FormView, View

from .forms import ArchivePeriodForm
from .models import Stream, System, trigger_restart

# --- Constants ---
GB_DIVIDER = 1 << 30
CHUNK_SIZE = 8192
DATETIME_WIDGET_FORMAT = "%Y-%m-%dT%H:%M"
SMARTCTL_SCAN_CMD = ["smartctl", "--scan"]
DRIVER_FALLBACKS = ("auto", "sat", "scsi", "ata", "nvme", "usbjmicron", "usbsunplus")


# --- Mixins and Permissions (без изменений) ---
def staff_member_required(user):
    return user.is_authenticated and user.is_staff


class StaffRequiredMixin(UserPassesTestMixin):
    login_url = 'admin:login'

    def test_func(self):
        return staff_member_required(self.request.user)


# --- Refactored Class-Based Views ---

class SystemMonitorView(StaffRequiredMixin, TemplateView):
    template_name = 'recorder/system_monitor.html'

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        system_settings = System.get()
        records_dir = Path(system_settings.records_dir)

        context.update({
            'title': 'Мониторинг системы',
            'system': system_settings,
            'log_file_path': settings.LOGFILE,
            'raid_status': self._get_raid_status(),
            'disks': self._list_possible_smart_devices(),
            'flag_status': self._get_flag_status(records_dir),
            'disk_usage': self._get_disk_usage(records_dir),
            'system_log_content': self._get_log_content(settings.LOGFILE)
        })
        return context

    @staticmethod
    def _get_raid_status() -> str:
        mdstat_path = Path("/proc/mdstat")
        if not mdstat_path.exists():
            return "Файл /proc/mdstat не найден. Управление RAID недоступно."
        try:
            return mdstat_path.read_text(encoding="UTF-8")
        except Exception as e:
            return f"Ошибка чтения /proc/mdstat: {e}"

    @staticmethod
    def _get_flag_status(records_dir: Path) -> Dict[str, bool]:
        """Проверяет наличие управляющих флагов."""
        flags = {
            'is_stopped': 'stop.flag',
            'is_restarting': 'restart.flag',
            'is_removing': 'rm.flag',
            'is_moving': 'mv.flag',
        }
        return {key: (records_dir / filename).exists() for key, filename in flags.items()}

    @staticmethod
    def _get_disk_usage(path: Path) -> Dict[str, Any]:
        """Возвращает информацию об использовании диска."""
        try:
            total, used, free = shutil.disk_usage(path)
            percent = (used / total) * 100 if total > 0 else 0
            return {
                'total': total / GB_DIVIDER,
                'used': used / GB_DIVIDER,
                'free': free / GB_DIVIDER,
                'percent': percent,
                'color': 'bg-danger' if percent > 90 else 'bg-warning' if percent > 75 else 'bg-success',
            }
        except FileNotFoundError:
            return {'error': f"Директория для записей '{path}' не найдена."}
        except Exception as e:
            return {'error': f"Ошибка при расчете места на диске: {e}"}

    @staticmethod
    def _get_log_content(log_path: Path, error_msg: str = "Лог-файл не найден.") -> str:
        """Читает содержимое лог-файла."""
        try:
            return mark_safe(log_path.read_text(encoding='UTF-8', errors='ignore'))
        except FileNotFoundError:
            return error_msg
        except Exception as e:
            return f"Ошибка чтения лог-файла: {e}"

    def _list_possible_smart_devices(self) -> List[Dict[str, Optional[str]]]:
        """Возвращает список устройств, поддерживающих SMART."""
        if not shutil.which("smartctl"):
            return []
        try:
            proc = subprocess.run(
                SMARTCTL_SCAN_CMD, capture_output=True, text=True, check=True,
                encoding="UTF-8", errors="ignore"
            )
            return self._parse_scan_output(proc.stdout)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

    @staticmethod
    def _parse_scan_output(scan_stdout: str) -> List[Dict[str, Optional[str]]]:
        """Разбирает вывод `smartctl --scan`."""
        devices = []
        for line in scan_stdout.splitlines():
            parts = line.split("#", 1)[0].strip().split()
            if not parts:
                continue
            device = parts[0]
            d_type = None
            if "-d" in parts:
                try:
                    d_type = parts[parts.index("-d") + 1]
                except (ValueError, IndexError):
                    pass
            devices.append({"device": device, "type": d_type})
        return devices


class StreamArchiveFormView(StaffRequiredMixin, FormView):
    """Отображает форму выбора периода и перенаправляет на скачивание."""
    template_name = 'recorder/archive_form.html'
    form_class = ArchivePeriodForm

    @staticmethod
    def _stream_content_generator(files: List[Path]) -> Generator[bytes, None, None]:
        for file_path in files:
            with file_path.open('rb') as f:
                while chunk := f.read(CHUNK_SIZE):
                    yield chunk

    def post(self, request, *args, **kwargs):
        stream = get_object_or_404(Stream, pk=self.kwargs['pk'])
        start_str = request.POST.get('start')
        end_str = request.POST.get('end')
        if not start_str or not end_str:
            return HttpResponseBadRequest("Поля 'start' и 'end' обязательны.")
        try:
            start_dt = timezone.make_aware(datetime.strptime(start_str, DATETIME_WIDGET_FORMAT))
            end_dt = timezone.make_aware(datetime.strptime(end_str, DATETIME_WIDGET_FORMAT))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Неверный формат даты/времени.")
        files_to_stream = stream.find_files_in_range(start_dt, end_dt)
        if not files_to_stream:
            return HttpResponse("За указанный период записи не найдены.", status=404,
                                content_type="text/plain; charset=utf-8")
        response = StreamingHttpResponse(self._stream_content_generator(files_to_stream), content_type='video/mp2t')
        filename = f"{stream.host}_{stream.login}_{start_dt:%Y%m%d-%H%M}_{end_dt:%Y%m%d-%H%M}.{settings.SEGMENT_FORMAT}"
        response['Content-Disposition'] = f'attachment;filename="{filename}"'
        return response

    def setup(self, request, *args, **kwargs):
        """Получаем объект stream до всех остальных методов."""
        super().setup(request, *args, **kwargs)
        self.stream = get_object_or_404(Stream, pk=self.kwargs['pk'])

    def get_initial(self) -> Dict[str, str]:
        """Устанавливает начальные значения для формы (вчера-завтра)."""
        now = timezone.localtime()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            'start': (today_start - timedelta(days=1)).strftime(DATETIME_WIDGET_FORMAT),
            'end': (today_start + timedelta(days=1)).strftime(DATETIME_WIDGET_FORMAT),
        }

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        ffmpeg_log_path = self.stream.record_path / 'ffmpeg.log'
        context.update({
            'stream': self.stream,
            'title': str(self.stream),
            'log_file_path': ffmpeg_log_path,
            'ffmpeg_log_content': SystemMonitorView._get_log_content(
                ffmpeg_log_path, "Лог-файл ffmpeg не найден."
            ),
            'segments': self.stream.record_path.glob(f'*.{settings.SEGMENT_FORMAT}'),
        })
        return context

    def form_valid(self, form) -> HttpResponseRedirect:
        """При успешной валидации формы, формирует URL и перенаправляет."""
        start_str = form.cleaned_data['start'].strftime(DATETIME_WIDGET_FORMAT)
        end_str = form.cleaned_data['end'].strftime(DATETIME_WIDGET_FORMAT)

        download_url = reverse('stream-archive', kwargs={'pk': self.stream.pk})
        return HttpResponseRedirect(f"{download_url}?start={start_str}&end={end_str}")


@require_POST
@user_passes_test(staff_member_required)
def wipe_log(request, pk: int = None):
    """Очищает системный лог или лог ffmpeg для потока."""
    if pk:
        log_path = get_object_or_404(Stream, pk=pk).record_path / 'ffmpeg.log'
        redirect_url = reverse('stream-archive', kwargs={'pk': pk})
    else:
        log_path = settings.LOGFILE
        redirect_url = reverse('system-monitor')
    log_path.open('wb').close()
    messages.success(request, f"Лог-файл '{log_path.name}' очищен.")
    return redirect(request.META.get('HTTP_REFERER', redirect_url))


@require_POST
@user_passes_test(staff_member_required)
def stop_recording(request):
    records_dir = Path(System.get().records_dir)
    stop_flag_file = records_dir / 'stop.flag'
    restart_flag_file = records_dir / 'restart.flag'
    restart_flag_file.unlink(missing_ok=True)
    stop_flag_file.touch(exist_ok=True)
    messages.warning(request, "Служба записи остановлена.")
    return redirect('system-monitor')


@require_POST
@user_passes_test(staff_member_required)
def restart_recording(request):
    trigger_restart()
    messages.info(request, "Запрошен перезапуск службы записи.")
    return redirect('system-monitor')


def _run_command(request, command_args: List[str], success_msg: str):
    """Helper to run shell commands and handle responses."""
    if not shutil.which(command_args[0]):
        messages.error(request, f"Утилита '{command_args[0]}' не найдена в системе.")
        return

    try:
        result = subprocess.run(
            command_args, capture_output=True, text=True, check=True,
            encoding="UTF-8", errors="ignore", timeout=15
        )
        messages.success(request, f"{success_msg}: {result.stdout.strip()}")
        if result.stderr:
            messages.info(request, f"Вывод stderr: {result.stderr.strip()}")
    except subprocess.CalledProcessError as e:
        error_message = f"Ошибка выполнения команды: {e.stderr or e.stdout or str(e)}"
        messages.error(request, error_message.strip())
    except subprocess.TimeoutExpired:
        messages.error(request, "Команда не завершилась вовремя (timeout).")
    except Exception as e:
        messages.error(request, f"Произошла непредвиденная ошибка: {e}")


def _get_disk_from_post(request) -> Optional[str]:
    """Safely gets a disk device path from a POST request."""
    try:
        index = int(request.POST.get("device", -1))
        # Используем `SystemMonitorView` для получения списка дисков
        all_disks = SystemMonitorView()._list_possible_smart_devices()
        if 0 <= index < len(all_disks):
            return all_disks[index]['device']
    except (ValueError, TypeError):
        pass
    messages.error(request, "Некорректный или отсутствующий индекс устройства.")
    return None


@require_POST
@user_passes_test(staff_member_required)
def manage_raid_disk(request):
    """Единое представление для управления дисками в RAID."""
    action = request.POST.get('action')
    disk_path = _get_disk_from_post(request)
    if not (disk_path and action):
        return redirect('system-monitor')
    raid_device = System.get().raid_device
    if not raid_device:
        messages.error(request, "RAID-устройство не настроено в системе.")
        return redirect('system-monitor')
    action_map = {
        'fail': ("--fail", f"Попытка отметить диск {disk_path} как сбойный..."),
        'remove': ("--remove", f"Попытка удалить диск {disk_path} из массива..."),
        'add': ("--add", f"Попытка добавить диск {disk_path} в массив..."),
    }
    if action not in action_map:
        messages.error(request, f"Неизвестное действие: {action}")
        return redirect('system-monitor')
    command, msg = action_map[action]
    messages.info(request, msg)
    if action == 'add':
        mdadm_args = ["mdadm", command, raid_device, disk_path]
    else:
        mdadm_args = ["mdadm", raid_device, command, disk_path]

    _run_command(request, mdadm_args, "Команда mdadm выполнена")
    return redirect('system-monitor')


@require_POST
@user_passes_test(staff_member_required)
def smart_status_view(request):
    """Возвращает текстовый отчет SMART для выбранного диска."""
    disk_path = _get_disk_from_post(request)
    if not disk_path:
        return HttpResponseBadRequest("Неверное устройство.")

    if not shutil.which("smartctl"):
        return HttpResponse("Утилита smartctl не найдена.", status=501)

    # Получаем рекомендованный тип драйвера
    all_disks = SystemMonitorView()._list_possible_smart_devices()
    entry = next((d for d in all_disks if d['device'] == disk_path), {})
    preferred_type = entry.get('type')

    drivers_to_try = ([preferred_type] if preferred_type else []) + list(DRIVER_FALLBACKS)
    report_lines = [f"SMART статус для {disk_path}\n"]

    for driver in set(drivers_to_try):  # `set` для уникальности
        cmd = ["smartctl", "-a", "-T", "permissive", "-d", driver, disk_path]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=10
            )
            # Проверяем, что вывод содержит ключевые фразы SMART
            if "SMART overall-health" in result.stdout or "SMART support is" in result.stdout:
                report_lines.append(f"\n--- Отчет с драйвером: {driver} ---\n")
                report_lines.append(result.stdout)
                if result.stderr:
                    report_lines.append(f"\n--- STDERR ---\n{result.stderr}")
                # Успешно, выходим из цикла
                return HttpResponse("".join(report_lines), content_type="text/plain; charset=utf-8")
        except Exception as e:
            report_lines.append(f"\nОшибка при запуске smartctl с драйвером {driver}: {e}\n")

    report_lines.append("\nНе удалось получить SMART-данные ни с одним из доступных драйверов.")
    return HttpResponse("".join(report_lines), content_type="text/plain; charset=utf-8")
