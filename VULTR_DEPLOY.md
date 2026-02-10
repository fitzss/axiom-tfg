# Deploying axiom-tfg on Vultr

## 1. Create a VM

1. Log in to [Vultr](https://my.vultr.com/).
2. **Deploy New Server** > **Cloud Compute (Shared CPU)**.
3. Pick a region close to your users.
4. OS: **Ubuntu 24.04 LTS**.
5. Plan: the cheapest plan (1 vCPU / 1 GB RAM) is sufficient.
6. Click **Deploy Now** and wait for the server to be ready.

## 2. SSH in

```bash
ssh root@<YOUR_VM_IP>
```

## 3. Install Docker + Compose

```bash
apt-get update && apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Verify:

```bash
docker compose version
```

## 4. Clone the repo and configure

```bash
git clone https://github.com/your-org/axiom-tfg.git
cd axiom-tfg
```

### Environment variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
nano .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | No | Gemini API key — enables the AI Assistant panel. Get one at https://aistudio.google.com/app/apikey |
| `AXIOM_PUBLIC_BASE_URL` | No | Public URL of your deployment (e.g. `http://<YOUR_VM_IP>:8000`). Makes `evidence_url` in API responses absolute. |

If you don't need AI features, you can skip creating `.env` entirely — the app works fine without it.

## 5. Start the app

```bash
docker compose up -d --build
```

Check the logs:

```bash
docker compose logs -f
```

## 6. Open the firewall

By default Vultr blocks most inbound ports. Open port 8000:

1. Go to your server in the Vultr dashboard.
2. **Settings** > **Firewall** — either add a rule for TCP port 8000, or manage it via a Firewall Group.
3. Alternatively, if using `ufw` on the VM:

```bash
ufw allow 8000/tcp
```

## 7. Verify

Open `http://<YOUR_VM_IP>:8000` in your browser. You should see the axiom-tfg web UI.

Test the API from your local machine:

```bash
curl -s http://<YOUR_VM_IP>:8000/health
# {"status":"ok"}

curl -s -X POST http://<YOUR_VM_IP>:8000/runs \
  -H "Content-Type: text/plain" \
  -d @examples/pick_place_can.yaml
```

## Optional: Nginx reverse proxy with a domain

If you want to serve on port 80/443 with a domain name:

```bash
apt-get install -y nginx

cat > /etc/nginx/sites-available/axiom <<'EOF'
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

ln -s /etc/nginx/sites-available/axiom /etc/nginx/sites-enabled/
nginx -t && systemctl restart nginx
ufw allow 80/tcp
```

Then point your DNS A record to the VM IP.

## Updating

```bash
cd axiom-tfg
git pull
docker compose up -d --build
```

## Data persistence

SQLite database and evidence JSON files are stored in the `axiom-data` Docker volume. They survive container rebuilds. To back up:

```bash
docker compose cp api:/app/data ./backup-data
```
