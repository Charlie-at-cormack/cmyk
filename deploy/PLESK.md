# Server install on Plesk

CMYK runs as a systemd service on `127.0.0.1`, and the domain's nginx proxies to it. Plesk Git pulls each push to `main`, runs `install.sh`, and the service restarts on its own.

On a server the app needs two settings: `CMYK_ALLOWED_HOSTS` (the public host name) and `CMYK_PASSWORD`. The app refuses to start with the first and not the second, because uploaded PDFs are client artwork.

You need root SSH on the server, plus a domain or subdomain in Plesk (below: `cmyk.example.com`).

## 1. Prepare the domain in Plesk

1. **SSL/TLS Certificates**: issue a Let's Encrypt certificate and turn on *Redirect from HTTP to HTTPS*. The login is HTTP Basic auth, so it must only travel over HTTPS.
2. **Hosting & DNS > Hosting**: note the **system user** name. Set **SSH access** to `/bin/bash` (not chrooted), so the Git deploy action can run Python.

## 2. Python 3.11+ (root SSH)

```bash
python3 --version
```

If that prints 3.11 or newer, the Python is fine. Otherwise install one: `dnf install python3.12` (AlmaLinux/Rocky 9), or `apt install python3.12 python3.12-venv` (Ubuntu 24.04). On Debian/Ubuntu, the `-venv` package is needed in every case.

## 3. Connect the GitHub repo (Plesk > domain > Git)

1. **Add Repository** > *Remote Git hosting* > `git@github.com:Charlie-at-cormack/cmyk.git`.
2. Plesk shows an SSH public key. In GitHub, go to repo > **Settings > Deploy keys > Add deploy key** and paste it. Leave *Allow write access* off.
3. **Deployment mode**: Automatic. **Deployment path**: `/cmyk-app` (outside `httpdocs`, so the source is never web-visible).
4. Turn on **Enable additional deployment actions** and enter the following, replacing the home path and the Python command from step 2:
   ```bash
   cd /var/www/vhosts/example.com/cmyk-app && PYTHON=python3.12 bash install.sh && date > .deployed
   ```
5. Copy the repository's **Webhook URL** from its settings. In GitHub, go to repo > **Settings > Webhooks > Add webhook**, paste it, and set *Content type* to `application/json`, *Just the push event*.
6. Click **Deploy now** once. `cmyk-app/` should then contain the code and a `.venv`.

## 4. Service, login and data folder (root SSH)

Fill in the four values first. Check that the port is free with `ss -ltn | grep :8700`.

```bash
H=/var/www/vhosts/example.com   # subscription home (the folder that holds httpdocs)
U=sysuser                       # system user from step 1
P=8700                          # free local port
D=cmyk.example.com              # host name the app is served on (comma-separate several)

install -d -o "$U" -g psacln -m 700 "$H/cmyk-data"   # uploaded PDFs and renders live here

umask 077
cat > /etc/cmyk.env <<EOF
CMYK_ALLOWED_HOSTS=$D
CMYK_USER=cormack
CMYK_PASSWORD=$(openssl rand -base64 18)
CMYK_DATA_DIR=$H/cmyk-data
EOF
cat /etc/cmyk.env               # note the password

for f in cmyk.service cmyk-restart.path cmyk-restart.service; do
  sed -e "s|__HOME__|$H|g" -e "s|__SYSUSER__|$U|g" -e "s|__PORT__|$P|g" \
      "$H/cmyk-app/deploy/$f" > "/etc/systemd/system/$f"
done
systemctl daemon-reload
systemctl enable --now cmyk.service cmyk-restart.path

curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $D" "http://127.0.0.1:$P/"   # expect 401
```

## 5. nginx (Plesk > domain > Apache & nginx Settings)

1. Untick **Smart static files processing** and **Serve static files directly by nginx**. Otherwise nginx looks for `app.js`, `style.css` and the page previews in `httpdocs`, and they 404.
2. Paste `deploy/nginx.conf` into **Additional nginx directives**, with `__PORT__` replaced, then click **Apply**.

Open `https://cmyk.example.com`. The browser asks for a login: `cormack` plus the password from `/etc/cmyk.env`.

## Day to day

- **Update**: push to `main`. The webhook deploys, `install.sh` re-syncs `requirements.lock`, and the service restarts. A restart cancels any analysis in progress, but uploaded files are kept.
- **Logs**: `journalctl -u cmyk -n 100`. Plesk's deploy log is under the repository's settings.
- **Change the password**: edit `/etc/cmyk.env`, then run `systemctl restart cmyk`.
- **Keep one process**: job progress is held in memory, so don't add uvicorn workers.
- **Old jobs**: jobs older than 7 days are removed when the service starts, so on a server that happens at each deploy.
