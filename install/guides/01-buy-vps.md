# Guide: Buy a Hetzner VPS (manual console path)

This is the fallback for users who don't want to use the Hetzner API. If you're comfortable
with an API token, the AI wizard will do this automatically — see the "Path B" prompt during
install.

## Why Hetzner CX33?

Aerocrawl's production instance runs on a Hetzner **CX33**:
- 2 vCPU AMD
- 4 GB RAM
- 40 GB NVMe SSD
- 20 TB traffic
- ~€5.83/month

This exact spec is what Aerosend itself runs — it's battle-tested at real production traffic.
You can run Aerocrawl on a smaller box (2 GB RAM minimum) but Playwright headless Chromium
uses ~1–1.5 GB during scrapes, so 4 GB is the sweet spot.

## Steps

### 1. Sign up for Hetzner Cloud

Go to **https://console.hetzner.cloud** and create an account. Credit card required but
billing is pay-as-you-go.

### 2. Create a new Project

Click **+ New project** in the top left. Name it `aerocrawl`.

### 3. Add your SSH key

Go to **Security → SSH keys → Add SSH key**.

On your local machine, print your public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

If you don't have one, generate it:

```bash
ssh-keygen -t ed25519 -C "aerocrawl-$(date +%F)"
```

Paste the contents of `~/.ssh/id_ed25519.pub` into Hetzner. Name it anything (e.g., `laptop`).

### 4. Create a Server

Click **+ Add Server**.

- **Location:** Nuremberg (`fsn1`) if you're in EU/MEA; Ashburn (`ash`) if you're in the Americas;
  Hillsboro (`hil`) or Singapore (`sin`) for West Coast / APAC.
- **Image:** **Ubuntu 22.04**
- **Type:** **Shared vCPU** → **CX33** (AMD)
- **Networking:** IPv4 only is fine (saves $1/mo). Keep IPv6 too — free.
- **SSH keys:** select the key you just added.
- **Firewalls:** click **Create Firewall**, add rules:
  - TCP 22 from 0.0.0.0/0 (SSH — restrict later if you want)
  - TCP 80 from 0.0.0.0/0 (Caddy ACME HTTP challenge)
  - TCP 443 from 0.0.0.0/0 (HTTPS API)
- **Name:** `aerocrawl-1` (or anything).

Click **Create & Buy now**. The server takes ~30 seconds to boot.

### 5. Grab the public IPv4 address

Once the server is in the **Running** state, its IPv4 appears on the project dashboard.

Copy it. You'll paste it into the AI wizard (or use it directly in Phase 2's SSH command).

### 6. Test SSH connectivity

```bash
ssh root@<ip>
```

You should get a root prompt. Type `exit` to disconnect. You're ready for Phase 2.

---

**Next:** return to the install wizard, or see `install/guides/02-dns-setup.md` if you want
a custom domain. Otherwise Aerocrawl will use a free `sslip.io` subdomain derived from your IP.
