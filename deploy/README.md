# Deploying the bot (free: Google Cloud e2-micro)

Free tier, as checked on 12 Oct 2026 (secondary sources: confirm on cloud.google.com/free before you start):
- one e2-micro VM, only in **us-central1, us-west1 or us-east1**;
- 30 GB standard disk.

Any other region bills from the first hour. Set a **budget alert at $1** right after signing up.

1. Create a Google Cloud account; this needs a card for verification. Then create a project.
2. Compute Engine → Create instance:
   - region `us-central1`, machine type `e2-micro`;
   - Debian 12, 30 GB standard persistent disk;
   - no extra firewall rules: the bot only makes outgoing requests.
3. Press **SSH** in the console, then:
   ```bash
   curl -sO https://raw.githubusercontent.com/obodnazar/air-alerts-ua/main/deploy/setup.sh
   bash setup.sh https://github.com/obodnazar/air-alerts-ua.git
   ```
   This needs the repo to be public. While it is private, clone with a read-only deploy key instead.
4. Paste the token when asked. The input is hidden, and the token is stored in `/etc/shkilnyi-ranok.env` (root-only).
5. **Stop the copy running on your laptop:** two copies with one token fight over updates (Telegram returns 409).

**Operating it:**

| Task | Command |
|---|---|
| Logs | `journalctl -u shkilnyi-ranok -f` |
| Restart | `sudo systemctl restart shkilnyi-ranok` |
| Update the code | `sudo -u bot git -C /opt/shkilnyi-ranok/app pull && sudo systemctl restart shkilnyi-ranok` |
| Rotate the token | `/revoke` in @BotFather, edit `/etc/shkilnyi-ranok.env`, then restart |

GitHub Actions is not used for hosting: its terms forbid using Actions "as part of a serverless application" or for activity unrelated to building the project.
