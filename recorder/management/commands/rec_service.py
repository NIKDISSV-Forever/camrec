import time
import subprocess
import shutil
import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

from recorder.models import Stream, System

GB_DIVIDER = 1 << 30
SEGMENT_FORMAT = settings.SEGMENT_FORMAT
logger = logging.getLogger(__name__)


def setup_logging(logfile_path: Path):
    """Настраивает глобальное логирование в консоль и в файл."""
    log_dir = logfile_path.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Определяем формат сообщений
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Очищаем предыдущие обработчики, чтобы избежать дублирования логов
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    # Обработчик для вывода в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Обработчик для записи в файл
    file_handler = logging.FileHandler(logfile_path, encoding='UTF-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info(f"Логирование включено. Вывод в консоль и в файл: {logfile_path}")


class StreamRecorder:
    __slots__ = ('stream', 'process', 'logfile')

    def __init__(self, stream: Stream):
        self.stream = stream
        self.process = self.logfile = None

    def start(self) -> bool:
        out_dir = self.stream.record_path
        out_dir.mkdir(parents=True, exist_ok=True)
        self.close()
        output_template = str(out_dir / f"%Y-%m-%d_%H-%M-%S.{SEGMENT_FORMAT}")
        self.logfile = (out_dir / 'ffmpeg.log').open('w', encoding='UTF-8')
        url = self.stream.full_url()
        if not shutil.which('ffmpeg'):
            logger.critical('FFmpeg не найден.')
            return False
        self.process = subprocess.Popen([
            "ffmpeg",
            "-hide_banner",
            "-loglevel", self.stream.loglevel,
            "-nostats",
            "-rtsp_transport", "tcp",
            "-i", url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(self.stream.segment_duration),
            "-reset_timestamps", "1",
            "-strftime", "1",
            output_template, '-y'
        ], stderr=self.logfile)
        logger.info(f"Запись запущена: {self.stream} -> {out_dir}")
        return True

    def close(self):
        """close logfile"""
        if self.logfile and not self.logfile.closed:
            self.logfile.close()
            self.logfile = None

    def stop(self):
        self.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
                logger.info(f"Запись остановлена: {self.stream}")
            except subprocess.TimeoutExpired:
                self.process.kill()
                logger.warning(
                    f"Процесс записи для {self.stream} не ответил и был принудительно завершен.")
            self.process = None

    def __del__(self):
        self.stop()


class Command(BaseCommand):
    help = "Запуск постоянной записи камер из модели Stream"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorders = []
        self._first_run = True
        self.system_settings = None
        self.records_dir = None
        self.stop_flag_file = None
        self.restart_flag_file = None

    def update_paths(self):
        """Получает актуальные настройки из БД и обновляет пути."""
        self.system_settings = System.get()
        self.records_dir = Path(self.system_settings.records_dir)
        self.stop_flag_file = self.records_dir / 'stop.flag'
        self.restart_flag_file = self.records_dir / 'restart.flag'
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def is_stopped(self):
        return self.stop_flag_file and self.stop_flag_file.exists()

    def restart(self):
        self.stop()
        started = 0
        logger.info(
            f"Запуск/перезапуск записей. "
            f"Директория: {self.records_dir}. "
            f"Мин. свободного места: {self.system_settings.min_free_gb} GB."
        )
        for stream in Stream.objects.all():
            self.recorders.append(r := StreamRecorder(stream))
            started += r.start()
        logger.info(f"Запущено {started} записей.")
        self.stop_flag_file.unlink(missing_ok=True)
        self.restart_flag_file.unlink(missing_ok=True)

    def stop(self):
        for recorder in self.recorders:
            recorder.stop()
        if self.recorders:
            logger.info("Все активные записи остановлены.")
        self.recorders.clear()

    def handle_dir_change_tasks(self):
        """Проверяет и выполняет задачи по перемещению/удалению директорий."""
        move_flag = self.records_dir / 'mv.flag'
        delete_flag = self.records_dir / 'rm.flag'
        if move_flag.exists():
            old_path_str = move_flag.read_text().strip()
            old_path = Path(old_path_str)
            if old_path.exists() and old_path.is_dir():
                logger.warning(
                    f"Обнаружена задача перемещения из '{old_path}' в '{self.records_dir}'. Останавливаю запись...")
                self.stop()
                try:
                    # Перемещаем содержимое
                    for item in old_path.iterdir():
                        dest_item = self.records_dir / item.name
                        logger.info(f"Перемещение: {item} -> {dest_item}")
                        shutil.move(str(item), str(dest_item))
                    logger.info(f"Перемещение из '{old_path}' успешно завершено.")
                    # Пытаемся удалить старую пустую папку
                    shutil.rmtree(old_path, ignore_errors=True)
                except Exception as e:
                    logger.error(f"Ошибка при перемещении файлов из '{old_path}': {e}", exc_info=True)
                move_flag.unlink()  # Удаляем флаг после выполнения
                # После перемещения нужен перезапуск, чтобы подхватить новые файлы
                self._first_run = True
            else:
                logger.warning(f"Старый путь '{old_path_str}' из move.flag не найден. Удаляю флаг.")
                move_flag.unlink()
        if delete_flag.exists():
            old_path_str = delete_flag.read_text().strip()
            old_path = Path(old_path_str)
            if old_path.exists() and old_path.is_dir():
                logger.warning(f"!!! ОБНАРУЖЕНА ЗАДАЧА УДАЛЕНИЯ ДАННЫХ В '{old_path}'. Останавливаю запись...")
                self.stop()
                for _ in range(2):
                    try:
                        shutil.rmtree(old_path)
                        logger.info(f"Директория '{old_path}' и все ее содержимое были успешно удалены.")
                    except PermissionError as e:
                        logger.warning(f"'{old_path}': {e}, пробуем немного позже.")
                        time.sleep(5)
                    except Exception as e:
                        logger.error(f"Ошибка при удалении директории '{old_path}': {e}", exc_info=True)
                delete_flag.unlink()
            else:
                logger.warning(f"Старый путь '{old_path_str}' из delete.flag не найден. Удаляю флаг.")
                delete_flag.unlink()

    def handle(self, *args, **options):
        setup_logging(settings.LOGFILE)
        logger.info("Запуск службы записи всех камер...")

        try:
            while True:
                # В начале каждой итерации получаем актуальные настройки
                self.update_paths()

                # Выполняем задачи по смене директории ДО всего остального
                self.handle_dir_change_tasks()

                self.cleanup_old_files()

                if self.is_stopped():
                    self.stop()

                if self._first_run or (self.restart_flag_file and self.restart_flag_file.exists()):
                    self.restart()
                    self._first_run = False

                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nПолучен сигнал прерывания (Ctrl+C). Завершение...")
            self.stop()

    def cleanup_old_files(self):
        min_free_gb = self.system_settings.min_free_gb
        if min_free_gb <= 0:
            return

        total, used, free = shutil.disk_usage(str(self.records_dir))
        free_gb = free / GB_DIVIDER
        if free_gb >= min_free_gb:
            return

        logger.warning(
            f"Недостаточно места ({free_gb:.2f} GB из необходимых {min_free_gb} GB). Начинаю удаление старых файлов...")

        try:
            files = sorted(self.records_dir.rglob(f"*.{SEGMENT_FORMAT}"), key=lambda p: p.stat().st_mtime)
        except FileNotFoundError:
            logger.warning(f"Каталог записей {self.records_dir} не найден. Пропускаю очистку.")
            return

        if files:
            self.stop()  # Останавливаем запись перед удалением, чтобы избежать проблем

        files_deleted = 0
        for file in files:
            try:
                file.unlink(missing_ok=True)
                files_deleted += 1
                free = shutil.disk_usage(str(self.records_dir)).free
                free_gb = free / GB_DIVIDER
                logger.info(f"Удалён: {file.name}. Свободно: {free_gb:.2f} GB.")
                if free_gb >= min_free_gb:
                    logger.info(f"Достигнут необходимый объем свободного места. Удалено файлов: {files_deleted}.")
                    break
            except Exception:
                logger.error(f"Не удалось удалить файл {file}", exc_info=True)
        else:
            if not files:
                logger.warning("Старых файлов для удаления не найдено, но места все еще недостаточно.")
            logger.warning("Места не достаточно, останавливаем запись, необходим ручной перезапуск/переконфигурация.")
            self.stop_flag_file.touch(exist_ok=True)
