# VPS Deployment For Two Apps

This project now has two web apps:

- Main app: `app.main:app`
- Mobile app: `stitch_leads_operations_platform.mobile_app:app`

Recommended public setup:

- main app on `https://ops.yourdomain.com`
- mobile app on `https://mobile.yourdomain.com`

Do not mount the mobile app under a subpath like `/mobile` unless you also rewrite its frontend routes, because it currently uses root-based paths such as `/api`, `/lead`, and `/login`.

## 1. Prepare environment

Install Python 3.11+ and create a virtual environment.

Example:

```bash
cd /opt/matcher
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` with at least:

```env
DATABASE_URL=postgresql://matcher_user:your_password@localhost:5432/matcher_db
APP_HOST=127.0.0.1
APP_PORT=8000
MATCHER_AUTH_USERNAME=https://www.sheltersrealty.co.in/
MATCHER_AUTH_PASSWORD=home@A1
MATCHER_SESSION_SECRET=replace-with-long-random-value
MOBILE_SESSION_SECRET=replace-with-long-random-value
MATCHER_INTERNAL_PROXY_TOKEN=replace-with-long-random-value
MOBILE_MATCHER_API_BASE=http://127.0.0.1:8000
```

## 2. Run locally on VPS first

Main app:

```bash
source /opt/matcher/.venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Mobile app:

```bash
source /opt/matcher/.venv/bin/activate
uvicorn stitch_leads_operations_platform.mobile_app:app --host 127.0.0.1 --port 8001
```

## 3. Create systemd services

Main app service: `/etc/systemd/system/matcher-main.service`

```ini
[Unit]
Description=Matcher Main App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/matcher
EnvironmentFile=/opt/matcher/.env
ExecStart=/opt/matcher/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Mobile app service: `/etc/systemd/system/matcher-mobile.service`

```ini
[Unit]
Description=Matcher Mobile App
After=network.target matcher-main.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/matcher
EnvironmentFile=/opt/matcher/.env
ExecStart=/opt/matcher/.venv/bin/uvicorn stitch_leads_operations_platform.mobile_app:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable matcher-main matcher-mobile
sudo systemctl start matcher-main matcher-mobile
sudo systemctl status matcher-main matcher-mobile
```

## 4. Nginx reverse proxy

Example main app vhost:

```nginx
server {
    server_name ops.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Example mobile app vhost:

```nginx
server {
    server_name mobile.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then add TLS with Certbot.

## 5. Refresh cron

The main app refresh endpoint stays on the main app:

```bash
0 */6 * * * curl -X POST http://127.0.0.1:8000/system/refresh
```

## 6. What to restart after changes

If backend code changes:

```bash
sudo systemctl restart matcher-main
```

If mobile app code changes:

```bash
sudo systemctl restart matcher-mobile
```

If shared env values change:

```bash
sudo systemctl restart matcher-main matcher-mobile
```

## 7. Important security notes

- Change the default password before public deployment.
- Use long random values for `MATCHER_SESSION_SECRET`, `MOBILE_SESSION_SECRET`, and `MATCHER_INTERNAL_PROXY_TOKEN`.
- Keep the Uvicorn apps bound to `127.0.0.1` and expose them only through Nginx.
