# Server install on Plesk

CMYK runs as a systemd service on `127.0.0.1`, and the domain's nginx proxies to it. Plesk Git pulls each push to `main`, runs `install.sh`, and the service restarts on its own.

Setting `CMYK_ALLOWED_HOSTS` (the public host name) turns on the login, because uploaded PDFs are client artwork.

- **First start:** the site shows **Create the admin login**. Enter the one-time setup code from the server log, plus your email and a password.
- **After that:** the site shows **Sign in**. Sessions last 7 days. After 5 wrong attempts from one address, sign-in pauses for 15 minutes.
- **Managing people:** admins use **Settings** in the sidebar to add and remove users, make users admins, and reset passwords. CMYK generates every new and reset password and shows it once. With email set up, it also emails it to the person.
- **Email (SMTP):** set up under **Settings > Email**, with a test button. The SMTP password is saved in `cmyk-data/auth/smtp.json`, readable only by the site user, and is never sent back to the browser. Some hosts block outgoing mail ports (587/465). If the test fails with a timeout, check the server firewall.

You need root SSH on the server, plus a domain or subdomain in Plesk (below: `cmyk.example.com`).

## 1. Prepare the domain in Plesk

1. **SSL/TLS Certificates**: issue a Let's Encrypt certificate and turn on *Redirect from HTTP to HTTPS*. The password and the session cookie must only travel over HTTPS.
2. **Hosting & DNS > Hosting**: note the **system user** name. Set **SSH access** to `/bin/bash` (not chrooted), so the Git deploy action can run Python.

## 2. Python 3.11+ (root SSH)

```bash
python3 --version
```

If that prints 3.11 or newer, use `python3`. Otherwise (Ubuntu 20.04 has 3.8, for example), install Python 3.12 server-wide with uv. This leaves the system Python alone:

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_BIN_DIR=/usr/local/bin uv python install 3.12
/usr/local/bin/python3.12 --version
```

Then use `PYTHON=/usr/local/bin/python3.12` below.

## 3. Connect the GitHub repo (Plesk > domain > Git)

1. **Add Repository** > *Remote Git hosting* > `git@github.com:Charlie-at-cormack/cmyk.git`.
2. Plesk shows an SSH public key. In GitHub, go to repo > **Settings > Deploy keys > Add deploy key** and paste it. Leave *Allow write access* off.
3. **Deployment mode**: Automatic. **Deployment path**: `cmyk-app`, with nothing in front. In File Manager it must appear as *Home directory > cmyk-app*, beside `httpdocs` and not inside it. Inside `httpdocs`, the source would be downloadable.
4. Turn on **Enable additional deployment actions** and enter the following, replacing the home path and the Python from step 2:
   ```bash
   cd /var/www/vhosts/example.com/cmyk-app && PYTHON=/usr/local/bin/python3.12 bash install.sh && date > .deployed
   ```
5. Copy the repository's **Webhook URL** from its settings. In GitHub, go to repo > **Settings > Webhooks > Add webhook**, paste it, and set *Content type* to `application/json`, *Just the push event*.
6. Click **Deploy now** once. `cmyk-app/` should then contain the code and a `.venv`.

## 4. Service and data folder (root SSH)

Fill in the four values first. Check that the port is free with `ss -ltn | grep :8700`.

```bash
H=/var/www/vhosts/example.com   # subscription home (the folder that holds httpdocs)
U=sysuser                       # system user from step 1
P=8700                          # free local port
D=cmyk.example.com              # host name the app is served on (comma-separate several)

install -d -o "$U" -g psacln -m 700 "$H/cmyk-data"   # PDFs, renders and the login live here

umask 077
cat > /etc/cmyk.env <<EOF
CMYK_ALLOWED_HOSTS=$D
CMYK_DATA_DIR=$H/cmyk-data
EOF

for f in cmyk.service cmyk-restart.path cmyk-restart.service; do
  sed -e "s|__HOME__|$H|g" -e "s|__SYSUSER__|$U|g" -e "s|__PORT__|$P|g" \
      "$H/cmyk-app/deploy/$f" > "/etc/systemd/system/$f"
done
systemctl daemon-reload
systemctl enable --now cmyk.service cmyk-restart.path

sleep 5   # the app needs a few seconds to load
curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $D" "http://127.0.0.1:$P/"   # expect 303 (to the login page)
journalctl -u cmyk -n 20 --no-pager | grep 'setup code'                       # the one-time code
```

## 5. nginx (Plesk > domain > Apache & nginx Settings)

1. Untick **Smart static files processing** and **Serve static files directly by nginx**. Otherwise nginx looks for `app.js`, `style.css` and the page previews in `httpdocs`, and they 404.
2. Paste `deploy/nginx.conf` into **Additional nginx directives**, the large box at the very bottom of the page. Replace `__PORT__`, then click **Apply**. Don't use the static-file extensions field or the Apache boxes.

Open `https://cmyk.example.com`. The site shows **Create the admin login**. Enter the setup code, either from the `journalctl` line above or from `cat $H/cmyk-data/auth/setup-code.txt`, then your email and a password of at least 10 characters. You're signed in straight away. The code stops working once it has been used.

## Day to day

- **Update**: push to `main`. The webhook deploys, `install.sh` re-syncs `requirements.lock`, and the service restarts. A restart cancels any analysis in progress, but uploaded files are kept.
- **Logs**: `journalctl -u cmyk -n 100`. Plesk's deploy log is under the repository's settings.
- **Forgotten password**: another admin resets it in **Settings > Users**. Everyone can change their own password under **Settings > Your account**.
- **Locked out (no admin can sign in)**: `rm $H/cmyk-data/auth/users.json && systemctl restart cmyk`. This **removes every login** and brings back **Create the admin login** with a new setup code (in `journalctl -u cmyk`).
- **Keep one process**: job progress is held in memory, so don't add uvicorn workers.
- **Old jobs**: jobs older than 7 days are removed when the service starts, so on a server that happens at each deploy.
