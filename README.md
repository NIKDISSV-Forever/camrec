# Установка:

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv smartmontools ffmpeg mdadm
curl -sSL https://install.python-poetry.org | python3 -
poetry install --no-root
poetry env activate
source .venv/bin/activate
```
Также установите caddy

Возможно понадобится
> sudo ln -s /usr/bin/python3 /usr/bin/python

# Сервис записи:

### Пример конфигурации:

```ini
# /etc/systemd/system/camrec.service
[Unit]
Description = CamRec Service (manage.py rec_service)
After = network-online.target

[Service]
Type = simple
WorkingDirectory = /home/ppa/camrec
ExecStart = /home/ppa/camrec/.venv/bin/python3 manage.py rec_service
Restart = always
RestartSec = 5
User = root
Group = root

[Install]
WantedBy = multi-user.target
```

```ini
# /etc/systemd/system/wwwcamrec.service
[Unit]
Description = CamRec Service (hypercorn)
After = network-online.target
Requires = dev-sda.device

[Service]
Type = simple
WorkingDirectory = /home/ppa/camrec
ExecStart = /home/ppa/camrec/.venv/bin/hypercorn camrec.asgi:application --bind 0.0.0.0:8000 --certfile .certs/cert.pem --keyfile .certs/key.pem --quic-bind 0.0.0.0:8001
Restart = always
RestartSec = 5
User = root
Group = root

[Install]
WantedBy = multi-user.target
```
### Команды управления:

```bash
sudo systemctl daemon-reload
sudo systemctl enable camrec
sudo systemctl start camrec
sudo systemctl status camrec
journalctl -u camrec -f
```

# Запуск

```bash
hypercorn camrec.asgi:application --bind 0.0.0.0:8000 --certfile .certs/cert.pem --keyfile .certs/key.pem --quic-bind 0.0.0.0:8001
```
