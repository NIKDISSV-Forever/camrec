import datetime
import os
from pathlib import Path
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django_cryptography.fields import encrypt


def trigger_restart():
    system_settings = System.get()
    records_dir = Path(system_settings.records_dir)
    (records_dir / 'restart.flag').touch(exist_ok=True)


class System(models.Model):
    ACTION_CHOICES = [
        ('mv', 'Переместить все файлы в новую директорию'),
        ('rm', 'Удалить все файлы из старой директории'),
    ]
    min_free_gb = models.PositiveSmallIntegerField(
        default=50,
        verbose_name="Минимум свободного места (GB)"
    )
    storage_pool_name = models.CharField(
        max_length=255,
        default="/dev/md0" if os.name == 'posix' else "Storage Pool",
        verbose_name="Имя RAID массива / Пула носителей",
        help_text="Для Linux: /dev/md0. Для Windows: имя пула носителей (например, 'Storage Pool')."
    )
    records_dir = models.CharField(
        max_length=255,
        default=str(settings.BASE_DIR / 'video'),
        verbose_name="Путь к директории записей",
        help_text="Абсолютный путь к папке, где будут храниться видеофайлы."
    )
    on_dir_change_action = models.CharField(
        max_length=2,
        choices=ACTION_CHOICES,
        default='mv',
        verbose_name="Действие при смене директории записей",
        help_text="Внимание: 'Переместить' или 'Удалить' может занять много времени и остановит запись на этот период."
    )

    class Meta:
        verbose_name = "Системная настройка"
        verbose_name_plural = "Системные настройки"

    def save(self, *args, **kwargs):
        # Получаем старый путь до сохранения
        old_instance = None
        if self.pk:
            try:
                old_instance = System.objects.get(pk=self.pk)
            except System.DoesNotExist:
                pass

        super().save(*args, **kwargs)  # Сначала сохраняем, чтобы pk был доступен

        new_records_dir = Path(self.records_dir)
        new_records_dir.mkdir(parents=True, exist_ok=True)
        if old_instance and old_instance.records_dir != self.records_dir:
            old_records_dir = Path(old_instance.records_dir)
            if self.on_dir_change_action == 'rm':
                delete_flag = new_records_dir / 'rm.flag'
                delete_flag.write_text(str(old_records_dir))
            else:
                move_flag = new_records_dir / 'mv.flag'
                move_flag.write_text(str(old_records_dir))
        trigger_restart()

    @classmethod
    def get(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    @staticmethod
    def get_absolute_url():
        return reverse('system-monitor')

    def __str__(self):
        return 'Основные настройки'


class Stream(models.Model):
    LOGLEVEL_CHOICES = [
        ("quiet", "Quiet"),
        ("panic", "Panic"),
        ("fatal", "Fatal"),
        ("error", "Error"),
        ("warning", "Warning"),
        ("info", "Info"),
        ("verbose", "Verbose"),
        ("debug", "Debug"),
        ("trace", "Trace"),
    ]
    host = models.CharField(max_length=255, verbose_name="Хост", help_text="IP-адрес или hostname")
    port = models.PositiveSmallIntegerField(default=554, verbose_name="Порт")
    login = models.CharField(max_length=64, default='admin', verbose_name="Логин")
    password = encrypt(models.CharField(max_length=128, verbose_name="Пароль"))
    protocol = models.CharField(max_length=16, default='rtsp', verbose_name="Протокол")
    path = models.CharField(max_length=256, blank=True, default='', verbose_name="Путь", help_text='/')
    segment_duration = models.PositiveIntegerField(
        default=3600,
        verbose_name="Длительность сегмента (сек)",
        help_text='Длительность одного видео-файла в секундах'
    )
    loglevel = models.CharField(
        max_length=7,
        choices=LOGLEVEL_CHOICES,
        default="info",
        verbose_name="Уровень логирования"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        unique_together = ('protocol', 'host', 'port', 'path')
        verbose_name = "Поток камеры"
        verbose_name_plural = "Потоки камер"

    @property
    def record_path(self) -> Path:
        """Получает путь для записи этого потока из настроек в БД."""
        if not self.pk:
            raise ValueError("Stream instance must be saved before accessing record_path.")
        records_dir = Path(System.get().records_dir)
        return records_dir / str(self.pk)

    def __str__(self):
        return f'{self.protocol}://{self.login}@{self.host}:{self.port}{self.path}'

    def full_url(self):
        return f'{self.protocol}://{self.login}:{self.password}@{self.host}:{self.port}{self.path}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.record_path.mkdir(parents=True, exist_ok=True)
        trigger_restart()

    def get_absolute_url(self):
        return reverse('stream-archive', kwargs={'pk': self.pk})

    def find_files_in_range(self, start_dt, end_dt):
        if not self.record_path.exists():
            return []
        files_in_range = []
        for f in self.record_path.glob(f'*.{settings.SEGMENT_FORMAT}'):
            try:
                timestamp_str = f.stem
                file_dt = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d_%H-%M-%S')
                file_dt_aware = timezone.make_aware(file_dt, timezone.get_current_timezone())
                file_end_dt = file_dt_aware + datetime.timedelta(seconds=self.segment_duration)
                if file_dt_aware < end_dt and file_end_dt > start_dt:
                    files_in_range.append(f)
            except ValueError:
                continue
        return sorted(files_in_range, key=lambda p: p.name)
